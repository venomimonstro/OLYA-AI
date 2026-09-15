from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserScenario:
    id: str
    category: str
    prompt: str
    expected_path: str
    min_chars: int = 1
    max_chars: int = 12000
    must_include: tuple[str, ...] = ()
    expect_web: bool | None = None
    max_ttft_ms: int = 6000
    max_total_ms: int = 30000


def _s(index: int, category: str, prompt: str, path: str, *, min_chars: int = 1, max_chars: int = 12000,
       must_include: tuple[str, ...] = (), expect_web: bool | None = None,
       max_ttft_ms: int = 6000, max_total_ms: int = 30000) -> UserScenario:
    return UserScenario(
        id=f"U{index:03d}", category=category, prompt=prompt, expected_path=path,
        min_chars=min_chars, max_chars=max_chars, must_include=must_include,
        expect_web=expect_web, max_ttft_ms=max_ttft_ms, max_total_ms=max_total_ms,
    )


SCENARIOS: tuple[UserScenario, ...] = (
    # 1–10: near-instant exact utility requests.
    _s(1, "instant", "Привет!", "instant", max_chars=120, expect_web=False, max_ttft_ms=500, max_total_ms=800),
    _s(2, "instant", "Сколько будет 17 * 23? Ответь только числом.", "instant", must_include=("391",), max_chars=20, expect_web=False, max_ttft_ms=500, max_total_ms=800),
    _s(3, "instant", "Посчитай 15% от 200", "instant", must_include=("30",), max_chars=30, expect_web=False, max_ttft_ms=500, max_total_ms=800),
    _s(4, "instant", "Какое время в Токио сейчас?", "instant", min_chars=12, max_chars=80, expect_web=False, max_ttft_ms=500, max_total_ms=800),
    _s(5, "instant", "Который час в Москве?", "instant", min_chars=10, max_chars=70, expect_web=False, max_ttft_ms=500, max_total_ms=800),
    _s(6, "instant", "Какая сегодня дата в Москве?", "instant", min_chars=15, max_chars=100, expect_web=False, max_ttft_ms=500, max_total_ms=800),
    _s(7, "instant", "Сколько будет (120 + 30) / 5?", "instant", must_include=("30",), max_chars=30, expect_web=False, max_ttft_ms=500, max_total_ms=800),
    _s(8, "instant", "Вычисли 8^3", "instant", must_include=("512",), max_chars=30, expect_web=False, max_ttft_ms=500, max_total_ms=800),
    _s(9, "instant", "Здравствуйте", "instant", max_chars=120, expect_web=False, max_ttft_ms=500, max_total_ms=800),
    _s(10, "instant", "Кто тебя создал?", "instant", min_chars=10, max_chars=300, expect_web=False, max_ttft_ms=500, max_total_ms=800),

    # 11–20: live structured providers; no generic SERP needed.
    _s(11, "structured", "Какая сейчас погода в Москве?", "structured", min_chars=30, expect_web=True, max_ttft_ms=2500, max_total_ms=3500),
    _s(12, "structured", "Какая погода завтра в Казани?", "structured", min_chars=25, expect_web=True, max_ttft_ms=3000, max_total_ms=4000),
    _s(13, "structured", "Какой курс доллара к рублю сейчас?", "structured", min_chars=20, expect_web=True, max_ttft_ms=2500, max_total_ms=3500),
    _s(14, "structured", "Какая сейчас ключевая ставка Банка России?", "structured", min_chars=20, expect_web=True, max_ttft_ms=2500, max_total_ms=3500),
    _s(15, "structured", "Сколько сейчас стоит биткоин в рублях?", "structured", min_chars=30, expect_web=True, max_ttft_ms=3000, max_total_ms=4500),
    _s(16, "structured", "Сколько сейчас стоит Ethereum в долларах?", "structured", min_chars=30, expect_web=True, max_ttft_ms=3000, max_total_ms=4500),
    _s(17, "structured", "Который час в Рейкьявике?", "structured", min_chars=15, expect_web=True, max_ttft_ms=2500, max_total_ms=3500),
    _s(18, "structured", "Какая сейчас погода в Лиссабоне?", "structured", min_chars=30, expect_web=True, max_ttft_ms=3000, max_total_ms=4000),
    _s(19, "structured", "Курс евро к рублю сегодня", "structured", min_chars=20, expect_web=True, max_ttft_ms=2500, max_total_ms=3500),
    _s(20, "structured", "Какая температура сейчас в Сочи?", "structured", min_chars=25, expect_web=True, max_ttft_ms=3000, max_total_ms=4000),

    # 21–30: stable atomic knowledge; short real GigaChat pass, no search/history.
    _s(21, "atomic", "Кто написал роман «Мастер и Маргарита»?", "atomic", must_include=("булгаков",), max_chars=350, expect_web=False, max_ttft_ms=3000, max_total_ms=6500),
    _s(22, "atomic", "Какая столица Франции?", "atomic", must_include=("париж",), max_chars=250, expect_web=False, max_ttft_ms=3000, max_total_ms=5500),
    _s(23, "atomic", "Что такое HTTP?", "atomic", min_chars=60, max_chars=900, expect_web=False, max_ttft_ms=3000, max_total_ms=9000),
    _s(24, "atomic", "Кто такой Лев Толстой?", "atomic", min_chars=60, max_chars=900, expect_web=False, max_ttft_ms=3000, max_total_ms=9000),
    _s(25, "atomic", "Когда родился Альберт Эйнштейн?", "atomic", must_include=("1879",), max_chars=350, expect_web=False, max_ttft_ms=3000, max_total_ms=6500),
    _s(26, "atomic", "Где находится озеро Байкал?", "atomic", min_chars=30, max_chars=600, expect_web=False, max_ttft_ms=3000, max_total_ms=7500),
    _s(27, "atomic", "Какая столица Японии?", "atomic", must_include=("токио",), max_chars=250, expect_web=False, max_ttft_ms=3000, max_total_ms=5500),
    _s(28, "atomic", "Что означает конверсия в маркетинге?", "atomic", min_chars=80, max_chars=900, expect_web=False, max_ttft_ms=3000, max_total_ms=9000),
    _s(29, "atomic", "Кто написал «Преступление и наказание»?", "atomic", must_include=("достоев",), max_chars=350, expect_web=False, max_ttft_ms=3000, max_total_ms=6500),
    _s(30, "atomic", "Что такое SEO?", "atomic", min_chars=70, max_chars=900, expect_web=False, max_ttft_ms=3000, max_total_ms=9000),

    # 31–40: normal self-contained reasoning/explanation, no web required.
    _s(31, "direct", "Почему небо голубое? Объясни простыми словами.", "direct", min_chars=160, max_chars=1800, expect_web=False, max_ttft_ms=3500, max_total_ms=15000),
    _s(32, "direct", "Как сварить яйцо вкрутую?", "direct", min_chars=100, max_chars=1500, expect_web=False, max_ttft_ms=3500, max_total_ms=15000),
    _s(33, "direct", "Объясни фотосинтез ребёнку 10 лет.", "direct", min_chars=180, max_chars=1800, expect_web=False, max_ttft_ms=3500, max_total_ms=16000),
    _s(34, "direct", "Чем оперативная память отличается от SSD?", "direct", min_chars=180, max_chars=2200, expect_web=False, max_ttft_ms=3500, max_total_ms=18000),
    _s(35, "direct", "Как создать папку в Linux через терминал?", "direct", min_chars=80, max_chars=1300, expect_web=False, max_ttft_ms=3500, max_total_ms=14000),
    _s(36, "direct", "Дай простой рецепт блинов на молоке.", "direct", min_chars=200, max_chars=2200, expect_web=False, max_ttft_ms=3500, max_total_ms=18000),
    _s(37, "direct", "Как самолёт держится в воздухе?", "direct", min_chars=180, max_chars=2000, expect_web=False, max_ttft_ms=3500, max_total_ms=18000),
    _s(38, "direct", "С чего начать изучение Python с нуля?", "direct", min_chars=250, max_chars=2500, expect_web=False, max_ttft_ms=3500, max_total_ms=20000),
    _s(39, "direct", "Объясни блокчейн без сложных терминов.", "direct", min_chars=220, max_chars=2400, expect_web=False, max_ttft_ms=3500, max_total_ms=20000),
    _s(40, "direct", "Помоги организовать рабочий день, если постоянно отвлекаюсь.", "direct", min_chars=250, max_chars=2600, expect_web=False, max_ttft_ms=3500, max_total_ms=22000),

    # 41–50: practical knowledge synthesis where search materially improves quality.
    _s(41, "web_advice", "Что нужно знать, чтобы чаще побеждать в шахматах?", "web", min_chars=500, max_chars=4500, expect_web=True, max_ttft_ms=9000, max_total_ms=45000),
    _s(42, "web_advice", "Дай рекомендации, как эффективнее учить английские слова.", "web", min_chars=450, max_chars=4200, expect_web=True, max_ttft_ms=9000, max_total_ms=45000),
    _s(43, "web_advice", "Какие лучшие практики резервного копирования сайта?", "web", min_chars=450, max_chars=4500, expect_web=True, max_ttft_ms=9000, max_total_ms=45000),
    _s(44, "web_advice", "Что нужно знать, чтобы начать бегать и не бросить через неделю?", "web", min_chars=450, max_chars=4200, expect_web=True, max_ttft_ms=9000, max_total_ms=45000),
    _s(45, "web_advice", "Дай рекомендации по подготовке сайта к SEO-продвижению.", "web", min_chars=500, max_chars=4800, expect_web=True, max_ttft_ms=9000, max_total_ms=50000),
    _s(46, "web_advice", "Какие лучшие способы защитить WordPress от взлома?", "web", min_chars=500, max_chars=4800, expect_web=True, max_ttft_ms=9000, max_total_ms=50000),
    _s(47, "web_advice", "Что нужно знать перед покупкой первого 3D-принтера?", "web", min_chars=450, max_chars=4500, expect_web=True, max_ttft_ms=9000, max_total_ms=45000),
    _s(48, "web_advice", "Дай рекомендации, как выбрать CRM для небольшого бизнеса.", "web", min_chars=500, max_chars=4800, expect_web=True, max_ttft_ms=9000, max_total_ms=50000),
    _s(49, "web_advice", "Какие лучшие подходы к изучению шахматных дебютов?", "web", min_chars=450, max_chars=4200, expect_web=True, max_ttft_ms=9000, max_total_ms=45000),
    _s(50, "web_advice", "Что нужно знать перед самостоятельной сборкой домашнего сервера?", "web", min_chars=500, max_chars=4800, expect_web=True, max_ttft_ms=9000, max_total_ms=50000),

    # 51–60: freshness/release/shopping/schedule queries that must not use stale model memory.
    _s(51, "fresh_web", "Кто президент США?", "web", min_chars=30, max_chars=1600, expect_web=True, max_ttft_ms=7000, max_total_ms=25000),
    _s(52, "fresh_web", "Кто сейчас CEO OpenAI?", "web", min_chars=30, max_chars=1600, expect_web=True, max_ttft_ms=7000, max_total_ms=25000),
    _s(53, "fresh_web", "Какая версия Python сейчас актуальная?", "web", min_chars=30, max_chars=1800, expect_web=True, max_ttft_ms=7000, max_total_ms=25000),
    _s(54, "fresh_web", "Когда выйдет следующий iPhone?", "web", min_chars=60, max_chars=2200, expect_web=True, max_ttft_ms=7000, max_total_ms=30000),
    _s(55, "fresh_web", "Когда следующий матч Спартака?", "web", min_chars=40, max_chars=1800, expect_web=True, max_ttft_ms=7000, max_total_ms=25000),
    _s(56, "fresh_web", "Где сейчас купить PlayStation 5 в Москве?", "web", min_chars=80, max_chars=2600, expect_web=True, max_ttft_ms=7000, max_total_ms=30000),
    _s(57, "fresh_web", "Какие сейчас пробки в Москве?", "web", min_chars=40, max_chars=1800, expect_web=True, max_ttft_ms=7000, max_total_ms=25000),
    _s(58, "fresh_web", "Какие последние новости OpenAI сегодня?", "web", min_chars=180, max_chars=4200, expect_web=True, max_ttft_ms=8000, max_total_ms=40000),
    _s(59, "fresh_web", "Какая сейчас инфляция в России?", "web", min_chars=60, max_chars=2200, expect_web=True, max_ttft_ms=7000, max_total_ms=30000),
    _s(60, "fresh_web", "Когда выйдет Windows 12?", "web", min_chars=60, max_chars=2200, expect_web=True, max_ttft_ms=7000, max_total_ms=30000),

    # 61–70: writing/translation with all material in the current turn; history/search should not slow it down.
    _s(61, "writing", "Переведи на английский: Добрый день! Спасибо за быстрый ответ.", "direct", min_chars=15, max_chars=500, expect_web=False, max_ttft_ms=3500, max_total_ms=12000),
    _s(62, "writing", "Напиши короткое поздравление с днём рождения коллеге.", "direct", min_chars=80, max_chars=1200, expect_web=False, max_ttft_ms=3500, max_total_ms=14000),
    _s(63, "writing", "Перепиши профессиональнее: Мы всё сделали, но клиент пока ничего не прислал и поэтому дальше двигаться не можем.", "direct", min_chars=80, max_chars=1300, expect_web=False, max_ttft_ms=3500, max_total_ms=14000),
    _s(64, "writing", "Сделай лучше этот текст: Наш сервис помогает компаниям автоматизировать рутинные процессы, быстрее отвечать клиентам и не терять важные обращения из разных каналов.", "direct", min_chars=100, max_chars=1600, expect_web=False, max_ttft_ms=3500, max_total_ms=15000),
    _s(65, "writing", "Напиши описание карточки товара для беспроводной мыши, без выдуманных характеристик.", "direct", min_chars=180, max_chars=2200, expect_web=False, max_ttft_ms=3500, max_total_ms=18000),
    _s(66, "writing", "Напиши вежливое письмо клиенту с просьбой прислать доступы к сайту.", "direct", min_chars=160, max_chars=2200, expect_web=False, max_ttft_ms=3500, max_total_ms=18000),
    _s(67, "writing", "Напиши пост для соцсетей о запуске нового сайта компании.", "direct", min_chars=180, max_chars=2400, expect_web=False, max_ttft_ms=3500, max_total_ms=18000),
    _s(68, "writing", "Сократи до 2 предложений: Хороший маркетинг начинается с понимания экономики бизнеса, источников прибыли, поведения клиентов и реальной ценности продукта. Только после этого имеет смысл выбирать рекламные каналы и масштабировать бюджет.", "direct", min_chars=60, max_chars=800, expect_web=False, max_ttft_ms=3500, max_total_ms=12000),
    _s(69, "writing", "Придумай 10 заголовков для статьи про SEO интернет-магазина.", "direct", min_chars=150, max_chars=2200, expect_web=False, max_ttft_ms=3500, max_total_ms=18000),
    _s(70, "writing", "Исправь грамматику: Мы вчера созвонились с клиентом обсудили задачи и договорились что завтра пришлём план работ.", "direct", min_chars=70, max_chars=1200, expect_web=False, max_ttft_ms=3500, max_total_ms=14000),

    # 71–80: follow-up and memory intents; must not be stripped into independent fast mode.
    _s(71, "context", "Продолжи предыдущий ответ и добавь конкретные примеры.", "context", min_chars=100, expect_web=False, max_ttft_ms=5000, max_total_ms=25000),
    _s(72, "context", "А если сделать наоборот?", "context", min_chars=40, expect_web=False, max_ttft_ms=5000, max_total_ms=22000),
    _s(73, "context", "Сделай как в прошлый раз, только короче.", "context", min_chars=40, expect_web=False, max_ttft_ms=5000, max_total_ms=22000),
    _s(74, "memory", "Что ты помнишь обо мне?", "memory", min_chars=30, expect_web=False, max_ttft_ms=5000, max_total_ms=22000),
    _s(75, "memory", "Как я просила отвечать на сложные вопросы?", "memory", min_chars=30, expect_web=False, max_ttft_ms=5000, max_total_ms=22000),
    _s(76, "memory", "Что мы решили использовать для проекта?", "memory", min_chars=30, expect_web=False, max_ttft_ms=5000, max_total_ms=22000),
    _s(77, "context", "Исправь это, там ошибка в третьем пункте.", "context", min_chars=40, expect_web=False, max_ttft_ms=5000, max_total_ms=22000),
    _s(78, "context", "Продолжи статью с того места, где остановились.", "context", min_chars=120, expect_web=False, max_ttft_ms=5000, max_total_ms=30000),
    _s(79, "context", "Сделай тот же формат, но для другой услуги.", "context", min_chars=80, expect_web=False, max_ttft_ms=5000, max_total_ms=26000),
    _s(80, "memory", "Какие мои предпочтения по формату ответов ты помнишь?", "memory", min_chars=30, expect_web=False, max_ttft_ms=5000, max_total_ms=22000),

    # 81–90: explicit long-form requests; large output budget must be opt-in only.
    _s(81, "longform", "Напиши статью не менее 10000 символов про SEO-продвижение интернет-магазина.", "longform", min_chars=8000, max_chars=18000, expect_web=False, max_ttft_ms=6000, max_total_ms=360000),
    _s(82, "longform", "Напиши большую подробную статью про контент-маркетинг для малого бизнеса.", "longform", min_chars=5000, max_chars=16000, expect_web=False, max_ttft_ms=6000, max_total_ms=360000),
    _s(83, "longform", "Подготовь лонгрид о том, как выбрать CMS для интернет-магазина.", "longform", min_chars=5000, max_chars=16000, expect_web=False, max_ttft_ms=6000, max_total_ms=360000),
    _s(84, "longform", "Напиши развёрнутую статью не менее 8000 символов о Яндекс Директ для владельца бизнеса.", "longform", min_chars=7000, max_chars=17000, expect_web=False, max_ttft_ms=6000, max_total_ms=360000),
    _s(85, "longform", "Сделай long-form материал на 10000 символов про автоматизацию поддержки клиентов.", "longform", min_chars=8000, max_chars=18000, expect_web=False, max_ttft_ms=6000, max_total_ms=360000),
    _s(86, "longform", "Напиши подробную большую статью о защите сайта от взлома.", "longform", min_chars=5000, max_chars=16000, expect_web=False, max_ttft_ms=6000, max_total_ms=360000),
    _s(87, "longform", "Подготовь статью не менее 9000 знаков о повышении конверсии интернет-магазина.", "longform", min_chars=7500, max_chars=18000, expect_web=False, max_ttft_ms=6000, max_total_ms=360000),
    _s(88, "longform", "Напиши лонгрид про построение отдела маркетинга с нуля.", "longform", min_chars=5000, max_chars=16000, expect_web=False, max_ttft_ms=6000, max_total_ms=360000),
    _s(89, "longform", "Создай большую статью на 10000 символов о CRM для отдела продаж.", "longform", min_chars=8000, max_chars=18000, expect_web=False, max_ttft_ms=6000, max_total_ms=360000),
    _s(90, "longform", "Напиши развёрнутую статью не менее 8000 символов про работу с репутацией бренда.", "longform", min_chars=7000, max_chars=17000, expect_web=False, max_ttft_ms=6000, max_total_ms=360000),

    # 91–100: routing traps seen in real chats.
    _s(91, "routing_edge", "Что такое курс валют?", "atomic", min_chars=80, max_chars=1000, expect_web=False, max_ttft_ms=3000, max_total_ms=10000),
    _s(92, "routing_edge", "Как работает курс валют?", "direct", min_chars=120, max_chars=1800, expect_web=False, max_ttft_ms=3500, max_total_ms=16000),
    _s(93, "routing_edge", "Кто был первым президентом США?", "direct", must_include=("вашингтон",), max_chars=900, expect_web=False, max_ttft_ms=3500, max_total_ms=12000),
    _s(94, "routing_edge", "Какая версия Python?", "web", min_chars=30, max_chars=1600, expect_web=True, max_ttft_ms=7000, max_total_ms=25000),
    _s(95, "routing_edge", "Исправь этот текст: Вчера мы закончили настройку сайта и проверили основные формы, после чего команда подготовила список следующих задач для запуска рекламы.", "direct", min_chars=90, max_chars=1500, expect_web=False, max_ttft_ms=3500, max_total_ms=15000),
    _s(96, "routing_edge", "Сделай лучше этот текст: Компания помогает интернет-магазинам находить точки роста, улучшать рекламу, SEO и конверсию сайта на основе данных и экономики бизнеса.", "direct", min_chars=100, max_chars=1700, expect_web=False, max_ttft_ms=3500, max_total_ms=16000),
    _s(97, "routing_edge", "Где купить недорогой монитор 27 дюймов?", "web", min_chars=180, max_chars=3000, expect_web=True, max_ttft_ms=8000, max_total_ms=35000),
    _s(98, "routing_edge", "Посоветуй фильм на вечер без тяжёлой драмы.", "direct", min_chars=120, max_chars=2000, expect_web=False, max_ttft_ms=3500, max_total_ms=17000),
    _s(99, "routing_edge", "Когда умер Александр Пушкин?", "atomic", must_include=("1837",), max_chars=500, expect_web=False, max_ttft_ms=3000, max_total_ms=7000),
    _s(100, "routing_edge", "Когда выйдет новая версия Ubuntu?", "web", min_chars=60, max_chars=2200, expect_web=True, max_ttft_ms=7000, max_total_ms=30000),
)

assert len(SCENARIOS) == 100
assert len({case.id for case in SCENARIOS}) == 100
