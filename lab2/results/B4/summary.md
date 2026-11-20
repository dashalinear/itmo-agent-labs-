## Цель
Подготовить обзор подходов к хранению состояния LLM-агентов с цитатами-подтверждениями, сравнением ограничений и разделом «Непроверенное».

## Ограничения пользователя
- [W1] Прочитать полностью (web_fetch) не меньше 5 реально найденных страниц.
- [W2] Каждое утверждение обзора подтверждено цитатой со страницы (evidence с URL); snippet поисковика подтверждением не считается.
- [W3] Обзор не длиннее 900 слов.
- [W4] Отдельный раздел «Непроверенное»: что не удалось подтвердить и какие источники не прочитаны.
- [W5] Сравнить подходы по их ограничениям.
- [W6] Минимум два раунда: после чтения первых страниц уточнить запросы по прочитанному; брать страницы с разных сайтов, если они доступны.
- [W7] Остановиться, когда требования к обзору выполнены: читать всю очередь не нужно.

## Решения и договорённости
- Использовать навык `research_web`: 2 раунда поиска, выбор источников, чтение страниц, сбор evidence.
- План: Round 1 (поиск) → выбор 7 источников → чтение → Round 2 (уточнение) → написание отчёта.
- Критерии завершения: min_search_queries=3, min_web_pages_read=5, min_web_sources_cited=4, min_evidence=6, required_sections=["approaches", "limitations", "comparison", "unverified"], min_queries_after_reading=1.

## Подтверждённые факты (с источниками)
- LangGraph: checkpointer хранит состояние графа треда как чекпоинты (краткосрочная память), store — долговременную память между тредами. Источник: `https://docs.langchain.com/oss/python/langgraph/persistence` (art_d386c05025cc3edfd176, ev_13f48a2a4ef9).
- LangGraph: InMemorySaver хранит чекпоинты в RAM — при перезапуске процесса все чекпоинты теряются; для продакшена нужен persistent checkpointer (Postgres/SQLite). Источник: `https://docs.langchain.com/oss/python/langgraph/persistence` (art_d386c05025cc3edfd176, ev_8c73704164f8).
- AMV-L: большинство систем используют age-based retention (TTL), но TTL не ограничивает вычислительный footprint памяти на request path — retrieval candidate sets и vector similarity scans растут непредсказуемо, давая heavy-tailed latency. Источник: `https://arxiv.org/abs/2603.04443v1` (art_f4eaad0978f98478aa40, ev_6c1f47d51bae).
- AMV-L: для предсказуемой производительности long-running LLM-агентов требуется явное управление размером working-set памяти и value-driven lifecycle management, а не только retention time. Источник: `https://arxiv.org/abs/2603.04443v1` (art_f4eaad0978f98478aa40, ev_db086ab51e96).
- VikingMem: система управления базой памяти для stateful LLM-приложений; генерирует high-level summary memories, приоритизирует недавние, сжимает старые; превосходит baseline на 30% в retrieval effectiveness. Источник: `https://arxiv.org/abs/2605.29640v3` (art_0c16c3d04950b33dc0c1, ev_ed1f1ed1a38b, ev_4598fa1c7c10).
- REMIT: reference sequencer с Verus-verified recovery core для checkpoint/interrupt/resume семантик в workflow persistence layers. Источник: `https://arxiv.org/abs/2608.03836v3` (art_2c2bba1f59b11d3bbf0c, ev_5a91846e5d7f, ev_85e20e46b858).
- Mandol: agglomerative agent memory system для long-term conversations; 4.8x insertion speedup при 10 QPS. Источник: `https://arxiv.org/abs/2606.29778v1` (art_d356641d87397b9bc646, ev_7247d64e7b36).
- REMIT: пять широко используемых agent workflow frameworks по-разному определяют resume semantics, ни один не предоставляет machine-checkable contract, а фактическое поведение нарушает даже заявленные фрагменты. Источник: `https://arxiv.org/abs/2608.03836v3` (art_2c2bba1f59b11d3bbf0c, ev_5a91846e5d7f).

## Прогресс
(дополнено правилами: модель не дописала раздел) в сжатом фрагменте 15 вызовов tools: queue_status×1, read_artifact×4, save_evidence×8, search_artifact×1, web_search×1

## Открытые вопросы и следующий шаг
(дополнено правилами: модель не дописала раздел) невыполненные шаги плана: Round 1: 3-4 search queries on LLM agent state storage; Select sources, fetch 5+ pages; Save evidence from pages; Round 2: refined queries + follow a link; Write report sections (approaches, limitations, comparison, unverified); complete_task

## Артефакты
(дополнено правилами: модель не дописала раздел) art_06bb89f539a917f10113, art_2c2bba1f59b11d3bbf0c, art_d356641d87397b9bc646, art_ee035e4658cd928988bf