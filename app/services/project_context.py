from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, Message, Project, ProjectMemory, Task, TaskCriterion, TaskEvidence
from app.schemas.chat import ChatMessage
from app.services.file_context import FileContextBuilder
from app.services.development import compact_project_development_context


class ProjectContextBuilder:
    """Build trusted project context with bounded recent history."""

    def __init__(self, max_history_messages: int = 48, max_memories: int = 50) -> None:
        self.max_history_messages = max_history_messages
        self.max_memories = max_memories
        self.file_context = FileContextBuilder()

    @staticmethod
    def _same(left: ChatMessage, right: ChatMessage) -> bool:
        return left.role == right.role and left.content == right.content

    def _new_client_turns(self, stored: list[ChatMessage], incoming: list[ChatMessage]) -> list[ChatMessage]:
        """Return only new user-authored turns for an existing server conversation.

        The server-side Message table is canonical. A client may submit its cached
        transcript for convenience, but cached assistant messages must not be able
        to rewrite the model's prior history.
        """
        if not incoming:
            return []
        best_end: int | None = None
        best_length = 0
        # Require an overlap of at least two turns. A single identical repeated
        # user message can be intentional and must keep its conversational meaning.
        for start in range(len(incoming)):
            max_length = min(len(stored), len(incoming) - start)
            for length in range(max_length, 1, -1):
                if length <= best_length:
                    break
                left = stored[-length:]
                right = incoming[start : start + length]
                if all(self._same(a, b) for a, b in zip(left, right)):
                    best_length = length
                    best_end = start + length
                    break

        if best_end is not None:
            tail = incoming[best_end:]
            return [message for message in tail if message.role == "user"]

        # No trustworthy transcript overlap was found. Only the newest user turn
        # is admitted; client-supplied assistant history cannot override stored
        # server history.
        last_user = next((message for message in reversed(incoming) if message.role == "user"), None)
        return [last_user] if last_user is not None else []

    def build(
        self,
        db: Session,
        *,
        project: Project | None,
        conversation: Conversation | None,
        task: Task | None = None,
        incoming: list[ChatMessage],
    ) -> list[ChatMessage]:
        result: list[ChatMessage] = []
        if project is not None:
            memories = list(
                db.scalars(
                    select(ProjectMemory)
                    .where(ProjectMemory.project_id == project.id)
                    .order_by(ProjectMemory.updated_at.desc())
                    .limit(self.max_memories)
                ).all()
            )
            trusted = ["X1 trusted project context.", f"Project: {project.name}"]
            if project.instructions.strip():
                trusted.append("Project instructions:\n" + project.instructions.strip())
            if memories:
                trusted.append(
                    "Confirmed project memory:\n"
                    + "\n".join(f"- {memory.key}: {memory.value}" for memory in reversed(memories))
                )
            development_context = compact_project_development_context(db, project.id)
            if development_context:
                trusted.append(development_context)
            if task is not None:
                criteria = list(
                    db.scalars(
                        select(TaskCriterion)
                        .where(TaskCriterion.task_id == task.id)
                        .order_by(TaskCriterion.ordinal)
                    ).all()
                )
                lines = [
                    "Canonical task state:",
                    f"Task title: {task.title}",
                    f"Goal: {task.goal}",
                    f"Status: {task.status}",
                    f"State version: {task.state_version}",
                ]
                if task.constraints:
                    lines.append("Constraints:\n" + "\n".join(f"- {item}" for item in task.constraints))
                if task.current_step.strip():
                    lines.append("Current step: " + task.current_step.strip())
                if criteria:
                    lines.append(
                        "Acceptance criteria:\n"
                        + "\n".join(
                            f"- [{'x' if criterion.satisfied else ' '}] {criterion.text} ({criterion.verification_method})"
                            for criterion in criteria
                        )
                    )
                verified = list(
                    db.scalars(
                        select(TaskEvidence)
                        .where(TaskEvidence.task_id == task.id, TaskEvidence.state == "verified")
                        .order_by(TaskEvidence.created_at.desc())
                        .limit(20)
                    ).all()
                )
                if verified:
                    lines.append(
                        "Verified evidence:\n"
                        + "\n".join(f"- {evidence.kind}: {evidence.summary}" for evidence in reversed(verified))
                    )
                trusted.append("\n".join(lines))
            result.append(ChatMessage(role="system", content="\n\n".join(trusted)))
            query = next((message.content for message in reversed(incoming) if message.role == "user"), "")
            if query.strip():
                file_context = self.file_context.build(db, project.id, query)
                if file_context:
                    result.append(ChatMessage(role="user", content=file_context))

        if conversation is not None:
            rows = list(
                db.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation.id)
                    .order_by(Message.created_at.desc())
                    .limit(self.max_history_messages)
                ).all()
            )
            stored = [
                ChatMessage(role=item.role, content=item.content)
                for item in reversed(rows)
                if item.role in {"user", "assistant"}
            ]
            result.extend(stored)
            result.extend(self._new_client_turns(stored, incoming))
        else:
            result.extend(incoming)
        return result
