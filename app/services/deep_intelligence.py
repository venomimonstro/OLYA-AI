from __future__ import annotations

from dataclasses import dataclass

from app.schemas.chat import ChatMessage


@dataclass(frozen=True)
class DeepIntelligencePlan:
    brief: str
    max_tokens: int


def planning_messages(question: str, evidence_messages: list[ChatMessage]) -> list[ChatMessage]:
    evidence = "\n\n".join(
        message.content for message in evidence_messages if message.role == "system"
    )[:9000]
    return [
        ChatMessage(
            role="system",
            content=(
                "Ты внутренний стратег OLYA AI. Не отвечай пользователю и не раскрывай скрытые рассуждения. "
                "Сформируй компактный рабочий план для другого генератора ответа. Верни только структурированный BRIEF без пошаговой цепочки мыслей. "
                "В BRIEF укажи: реальную цель пользователя; ключевые подзадачи; 2–4 конкурирующие гипотезы/варианта, которые нужно сопоставить; "
                "какие факты из EVIDENCE действительно важны; что нельзя утверждать без данных; главные риски/компромиссы; критерии хорошего ответа; рекомендуемую структуру финала. "
                "Для технической задачи добавь архитектурные ограничения и проверку результата. Для решения/стратегии — критерии выбора и сценарии. "
                "Будь плотным: обычно 250–700 слов. Не пиши финальный ответ пользователю."
            ),
        ),
        ChatMessage(
            role="user",
            content=(
                f"ЗАПРОС ПОЛЬЗОВАТЕЛЯ:\n{question[:5000]}"
                + (f"\n\nEVIDENCE / КОНТЕКСТ:\n{evidence}" if evidence else "")
            ),
        ),
    ]


def plan_context_message(plan: DeepIntelligencePlan) -> ChatMessage:
    return ChatMessage(
        role="system",
        content=(
            "DEEP INTELLIGENCE BRIEF. Это внутренний рабочий план, не цитируй и не упоминай его. "
            "Используй его для более глубокого ответа, но проверенные факты из WEB EVIDENCE/файлов имеют приоритет. "
            "Не раскрывай скрытые рассуждения; пользователю дай только итоговый ответ.\n\n" + plan.brief[:7000]
        ),
    )


def deep_synthesis_messages(question: str, draft: str, evidence_messages: list[ChatMessage]) -> list[ChatMessage]:
    evidence = "\n\n".join(
        message.content for message in evidence_messages if message.role == "system"
    )[:9000]
    return [
        ChatMessage(
            role="system",
            content=(
                "Ты финальный senior-редактор OLYA AI для сложных задач. Перед тобой черновик после основного reasoning-прохода. "
                "Верни только лучший готовый ответ пользователю. Не раскрывай скрытые рассуждения и внутренние планы. "
                "Проверь логические провалы, противоречия, неподтверждённые утверждения, игнорирование альтернатив и слабые практические выводы. "
                "Сохрани полезные факты и конкретику, но пересобери ответ целиком, если так он станет сильнее. "
                "Для анализа/решения: прямой вывод, аргументы, альтернативы, риски/компромиссы, конкретный следующий шаг. "
                "Для технической задачи: рабочая архитектура/алгоритм, почему он выбран, ограничения, отказные сценарии и проверка. "
                "Не добавляй факты, цифры и источники, которых нет в черновике или EVIDENCE."
            ),
        ),
        ChatMessage(
            role="user",
            content=(
                f"ЗАПРОС:\n{question[:5000]}\n\nЧЕРНОВИК:\n{draft[:9000]}"
                + (f"\n\nEVIDENCE:\n{evidence}" if evidence else "")
            ),
        ),
    ]
