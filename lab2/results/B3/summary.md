## Цель
Исследовать подходы к хранению состояния LLM-агентов, сопоставить их ограничения и подготовить обзор с подтверждениями (evidence).

## Ограничения пользователя
- [W1] Прочитать полностью (web_fetch) не меньше 5 реально найденных страниц.
- [W2] Каждое утверждение обзора подтверждено цитатой со страницы (evidence с URL); snippet поисковика подтверждением не считается.
- [W3] Обзор не длиннее 900 слов.
- [W4] Отдельный раздел «Непроверенное»: что не удалось подтвердить и какие источники не прочитаны.
- [W5] Сравнить подходы по их ограничениям.
- [W6] Минимум два раунда: после чтения первых страниц уточнить запросы и пройти хотя бы по одной ссылке, найденной на прочитанной странице; не больше 60 % прочитанных страниц с одного домена.

## Решения и договорённости
- Использовать навык `research_web` для автономного поиска.
- Выполнить минимум 3 поисковых запроса в первом раунде.
- Выбрать источники из разных доменов для соблюдения лимита долей доменов (max_domain_share: 0.6).
- Собрать минимум 6 подтверждений (evidence) с URL.
- Структура отчета должна включать секции: approaches, limitations, comparison, unverified.
- Второй раунд: уточнить запросы после чтения первых страниц, пройти по ссылкам из прочитанных статей.
- При неудаче `complete_task` из-за критериев (домены, ссылки) продолжить работу: искать источники на других доменах и переходить по ссылкам внутри прочитанных страниц.

## Подтверждённые факты (с источниками)
1. **Определение рабочей памяти:** «Whatever fits inside the current context window constitutes the agent’s working memory.» (Источник: `https://arxiv.org/html/2603.07670v1`, строка 319, artifact_id: `art_8fa11efb6c6659512101`, evidence_id: `ev_0a5f00cdefa0`).
2. **Аналогия с моделью Баддли:** «Baddeley’s central executive plus buffer model [Baddeley, 2000] maps neatly: the LLM is the executive, the context window is the buffer, and both share the same bottleneck—limited capacity.» (Источник: `https://arxiv.org/html/2603.07670v1`, строка 320, artifact_id: `art_8fa11efb6c6659512101`).
3. **Ограничение расширения контекста:** «Extending context windows to 100k+ tokens [Chen et al., 2023] delays the problem but cannot eliminate it, and longer contexts incur quadratic cost increases in attention.» (Источник: `https://arxiv.org/html/2603.07670v1`, строка 541, artifact_id: `art_8fa11efb6c6659512101`, evidence_id: `ev_0f303e210b48`).
4. **Проблема дрейфа суммаризации:** «Context-resident memory is transparent and infrastructure-free, but it carries a well-known pathology: summarization drift. Each compression pass silently discards low-frequency details.» (Источник: `https://arxiv.org/html/2603.07670v1`, строки 538-539, artifact_id: `art_8fa11efb6c6659512101`, evidence_id: `ev_5bc839714c67`).
5. **Архитектурный паттерн A (Монолитный контекст):** «Pattern A: Monolithic context. All memory lives inside the prompt. Zero infrastructure, fully transparent, but capacity-capped and prone to summarization drift.» (Источник: `https://arxiv.org/html/2603.07670v1`, строки 931-933, artifact_id: `art_8fa11efb6c6659512101`).
6. **Архитектурный паттерн B (Контекст + хранилище):** «Pattern B: Context + retrieval store. Working memory in the context window; long-term records in an external vector or structured store.» (Источник: `https://arxiv.org/html/2603.07670v1`, строки 936-940, artifact_id: `art_8fa11efb6c6659512101`, evidence_id: `ev_61da0420c17d`).
7. **Недостаточность длинного контекста:** «Longer context enlarges working memory but does not provide persistent cross-session storage, structured knowledge organization, selective retrieval from months of history, or governance mechanisms like deletion and access control.» (Источник: `https://arxiv.org/html/2603.07670v1`, строка 980