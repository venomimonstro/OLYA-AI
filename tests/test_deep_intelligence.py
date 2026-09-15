from __future__ import annotations

import unittest

from app.schemas.chat import ChatMessage
from app.services.deep_intelligence import DeepIntelligencePlan, deep_synthesis_messages, plan_context_message, planning_messages


class DeepIntelligenceTests(unittest.TestCase):
    def test_planner_builds_internal_brief_prompt(self) -> None:
        messages = planning_messages(
            "Сравни две архитектуры и предложи план масштабирования",
            [ChatMessage(role="system", content="WEB EVIDENCE: подтверждено A и B")],
        )
        self.assertEqual(messages[0].role, "system")
        self.assertIn("BRIEF", messages[0].content)
        self.assertIn("конкурирующие", messages[0].content)
        self.assertIn("WEB EVIDENCE", messages[1].content)

    def test_plan_context_is_explicitly_internal(self) -> None:
        message = plan_context_message(DeepIntelligencePlan(brief="Цель: выбрать архитектуру", max_tokens=900))
        self.assertEqual(message.role, "system")
        self.assertIn("не цитируй", message.content)
        self.assertIn("Цель: выбрать архитектуру", message.content)

    def test_synthesis_prompt_demands_tradeoffs_and_validation(self) -> None:
        messages = deep_synthesis_messages(
            "Как спроектировать отказоустойчивый сервис?",
            "Черновой ответ",
            [],
        )
        self.assertIn("риски/компромиссы", messages[0].content)
        self.assertIn("отказные сценарии", messages[0].content)
        self.assertIn("Черновой ответ", messages[1].content)


if __name__ == "__main__":
    unittest.main()
