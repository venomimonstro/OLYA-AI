from __future__ import annotations

import unittest

from app.inference.router import choose_route
from app.services.response_completeness import needs_expansion


class QualityRoutingTests(unittest.TestCase):
    def test_atomic_fact_stays_fast(self) -> None:
        route = choose_route("Что такое DNS?", "auto", 4096, 8192)
        self.assertEqual(route.mode, "fast")
        self.assertFalse(route.reasoning)

    def test_normal_explanation_defaults_to_work(self) -> None:
        route = choose_route(
            "Объясни, как построить хорошую систему резервного копирования для небольшого проекта",
            "auto",
            4096,
            8192,
        )
        self.assertEqual(route.mode, "work")

    def test_complex_analysis_auto_escalates_to_deep(self) -> None:
        route = choose_route(
            "Проведи глубокий анализ архитектуры проекта, сравни варианты хранения данных, найди риски и предложи стратегию масштабирования",
            "auto",
            4096,
            8192,
        )
        self.assertEqual(route.mode, "deep")
        self.assertTrue(route.reasoning)

    def test_user_selected_fast_is_respected(self) -> None:
        route = choose_route(
            "Проведи глубокий анализ архитектуры проекта и сравни все варианты",
            "fast",
            4096,
            8192,
        )
        self.assertEqual(route.mode, "fast")

    def test_shallow_complex_answer_requires_repair(self) -> None:
        question = "Проанализируй архитектуру сервиса, сравни подходы и предложи лучший план масштабирования"
        answer = "Используйте кэш и масштабируйте сервер. Это улучшит производительность."
        self.assertTrue(needs_expansion(question, answer))

    def test_substantive_answer_can_pass(self) -> None:
        question = "Как лучше выбрать архитектуру кэша для API и какие есть риски?"
        answer = (
            "Для небольшого API я бы начал с Redis как общего кэша, потому что он отделяет состояние от экземпляров приложения и упрощает горизонтальное масштабирование. "
            "Критерий выбора — профиль данных: короткоживущие ответы удобно хранить по ключу запроса с TTL, а часто изменяемые сущности лучше инвалидировать по событию. "
            "Например, для каталога можно кэшировать карточку товара на 5 минут, но цену сбрасывать сразу после обновления в основной базе. "
            "Главный риск — устаревшие данные: если инвалидировать кэш только по времени, пользователь может увидеть старую цену или остаток. "
            "Ещё один компромисс — память: большой TTL повышает hit rate, однако увеличивает объём кэша и цену ошибки при устаревании. "
            "Поэтому сначала измерьте hit rate, latency и долю промахов на одном типе данных, а не кэшируйте всё подряд. "
            "Следующий шаг — выбрать один горячий endpoint, добавить Redis, метрики hit/miss и тест под нагрузкой; после этого расширять кэш только туда, где измеримый выигрыш оправдывает сложность. "
            "Итог: Redis подходит как базовый вариант, если вы заранее проектируете инвалидирование и наблюдаемость, а не используете кэш как универсальную заплатку."
        )
        self.assertFalse(needs_expansion(question, answer))


if __name__ == "__main__":
    unittest.main()
