# Частичный результат (задача не завершена): B4

- Статус: **paused** — budget exhausted before the call (model_calls: needed 1, remaining 0 of 120)
- Вид задачи: web

## Запрос

Разберись в подходах к хранению состояния LLM-агентов. Сам найди источники, изучи их, сопоставь ограничения и подготовь обзор с подтверждениями. Работай в пределах бюджета и явно укажи, что осталось непроверенным.

## Ограничения пользователя

- [W1] Прочитать полностью (web_fetch) не меньше 5 реально найденных страниц.
- [W2] Каждое утверждение обзора подтверждено цитатой со страницы (evidence с URL); snippet поисковика подтверждением не считается.
- [W3] Обзор не длиннее 900 слов.
- [W4] Отдельный раздел «Непроверенное»: что не удалось подтвердить и какие источники не прочитаны.
- [W5] Сравнить подходы по их ограничениям.
- [W6] Минимум два раунда: после чтения первых страниц уточнить запросы по прочитанному; брать страницы с разных сайтов, если они доступны.
- [W7] Остановиться, когда требования к обзору выполнены: читать всю очередь не нужно.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 6

| id | тип | вывод | источник | стр. | строки | цитата |
|---|---|---|---|---|---|---|
| ev_0c64fb4149e7 | fact | Mem0: большие контекстные окна не решают проблему памяти; Mem0-пайплайн даёт 92.5% на LoCoMo при ~6956 токенах на запро… | https://mem0.ai/library/agent-memory/long-term-memory | — | 118–118 | The paper Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory demonstrates that structured memory pipelines outperform full-context baselines. Mem0's current algorithm reports 92… |
| ev_da110be2e06c | fact | Mem0: латентность растёт с размером контекста (200K токенов — 5–10 с); Mem0 показывает p95 1.44s при высокой нагрузке | https://mem0.ai/library/agent-memory/long-term-memory | — | 122–122 | Latency also grows with context size: 4K tokens produce sub-second responses, 200K tokens take 5 to 10 seconds, and high concurrency creates GPU memory pressure and queue backlogs. |
| ev_d7c9a28674af | fact | Mem0: долгосрочная память реализуется пайплайном из четырёх стадий — extraction, consolidation, storage (вектор/граф), … | https://mem0.ai/library/agent-memory/long-term-memory | — | 219–219 | Through a four-stage pipeline: extraction pulls durable facts out of raw conversation, consolidation merges duplicates and resolves contradictions against existing memory, storage indexes the result … |
| ev_84cd7843668d | fact | VAHU: бездумное добавление в память приводит к error propagation; utility-based deletion даёт до 10% прироста производи… | https://vahu.org/memory-and-state-management-for-persistent-llm-agents | — | 55–55 | Research published in May 2025 highlights that indiscriminate addition propagates errors. If your agent makes a bad decision and stores it as a "fact," future queries might retrieve that error, leadi… |
| ev_c715373f8498 | fact | VAHU: векторные БД хороши для семантического сходства, но плохо справляются с реляционными и временными зависимостями —… | https://vahu.org/memory-and-state-management-for-persistent-llm-agents | — | 63–63 | Vector databases are great for semantic similarity, but they struggle with relational and temporal dependencies. If a user asks, "What did I decide about the project timeline after our meeting with S… |
| ev_9da8570168e4 | fact | VAHU: трёхуровневая архитектура памяти — working (эфемерное), short-term (Redis-кэш), long-term (векторные БД Pinecone/… | https://vahu.org/memory-and-state-management-for-persistent-llm-agents | — | 45–45 | Working Memory: This is your immediate scratchpad. It uses ephemeral storage for the current task's context. If you're using LangChain, this is often handled within the chain's execution state. It's … |

## Приложение B. Поисковые запросы: 6

- `LLM agent state management memory persistence approaches` — 8 результатов, новых URL: 8 (multi:ddgs:bing,ddgs:duckduckgo,ddgs:yandex,wikipedia,arxiv,hn_algolia,stackoverflow,github)
- `LangGraph checkpointer state persistence agent` — 8 результатов, новых URL: 8 (multi:ddgs:bing,ddgs:duckduckgo,ddgs:yandex,wikipedia,arxiv,hn_algolia,stackoverflow,github)
- `LLM agent memory short-term long-term context window limitations` — 8 результатов, новых URL: 8 (multi:ddgs:bing,ddgs:duckduckgo,ddgs:yandex,wikipedia,arxiv,hn_algolia,stackoverflow,github)
- `Mem0 long-term memory LLM agent vector store limitations` — 7 результатов, новых URL: 7 (multi:ddgs:bing,ddgs:duckduckgo,ddgs:yandex,wikipedia,arxiv,hn_algolia,stackoverflow,github)
- `Letta MemGPT memory hierarchy agent state management` — 5 результатов, новых URL: 5 (multi:ddgs:bing,ddgs:duckduckgo,ddgs:yandex,wikipedia,arxiv,hn_algolia,stackoverflow,github)
- `Mem0 long-term memory agent architecture limitations` — 0 результатов, новых URL: 0 (multi:ddgs:bing,ddgs:duckduckgo,ddgs:yandex,wikipedia,arxiv,hn_algolia,stackoverflow,github)

## Приложение C. Источники

Найдено: 36, отобрано: 13, прочитано: 11, ошибок: 1, отклонено: 22, не разобрано (found): 1, в очереди (pending): 1.

| статус | URL | обнаружен | причина / ошибка |
|---|---|---|---|
| done | https://arize.com/blog/memory-and-state-in-llm-applications/ | search:LLM agent state management memory persistence approa… | duplicate |
| done | https://vahu.org/memory-and-state-management-for-persistent-llm-agents | search:LLM agent state management memory persistence approa… | three-tier architecture, vector DB retrieval — different perspective |
| done | https://www.kunalganglani.com/blog/ai-agent-memory-state-management | search:LLM agent state management memory persistence approa… | Four-tier memory architecture, durable resumption patterns, framework implementations |
| skipped | https://agentmemo.ai/blog/agent-state-management-guide.html | search:LLM agent state management memory persistence approa… | backup, similar to kunalganglani |
| skipped | https://arxiv.org/html/2603.07670 | search:LLM agent state management memory persistence approa… | backup (survey on multi-agent shared memory) |
| skipped | https://en.wikipedia.org/wiki/Black_October_(information_security) | search:LLM agent state management memory persistence approa… | off-topic (PHP memory bugs) |
| skipped | https://en.wikipedia.org/wiki/AI_anthropomorphism | search:LLM agent state management memory persistence approa… | off-topic |
| skipped | https://en.wikipedia.org/wiki/Computational_creativity | search:LLM agent state management memory persistence approa… | off-topic |
| done | https://docs.langchain.com/oss/python/langgraph/persistence | search:LangGraph checkpointer state persistence agent | Official LangGraph docs on persistence/checkpointers — primary source for one approach |
| skipped | https://docs.langchain.com/oss/python/langgraph/checkpointers | search:LangGraph checkpointer state persistence agent | same docs site as persistence page; avoid domain concentration |
| skipped | https://mortalapps.com/agents/frameworks/langgraph-checkpointers-state-persistence/ | search:LangGraph checkpointer state persistence agent | backup, similar to official docs |
| skipped | https://www.ibm.com/docs/en/watsonx/watson-orchestrate/base?topic=agents-enabling-state-p… | search:LangGraph checkpointer state persistence agent | backup, vendor-specific |
| skipped | https://fast.io/resources/langgraph-persistence/ | search:LangGraph checkpointer state persistence agent | backup, similar content |
| done | https://arxiv.org/abs/2608.03836v3 | search:LangGraph checkpointer state persistence agent | Paper on checkpoint/interrupt/resume semantics across agent frameworks — limitations of persistence layers |
| done | https://arxiv.org/abs/2608.29381v1 | search:LangGraph checkpointer state persistence agent | Security implications of checkpoint/rollback for stateful agents |
| skipped | https://stackoverflow.com/questions/79794139/psycopg-operationalerror-server-closed-the-c… | search:LangGraph checkpointer state persistence agent | single bug report, not a source of approaches |
| skipped | https://en.wikipedia.org/wiki/Large_language_model | search:LLM agent memory short-term long-term context window… | too broad, snippet not specific to agent state |
| skipped | https://en.wikipedia.org/wiki/Generative_pre-trained_transformer | search:LLM agent memory short-term long-term context window… | off-topic (architecture) |
| skipped | https://en.wikipedia.org/wiki/ChatGPT | search:LLM agent memory short-term long-term context window… | off-topic |
| skipped | https://en.wikipedia.org/wiki/Artificial_intelligence_in_video_games | search:LLM agent memory short-term long-term context window… | off-topic |
| skipped | https://en.wikipedia.org/wiki/Neural_network_(machine_learning) | search:LLM agent memory short-term long-term context window… | off-topic |
| done | https://arxiv.org/abs/2601.01885v3 | search:LLM agent memory short-term long-term context window… | Paper on unified long/short-term memory management for LLM agents |
| skipped | https://arxiv.org/abs/2510.09720v1 | search:LLM agent memory short-term long-term context window… | narrow (preference-aware updates); keep as backup |
| skipped | https://arxiv.org/abs/2601.11913v2 | search:LLM agent memory short-term long-term context window… | narrow (LSTM-inspired MAS); backup |
| done | https://mem0.ai/library/agent-memory/long-term-memory | search:Mem0 long-term memory LLM agent vector store limitat… | Практический подход Mem0 к долгосрочной памяти |
| skipped | https://mem0.ai/blog/state-of-ai-agent-memory-2026 | search:Mem0 long-term memory LLM agent vector store limitat… | Маркетинговый блог Mem0, бенчмарки от провайдера |
| skipped | https://atlan.com/know/how-to-implement-long-term-memory-ai-agents/ | search:Mem0 long-term memory LLM agent vector store limitat… | Практический гайд, дублирует mem0.ai |
| skipped | https://mojostudio.in/blog/ai-agent-memory-systems-mem0-vector-databases-episodic-recall | search:Mem0 long-term memory LLM agent vector store limitat… | Блог, вторичный источник |
| done | https://arxiv.org/abs/2601.07978v5 | search:Mem0 long-term memory LLM agent vector store limitat… | Исследование стоимости и точности LTM в распределённых мультиагентных системах — ограничения |
| done | https://arxiv.org/abs/2606.06054v1 | search:Mem0 long-term memory LLM agent vector store limitat… | Ограничения similarity-based memory search — доверие к извлечению |
| skipped | https://github.com/letta-ai/letta | search:Letta MemGPT memory hierarchy agent state management | README репозитория, дублирует доки Letta |
| error | https://docs.letta.com/v1-sdk/concepts/stateful-agents | search:Letta MemGPT memory hierarchy agent state management | timeout_retries_exhausted: ReadTimeout:  |
| done | https://www.letta.com/blog/agent-memory/ | search:Letta MemGPT memory hierarchy agent state management | Официальный блог Letta (MemGPT) — подход к иерархии памяти агентов, другой домен |
| skipped | https://github.com/qing-tian-meng-ying/Agent-Memory-Structure/blob/main/all_techniques/26… | search:Letta MemGPT memory hierarchy agent state management | Сторонний репозиторий, вторичный источник |
| pending | https://haozhe-xing.github.io/agent_learning/en/chapter_memory/06b_memgpt_practice.html | search:Letta MemGPT memory hierarchy agent state management | Практический разбор архитектуры MemGPT/Letta, учебный источник с другого домена |

Непроверенные источники (не прочитаны): 2
- https://arxiv.org/pdf/2504.19413 (found)
- https://haozhe-xing.github.io/agent_learning/en/chapter_memory/06b_memgpt_practice.html (pending)

## Расход

```json
{
  "tokens": {
    "spent": 1678224,
    "reserved": 0,
    "limit": 2000000,
    "of_which": {
      "input_tokens": 1581220,
      "output_tokens": 12826,
      "compaction_input_tokens": 71336,
      "compaction_output_tokens": 12842,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 120,
    "limit": 120
  },
  "compaction_calls": 11,
  "failed_calls": 0,
  "tool_calls": {
    "used": 153,
    "limit": 300
  },
  "search_calls": {
    "used": 6,
    "limit": 10
  },
  "http_requests": {
    "used": 14,
    "limit": 50
  },
  "cache_hits": 1,
  "bytes_downloaded": 2269866,
  "active_seconds": {
    "used": 452.5,
    "limit": 5400
  },
  "peak_input_tokens": 17053
}
```
