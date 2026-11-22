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
- План: Round 1 (поиск) → выбор/чтение 5+ страниц → Round 2 (уточнение) → написание отчёта.
- Критерии завершения: min_search_queries=3, min_web_pages_read=5, min_web_sources_cited=4, min_evidence=6, required_sections=["approaches", "limitations", "comparison", "unverified"], min_queries_after_reading=1.

## Подтверждённые факты (с источниками)
- Arize AI (URL: https://arize.com/blog/memory-and-state-in-llm-applications/):
  - "The term 'memory' is often used in discussions about LLM applications, but it’s an abstraction that can mean different things to different people." (line 171)
  - "LLMs have a fixed context window, limiting how much information they can process at once." (line 239)
  - "Stateful designs often improve user experience and application performance but come with increased costs." (line 243, ev_4859d20f6dd1)
  - "Domain-specific applications benefit from storing structured entities as discrete memory variables." (line 316)
- LangGraph Persistence (URL: https://docs.langchain.com/oss/python/langgraph/persistence):
  - "LangGraph’s persistence layer gives agents short-term memory through checkpointers and long-term memory through stores." (line 145, ev_57781f5ec58a)
  - "Checkpointers persist a thread’s graph state as checkpoints. Use them for short-term, thread-scoped memory..." (line 152)
  - "Most applications can use both: a checkpointer tracks the current thread, and a store tracks durable information across threads." (line 156, ev_89f9260e5213)
- arXiv 2603.07670 (URL: https://arxiv.org/html/2603.07670):
  - "Large language model (LLM) agents increasingly operate in settings where a single context window is far too small to capture what has happened, what was learned, and what should not be repeated." (line 165)
  - "Whatever fits inside the current context window constitutes the agent’s working memory." (line 317)
  - "Extending context windows to 100k+ tokens [Chen et al., 2023] delays the problem but cannot eliminate it, and longer contexts incur quadratic cost increases in attention." (line 539)
  - "Memory-augmented agents are expensive: large context windows, multiple retrieval calls per step, ever-growing stores." (line 1031)
  - "Context-resident memory is transparent and infrastructure-free, but it carries a well-known pathology: summarization drift." (line 536, ev_4cc9ac1595f7)
  - "Long context is not memory. Despite context windows stretching to 200k tokens [Chen et al., 2023], long-context models consistently underperform purpose-built memory systems on tasks requiring selective retrieval and active management." (line 766, ev_c3248fc55c1f)
  - "RAG-based agents beat pure long-context baselines across the board, yet the primary bottleneck is no longer storage—it is retrieval quality." (line 771, ev_4659ba3e27b9)
  - "Parametric memory excels at seamless knowledge integration but fails at targeted deletion and auditing. Non-parametric memory supports inspection and governance but can feel “bolted on”—the agent sometimes ignores retrieved records or uses them inconsistently." (line 784, ev_be1eb5b0c66f)
- mem0.ai (URL: https://mem0.ai/library/agent-memory/vector-databases-and-memory-for-ai-agents):
  - "They know nothing about the content of what they store. They cannot distinguish a user preference from a random sentence. They have no awareness of time, contradiction, or relevance to a task." (line 79, ev_fbc72fab7e33)
  - "The result is memory pollution: the store has grown to contain more noise than signal, and the noise is semantic..." (line 211, ev_42f0cdc173ae)
- explore.n1n.ai (URL: https://explore.n1n.ai/blog/rethinking-agent-memory-vector-databases-overkill-2026-07-25):
  - "Vector search answers the question: 'What are the things most similar to this query?' This is inherently fu..." (line 31, ev_074ddcb57c6b)
  - "Embedding Costs: Every write requires a call to an embedding API. Indexing..." (lines 33-36, ev_e88fab39a04a)

## Прогресс
- Выполнено 5 поисковых запроса (Round 1: 3, Round 2: 2).
- Найдено 33 URL, выбрано 10 в очередь.
- Прочитано 5 страниц из 5 необходимых (Arize, LangGraph, arXiv 2603.07670, mem0.ai, explore.n1n.ai).
- Детально изучены разделы Arize (lines 155-493), LangGraph (lines 120-239), arXiv (lines 530-544, 760-785), mem0.ai (lines 1-212), explore.n1n.ai (полный текст).
- Собрано 11 evidence (минимум 6 выполнен).
- Написаны разделы: approaches (updated, 5 cited evidence), limitations (updated, 8 cited evidence).

## Открытые вопросы и следующий шаг
(дополнено правилами: модель не дописала раздел) невыполненные шаги плана: Round 1: 3-4 search queries on LLM agent state storage; Select sources, fetch 5+ pages; Save evidence from pages; Round 2: refined queries + follow a link; Write report sections (approaches, limitations, comparison, unverified); complete_task

## Артефакты
(дополнено правилами: модель не дописала раздел) art_191abf9d0ab39a8720c4