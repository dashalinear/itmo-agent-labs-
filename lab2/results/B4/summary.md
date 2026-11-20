## Цель
Подготовить обзор подходов к хранению состояния LLM-агентов с цитатами, сравнением ограничений и разделом «Непроверенное».

## Ограничения пользователя
- [W1] Прочитать полностью (web_fetch) не меньше 5 реально найденных страниц.
- [W2] Каждое утверждение обзора подтверждено цитатой со страницы (evidence с URL); snippet поисковика подтверждением не считается.
- [W3] Обзор не длиннее 900 слов.
- [W4] Отдельный раздел «Непроверенное»: что не удалось подтвердить и какие источники не прочитаны.
- [W5] Сравнить подходы по их ограничениям.
- [W6] Минимум два раунда: после чтения первых страниц уточнить запросы по прочитанному; брать страницы с разных сайтов, если они доступны.
- [W7] Остановиться, когда требования к обзору выполнены: читать всю очередь не нужно.

## Решения и договорённости
- Использовать навык `research_web`: 2 раунда поиска, выбор источников с разных доменов, чтение полных страниц.
- План: 1) поиск, 2) выбор источников, 3) чтение 5+ страниц, 4) уточняющий поиск, 5) написание отчета.

## Подтверждённые факты (с источниками)
- Arize: State — механизм удержания информации во времени; LLM stateless. Источник: `https://arize.com/blog/memory-and-state-in-llm-applications/` (art_686c62d3a96c4f35f2a7, стр. 191, 197).
- Arize: Tiering memory приоритизирует данные (high-priority: предпочтения). Источник: `https://arize.com/blog/memory-and-state-in-llm-applications/` (art_686c62d3a96c4f35f2a7, стр. 312-316).
- LangGraph: Checkpointer отслеживает тред, Store — долговременную память. Источник: `https://docs.langchain.com/oss/python/langgraph/persistence` (art_d386c05025cc3edfd176, стр. 145-154).
- Agentic Memory: Обучение единого управления долгосрочной/краткосрочной памятью. Источник: `https://arxiv.org/abs/2601.01885v3` (art_469d550fcc625fe3537c, стр. 33).
- Resume Means Resume: Machine-checked контракт для checkpoint/interrupt/resume. Источник: `https://arxiv.org/abs/2608.03836v3` (art_2c2bba1f59b11d3bbf0c, стр. 33).
- 4-Tier Memory: CrewAI v1.15.1, Mem0, LangGraph. Источник: `https://www.kunalganglani.com/blog/ai-agent-memory-state-management` (art_a6e178ee9446cc7e403a, стр. 4, 56).
- Safe to Resume?: Верный rollback не гарантирует безопасное возобновление. Источник: `https://arxiv.org/abs/2608.29381v1` (art_b7186dcb4a8099370749, стр. 33).
- Cost and Accuracy of LTM: Тестбенч для DMAS; mem0, Graphiti, cognee. Источник: `https://arxiv.org/abs/2601.07978v5` (art_05dab3ed33c97cd22691, стр. 33).
- Beyond Similarity: MemGate как доверенный поиск памяти. Источник: `https://arxiv.org/abs/2606.06054v1` (art_65be5a9eb50caf56fc57, стр. 33).
- Mem0: Большие контекстные окна не решают проблему памяти; p95 latency 1.44s. Источник: `https://mem0.ai/library/agent-memory/long-term-memory` (art_2f24015b5e590638d484, стр. 115, 124).
- Kunalganglani: 4 уровня памяти (in-context, key-value, episodic, semantic); append-only log = state. Источник: `https://www.kunalganglani.com/blog/ai-agent-memory-state-management` (art_a6e178ee9446cc7e403a, стр. 69-70).

## Прогресс
(дополнено правилами: модель не дописала раздел) в сжатом фрагменте 15 вызовов tools: queue_status×1, read_artifact×4, save_evidence×5, search_artifact×3, web_fetch×1, web_search×1

## Открытые вопросы и следующий шаг
(дополнено правилами: модель не дописала раздел) невыполненные шаги плана: Round 1: 3-4 broad searches on LLM agent state storage; Select sources, queue diverse sites; Read 5+ pages, save evidence; Round 2: refined queries + follow a link; Write report sections (approaches, limitations, comparison, unverified); complete_task

## Артефакты
(дополнено правилами: модель не дописала раздел) art_2f24015b5e590638d484, art_7250816027508dc1c94d