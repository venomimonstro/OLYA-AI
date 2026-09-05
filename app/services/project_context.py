from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, Message, Project, ProjectMemory, Task, TaskCriterion, TaskEvidence
from app.schemas.chat import ChatMessage
from app.services.file_context import FileContextBuilder
from app.services.development import compact_project_development_context

class ProjectContextBuilder:
    """Build trusted project context with bounded recent history."""
    def __init__(self,max_history_messages:int=48,max_memories:int=50)->None:
        self.max_history_messages=max_history_messages; self.max_memories=max_memories; self.file_context=FileContextBuilder()
    def build(self,db:Session,*,project:Project|None,conversation:Conversation|None,task:Task|None=None,incoming:list[ChatMessage])->list[ChatMessage]:
        result=[]
        if project is not None:
            memories=list(db.scalars(select(ProjectMemory).where(ProjectMemory.project_id==project.id).order_by(ProjectMemory.updated_at.desc()).limit(self.max_memories)).all())
            trusted=["X1 trusted project context.",f"Project: {project.name}"]
            if project.instructions.strip(): trusted.append("Project instructions:\n"+project.instructions.strip())
            if memories: trusted.append("Confirmed project memory:\n"+"\n".join(f"- {m.key}: {m.value}" for m in reversed(memories)))
            development_context=compact_project_development_context(db,project.id)
            if development_context: trusted.append(development_context)
            if task is not None:
                criteria=list(db.scalars(select(TaskCriterion).where(TaskCriterion.task_id==task.id).order_by(TaskCriterion.ordinal)).all())
                lines=["Canonical task state:",f"Task title: {task.title}",f"Goal: {task.goal}",f"Status: {task.status}",f"State version: {task.state_version}"]
                if task.constraints: lines.append("Constraints:\n"+"\n".join(f"- {x}" for x in task.constraints))
                if task.current_step.strip(): lines.append("Current step: "+task.current_step.strip())
                if criteria: lines.append("Acceptance criteria:\n"+"\n".join(f"- [{'x' if x.satisfied else ' '}] {x.text} ({x.verification_method})" for x in criteria))
                verified=list(db.scalars(select(TaskEvidence).where(TaskEvidence.task_id==task.id,TaskEvidence.state=="verified").order_by(TaskEvidence.created_at.desc()).limit(20)).all())
                if verified: lines.append("Verified evidence:\n"+"\n".join(f"- {x.kind}: {x.summary}" for x in reversed(verified)))
                trusted.append("\n".join(lines))
            result.append(ChatMessage(role="system",content="\n\n".join(trusted)))
            query=next((m.content for m in reversed(incoming) if m.role=="user"),"")
            if query.strip():
                fc=self.file_context.build(db,project.id,query)
                if fc: result.append(ChatMessage(role="user",content=fc))
        if conversation is not None:
            stored=list(db.scalars(select(Message).where(Message.conversation_id==conversation.id).order_by(Message.created_at.desc()).limit(self.max_history_messages)).all())
            for item in reversed(stored):
                if item.role in {"user","assistant"}: result.append(ChatMessage(role=item.role,content=item.content))
        for msg in incoming:
            if result and result[-1].role==msg.role and result[-1].content==msg.content: continue
            result.append(msg)
        return result
