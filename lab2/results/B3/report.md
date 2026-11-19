# Частичный результат (задача не завершена): B3

- Статус: **paused** — no progress: web_fetch returned the same result 6 times
- Вид задачи: web

## Запрос

Разберись в подходах к хранению состояния LLM-агентов. Сам найди источники, изучи их, сопоставь ограничения и подготовь обзор с подтверждениями. Работай в пределах бюджета и явно укажи, что осталось непроверенным.

## Ограничения пользователя

- [W1] Прочитать полностью (web_fetch) не меньше 5 реально найденных страниц.
- [W2] Каждое утверждение обзора подтверждено цитатой со страницы (evidence с URL); snippet поисковика подтверждением не считается.
- [W3] Обзор не длиннее 900 слов.
- [W4] Отдельный раздел «Непроверенное»: что не удалось подтвердить и какие источники не прочитаны.
- [W5] Сравнить подходы по их ограничениям.
- [W6] Минимум два раунда: после чтения первых страниц уточнить запросы и пройти хотя бы по одной ссылке, найденной на прочитанной странице; не больше 60 % прочитанных страниц с одного домена.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 4

| id | тип | вывод | источник | стр. | строки | цитата |
|---|---|---|---|---|---|---|
| ev_e7598c59a9f0 | approach | Обзор 2026 (TMLR) выделяет три измерения памяти агентов: субстрат (внутреннее параметрическое состояние и внешние retri… | https://arxiv.org/abs/2602.06052v4 | — | 33–33 | we provide a unified view of foundation agent memory along three dimensions: memory substrate (internal parametric state and external retrieval-augmented stores), cognitive mechanism (sensory, workin… |
| ev_742050b34f09 | approach | В обзоре TMLR управление памятью (context curation, консолидация опыта) описывается как обучаемая способность, включая … | https://arxiv.org/abs/2602.06052v4 | — | 33–33 | memory management itself is becoming a trainable capability spanning reinforcement-learned context curation, experience consolidation at decision time, and the emerging ecosystem of portable, shareab… |
| ev_8966b7104d34 | approach | SagaLLM (Chang & Geng, 2025) интегрирует транзакционный паттерн Saga с персистентной памятью, автоматической компенсаци… | https://arxiv.org/abs/2503.11951v3 | — | 33–33 | SagaLLM bridges this gap by integrating the Saga transactional pattern with persistent memory, automated compensation, and independent validation agents. |
| ev_f52f46698a1d | limitation | SagaLLM сознательно ослабляет строгие ACID-гарантии, обеспечивая консистентность на уровне всего воркфлоу через модульн… | https://arxiv.org/abs/2503.11951v3 | — | 33–33 | Although SagaLLM relaxes strict ACID guarantees, it ensures workflow-wide consistency and recovery through modular checkpointing and compensable execution. |

## Приложение B. Поисковые запросы: 10

- `LLM agent state management memory persistence approaches` — 8 результатов, новых URL: 8 (multi:ddgs:bing,ddgs:duckduckgo,ddgs:yandex,wikipedia,arxiv,hn_algolia,stackoverflow,github)
- `LLM agent memory short-term long-term storage vector database` — 8 результатов, новых URL: 8 (multi:ddgs:bing,ddgs:duckduckgo,ddgs:yandex,wikipedia,arxiv,hn_algolia,stackoverflow,github)
- `LangGraph checkpointer agent state persistence` — 8 результатов, новых URL: 8 (multi:ddgs:bing,ddgs:duckduckgo,ddgs:yandex,wikipedia,arxiv,hn_algolia,stackoverflow,github)
- `LangGraph checkpointer persistence state store thread` — 1 результатов, новых URL: 1 (multi:ddgs:bing,ddgs:duckduckgo,ddgs:yandex,wikipedia,arxiv,hn_algolia,stackoverflow,github)
- `MemGPT memory management LLM agent virtual context` — 1 результатов, новых URL: 1 (multi:ddgs:bing,ddgs:duckduckgo,ddgs:yandex,wikipedia,arxiv,hn_algolia,stackoverflow,github)
- `LangGraph persistence checkpointer documentation` — 0 результатов, новых URL: 0 (multi:ddgs:bing,ddgs:duckduckgo,ddgs:yandex,wikipedia,arxiv,hn_algolia,stackoverflow,github)
- `"langgraph" checkpointer persistence` — 8 результатов, новых URL: 5 (multi:ddgs:bing,ddgs:duckduckgo,ddgs:yandex,wikipedia,arxiv,hn_algolia,stackoverflow,github)
- `Snowflake checkpoint saver LangGraph` — 1 результатов, новых URL: 0 (multi:ddgs:bing,ddgs:duckduckgo,ddgs:yandex,wikipedia,arxiv,hn_algolia,stackoverflow,github)
- `LangGraph checkpointer persistence state management` — 1 результатов, новых URL: 1 (multi:ddgs:bing,ddgs:duckduckgo,ddgs:yandex,wikipedia,arxiv,hn_algolia,stackoverflow,github)
- `LLM agent state persistence checkpoint memory management` — 5 результатов, новых URL: 5 (multi:ddgs:bing,ddgs:duckduckgo,ddgs:yandex,wikipedia,arxiv,hn_algolia,stackoverflow,github)

## Приложение C. Источники

Найдено: 38, отобрано: 15, прочитано: 11, ошибок: 4, отклонено: 22, не разобрано (found): 1, в очереди (pending): 0.

| статус | URL | обнаружен | причина / ошибка |
|---|---|---|---|
| error | https://arize.com/blog/memory-and-state-in-llm-applications/ | search:LLM agent state management memory persistence approa… | timeout_retries_exhausted: ReadTimeout:  |
| error | https://agentmemo.ai/blog/agent-state-management-guide.html | search:LLM agent state management memory persistence approa… | timeout_retries_exhausted: ReadTimeout:  |
| done | https://arxiv.org/abs/2505.16067 | search:LLM agent state management memory persistence approa… | Эмпирическое исследование влияния управления памятью на поведение LLM-агентов |
| error | https://vahu.org/memory-and-state-management-for-persistent-llm-agents | search:LLM agent state management memory persistence approa… | timeout_retries_exhausted: ReadTimeout:  |
| skipped | https://en.wikipedia.org/wiki/Black_October_(information_security) | search:LLM agent state management memory persistence approa… | Не по теме (PHP memory errors) |
| skipped | https://en.wikipedia.org/wiki/AI_anthropomorphism | search:LLM agent state management memory persistence approa… | Не по теме |
| skipped | https://en.wikipedia.org/wiki/Computational_creativity | search:LLM agent state management memory persistence approa… | Не по теме |
| skipped | https://en.wikipedia.org/wiki/Misinformation | search:LLM agent state management memory persistence approa… | Не по теме |
| skipped | https://en.wikipedia.org/wiki/Large_language_model | search:LLM agent memory short-term long-term storage vector… | Слишком общий, не про хранение состояния |
| skipped | https://en.wikipedia.org/wiki/Machine_learning | search:LLM agent memory short-term long-term storage vector… | Не по теме |
| skipped | https://en.wikipedia.org/wiki/Glossary_of_artificial_intelligence | search:LLM agent memory short-term long-term storage vector… | Не по теме |
| skipped | https://en.wikipedia.org/wiki/Chatbot | search:LLM agent memory short-term long-term storage vector… | Не по теме |
| skipped | https://en.wikipedia.org/wiki/Neural_network_(machine_learning) | search:LLM agent memory short-term long-term storage vector… | LSTM, не про память агентов |
| skipped | https://arxiv.org/abs/2510.09720v1 | search:LLM agent memory short-term long-term storage vector… | Узкая тема preference-aware memory update; отложу, если не хватит источников |
| done | https://arxiv.org/abs/2602.06052v4 | search:LLM agent memory short-term long-term storage vector… | Обзор агентской памяти (survey) — полезно для сравнения подходов |
| done | https://arxiv.org/abs/2507.22925v1 | search:LLM agent memory short-term long-term storage vector… | Иерархическая память для долгосрочного рассуждения |
| done | https://arxiv.org/abs/2608.03836v3 | search:LangGraph checkpointer agent state persistence | Семантика checkpoint/interrupt/resume в 5 фреймворках — ограничения подходов |
| done | https://arxiv.org/abs/2608.29381v1 | search:LangGraph checkpointer agent state persistence | Безопасность checkpoint/rollback в stateful-агентах |
| skipped | https://stackoverflow.com/questions/79794139/psycopg-operationalerror-server-closed-the-c… | search:LangGraph checkpointer agent state persistence | Конкретный баг, не обзор подходов |
| skipped | https://github.com/vaibhav7087/agent-state-checkpoints | search:LangGraph checkpointer agent state persistence | Практический проект, не обзор |
| skipped | https://github.com/FajarLegiantoA/AgenticAI_LangGraph | search:LangGraph checkpointer agent state persistence | Практический проект, не обзор |
| skipped | https://github.com/SAP/langgraph-checkpoint-sap-agent-memory-service | search:LangGraph checkpointer agent state persistence | README конкретного савера, не обзор |
| skipped | https://github.com/arpits-code/stateful-rag-agent | search:LangGraph checkpointer agent state persistence | Практический проект, не обзор |
| skipped | https://github.com/danishdynamic/Multi-Agent-Research-Decision-Assistant | search:LangGraph checkpointer agent state persistence | Практический проект, не обзор |
| error | https://en.wikipedia.org/wiki/Black_October_(inform... | agent | http_error: HTTP 404 |
| skipped | https://github.com/sumukh-m-gowda/CHATGPT-2.O | search:LangGraph checkpointer persistence state store thread | Проект-пример, не систематический источник по подходам |
| done | https://arxiv.org/abs/2310.08560v2 | search:MemGPT memory management LLM agent virtual context | MemGPT — ключевой подход к виртуальному управлению контекстом/памятью LLM-агентов |
| skipped | https://stackoverflow.com/questions/79607143/how-to-implement-subgraph-memory-persistence… | search:"langgraph" checkpointer persistence | Узкий вопрос о subgraph, не нужен для обзора подходов |
| skipped | https://stackoverflow.com/questions/79317849/attributeerror-str-object-has-no-attribute-m… | search:"langgraph" checkpointer persistence | Баг-репорт, не относится к подходу |
| done | https://github.com/NP-compete/langgraph-checkpoint-snowflake | search:"langgraph" checkpointer persistence | Альтернативный бэкенд checkpoint (Snowflake + Redis-кэш) — показывает ограничения/варианты хранения |
| skipped | https://github.com/scinfu/langmigrate | search:"langgraph" checkpointer persistence | Миграции схем — периферийная тема, не ядро подходов |
| done | https://github.com/davidmovsisian/langgraph-persistence-guide | search:"langgraph" checkpointer persistence | Комплексный гайд по checkpointers и stores в LangGraph — практический подход к хранению состояния |
| done | https://arxiv.org/abs/2606.23521v1 | search:LLM agent state persistence checkpoint memory manage… | Concordia: JIT-compiled persistent-kernel checkpointing for fault-tolerant LLM inference — directly relevant to state p… |
| done | https://arxiv.org/abs/2609.38275v1 | search:LLM agent state persistence checkpoint memory manage… | Fuzzing persistent memory use in LLM agents — covers limitations of memory persistence |
| skipped | https://arxiv.org/abs/2606.03895v3 | search:LLM agent state persistence checkpoint memory manage… | Agent libOS is more about runtime substrate and capability control, less directly about state persistence approaches |
| skipped | https://arxiv.org/abs/2611.04003v1 | search:LLM agent state persistence checkpoint memory manage… | WASP-Mem is about procedural memory organization, less directly about state persistence approaches |
| done | https://arxiv.org/abs/2503.11951v3 | search:LLM agent state persistence checkpoint memory manage… | SagaLLM: context management, validation, transaction guarantees for multi-agent LLM planning — relevant to state manage… |

Непроверенные источники (не прочитаны): 1
- https://github.com/AJEETMOHANTY/nextgen-ai-assistant (found)

## Расход

```json
{
  "tokens": {
    "spent": 845176,
    "reserved": 0,
    "limit": 1200000,
    "of_which": {
      "input_tokens": 794835,
      "output_tokens": 6179,
      "compaction_input_tokens": 35762,
      "compaction_output_tokens": 8400,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 64,
    "limit": 90
  },
  "compaction_calls": 7,
  "failed_calls": 0,
  "tool_calls": {
    "used": 74,
    "limit": 300
  },
  "search_calls": {
    "used": 10,
    "limit": 10
  },
  "http_requests": {
    "used": 21,
    "limit": 50
  },
  "cache_hits": 19,
  "bytes_downloaded": 1012606,
  "active_seconds": {
    "used": 392.8,
    "limit": 5400
  },
  "peak_input_tokens": 17042
}
```
