from __future__ import annotations

import unittest

from app.services.research_planner import plan_research


class LocalBusinessResearchPlannerTests(unittest.TestCase):
    def test_generic_auto_preflight_does_not_block_specialized_chat_route(self):
        for query in (
            "лучший сервис москвы",
            "лучшие автосервисы в москве",
            "топ стоматологий Москвы",
            "автосервисы Москва",
        ):
            with self.subTest(query=query):
                plan = plan_research(query, intent="general")
                self.assertEqual(plan.freshness, "stable")

    def test_explicit_local_business_research_remains_current(self):
        plan = plan_research(
            "лучшие автосервисы",
            intent="local_business",
            location="Москва",
            category="автосервисы",
        )
        self.assertEqual(plan.intent, "local_business")
        self.assertEqual(plan.freshness, "current")


if __name__ == "__main__":
    unittest.main()
