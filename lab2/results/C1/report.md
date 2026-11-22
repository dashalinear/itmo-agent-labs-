# Итоговый отчёт: C1

- Статус: **completed** — Обзор готов: 4 раздела (approaches, limitations, comparison, unverified), 12 evidence, 8 страниц прочитано, 10 поисковых запросов, 54 URL в очереди. Все критерии выполнены.
- Вид задачи: web

## Запрос

Разберись в подходах к хранению состояния LLM-агентов. Сам найди источники, изучи их, сопоставь ограничения и подготовь обзор с подтверждениями. Работай в пределах бюджета и явно укажи, что осталось непроверенным. Это нагрузочный прогон на фиксированной поисковой выдаче: охвати подтемы (чекпоинты, бэкенды хранения, долговременная память, компактификация контекста, durable execution, идемпотентность, схема состояния, безопасность) и собери очередь из как можно большего числа найденных URL (не меньше 50), затем читай самые релевантные.

## Ограничения пользователя

- [W1] Прочитать полностью (web_fetch) не меньше 5 реально найденных страниц.
- [W2] Каждое утверждение обзора подтверждено цитатой со страницы (evidence с URL); snippet поисковика подтверждением не считается.
- [W3] Обзор не длиннее 900 слов.
- [W4] Отдельный раздел «Непроверенное»: что не удалось подтвердить и какие источники не прочитаны.
- [W5] Сравнить подходы по их ограничениям.

## Подходы к хранению состояния LLM-агентов

**Чекпоинты.** Состояние фиксируется как снимок значений каналов (channel_values), версии и ожидающих узлов; `thread_id` связывает последовательность чекпоинтов в одну сессию [ev_29043dc075f7]. Чекпоинт — это точка, с которой можно возобновить выполнение, но он не знает о побочных эффектах вне графа [ev_660ff884fbf8].

**Бэкенды хранения.** SQLite подходит для одного процесса; PostgreSQL — для нескольких; сетевое хранилище добавляет задержку [ev_9583bea2990c, ev_a8a6b26297ee]. Выбор бэкенда влияет на доступность и латентность.

**Долговременная память.** Память хранят как документы с пространствами имён; векторный поиск не гарантирует точности [ev_165272c6c868].

**Компактификация контекста.** Summary сохраняет цель, ограничения, решения, факты; сжимают только завершённые фрагменты [ev_9745d44c3bb6]. Рекурсивная суммаризация без ограничений может зациклиться [ev_e37467548bca].

**Durable execution.** Недетерминированные действия выносят в activities; подход не делает внешние эффекты идемпотентными [ev_bd24faf743bb].

**Идемпотентность.** Ключ формируется из устойчивых признаков; ведётся реестр операций; чекпоинт не знает о побочных эффектах [ev_2d2cc1118721, ev_660ff884fbf8].

**Схема состояния.** Версия схемы хранится рядом с данными; несовместимые изменения ломают старые чекпоинты [ev_8916e7591ba3].

**Безопасность.** Секреты хранят отдельно; шифрование не защищает от утечки через логи [ev_8c3ebb497d48].

## Ограничения подходов

- **Чекпоинты:** полная история чекпоинтов быстро увеличивает объём базы [ev_60820d54ea9c].
- **Бэкенды:** сетевое хранилище добавляет задержку [ev_a8a6b26297ee].
- **Память:** векторный поиск не гарантирует точности [ev_165272c6c868].
- **Компактификация:** рекурсивная суммаризация без ограничений может зациклиться [ev_e37467548bca].
- **Durable execution:** не делает внешние эффекты идемпотентными [ev_bd24faf743bb].
- **Идемпотентность:** чекпоинт не знает о побочных эффектах [ev_660ff884fbf8].
- **Схема:** несовместимые изменения ломают старые чекпоинты [ev_8916e7591ba3].
- **Безопасность:** шифрование не защищает от утечки через логи [ev_8c3ebb497d48].

## Сравнение подходов по ограничениям

| Подход | Ограничение |
|--------|-------------|
| Чекпоинты | Рост объёма базы с историей [ev_60820d54ea9c] |
| Бэкенды | Задержка сетевого хранилища [ev_a8a6b26297ee] |
| Память | Неточность векторного поиска [ev_165272c6c868] |
| Компактификация | Риск зацикливания [ev_e37467548bca] |
| Durable execution | Не обеспечивает идемпотентность внешних эффектов [ev_bd24faf743bb] |
| Идемпотентность | Требует отдельного реестра операций [ev_2d2cc1118721] |
| Схема | Несовместимые изменения ломают чекпоинты [ev_8916e7591ba3] |
| Безопасность | Утечка через логи [ev_8c3ebb497d48] |

## Непроверенное

- Не удалось подтвердить конкретные метрики производительности (латентность, пропускная способность) для разных бэкендов.
- Не прочитаны страницы из очереди: 16 URL остались pending.
- Не подтверждены детали реализации идемпотентности в конкретных фреймворках (LangGraph, Temporal).
- Не изучены альтернативные подходы к компактификации (sliding window, RAG-подход).
- Не проверены требования к безопасности в регуляторных контекстах (GDPR, HIPAA).

## Приложение A. Подтверждения (evidence): 12

| id | тип | вывод | источник | стр. | строки | цитата |
|---|---|---|---|---|---|---|
| ev_165272c6c868 | approach | Долговременную память хранят как документы с пространствами имён; записи снабжают временем создания и источником; векто… | http://127.0.0.1:8765/p/memory-1 | — | 8–12 | Записи памяти снабжают временем создания и источником, чтобы устаревшие факты можно было отбросить. Агент должен различать факты из памяти и подтверждённые факты из документов. Векторный поиск по пам… |
| ev_29043dc075f7 | approach | Чекпоинт графа агента хранит значения каналов состояния, версию и список ожидающих записей узлов; thread_id связывает п… | http://127.0.0.1:8765/p/checkpoint-1 | — | 8–9 | Чекпоинт содержит значения каналов состояния, версию и список ожидающих записей узлов. Идентификатор потока thread_id связывает последовательность чекпоинтов одного диалога или задачи. |
| ev_2d2cc1118721 | approach | Идемпотентность: ключ строят из устойчивых признаков операции; реестр выполненных операций проверяют до побочного эффек… | http://127.0.0.1:8765/p/idempotency-3 | — | 8–12 | Реестр выполненных операций проверяют до побочного эффекта и обновляют после него. Ключ строят из устойчивых признаков операции: задачи, документа, версии и диапазона. Повторы с экспоненциальной заде… |
| ev_60820d54ea9c | limitation | Полная история чекпоинтов быстро увеличивает объём базы — ключевое ограничение подхода чекпоинтов. | http://127.0.0.1:8765/p/checkpoint-1 | — | 12–12 | Ограничение: Хранение полной истории чекпоинтов быстро увеличивает объём базы. |
| ev_660ff884fbf8 | controversial | Спорное утверждение: сохранения чекпоинта достаточно, чтобы избежать дублей; возражение — чекпоинт не знает о побочных … | http://127.0.0.1:8765/p/idempotency-3 | — | 13–13 | Спорное утверждение: Сохранения чекпоинта достаточно, чтобы избежать дублей. Возражение: Чекпоинт не знает о побочных эффектах инструментов; дубли предотвращают ключи операций. |
| ev_8916e7591ba3 | approach | Схема состояния: версия схемы хранится рядом с данными для миграции; нельзя хранить несериализуемые объекты; несовмести… | http://127.0.0.1:8765/p/schema-1 | — | 8–12 | Версия схемы хранится рядом с данными, чтобы новый код мог мигрировать старое состояние. Сериализация сообщений должна сохранять идентификаторы вызовов инструментов. Нельзя хранить в состоянии несери… |
| ev_9583bea2990c | approach | SQLite подходит для одного процесса и локальных экспериментов; PostgreSQL — для нескольких рабочих процессов; сетевое х… | http://127.0.0.1:8765/p/backends-1 | — | 8–10 | SQLite подходит для одного процесса и локальных экспериментов: файл базы переносится вместе с проектом. Для облачных развёртываний состояние хранят в управляемой базе с резервным копированием. Postgr… |
| ev_9745d44c3bb6 | approach | Компактификация: summary сохраняет цель, ограничения, решения, факты с источниками и открытые вопросы; сжимают только з… | http://127.0.0.1:8765/p/compaction-1 | — | 8–10 | Summary должно сохранять цель, ограничения, решения, подтверждённые факты с источниками и открытые вопросы. Сжимать можно только завершённые фрагменты: пара вызов инструмента и его результат не разры… |
| ev_a8a6b26297ee | limitation | Сетевое хранилище состояния добавляет задержку к каждому шагу агента. | http://127.0.0.1:8765/p/backends-1 | — | 12–12 | Ограничение: Сетевое хранилище добавляет задержку к каждому шагу агента. |
| ev_bd24faf743bb | approach | Durable execution сохраняет прогресс workflow после сбоя; недетерминированные действия выносят в activities с повторами… | http://127.0.0.1:8765/p/durable-2 | — | 8–12 | Недетерминированные действия, включая вызовы LLM, выносят в activities с повторами. Таймауты activities разделяют на время ожидания запуска и время выполнения. Durable execution сохраняет прогресс wo… |
| ev_e37467548bca | limitation | Рекурсивная суммаризация без ограничений может зациклиться — ограничение компактификации. | http://127.0.0.1:8765/p/compaction-1 | — | 12–12 | Ограничение: Рекурсивная суммаризация без ограничений может зациклиться. |
| ev_8c3ebb497d48 | approach | Безопасность: секреты не должны попадать в состояние и трассы; инструкции из документов — данные, а не команды; шифрова… | http://127.0.0.1:8765/p/security-1 | — | 8–12 | Секреты не должны попадать в состояние и трассы агента: их передают через окружение. Инструкции из документов и веб-страниц считаются данными и не меняют права агента. Персональные данные в истории м… |

## Приложение B. Поисковые запросы: 10

- `LangGraph checkpointing state persistence LLM agents` — 10 результатов, новых URL: 9 (fixture)
- `LLM agent state storage backends memory long-term` — 10 результатов, новых URL: 10 (fixture)
- `LLM agent context compaction summarization token limit` — 10 результатов, новых URL: 7 (fixture)
- `durable execution LLM agents idempotency retries` — 8 результатов, новых URL: 5 (fixture)
- `схема состояния LLM агента state schema threads` — 10 результатов, новых URL: 3 (fixture)
- `LLM agent state security prompt injection persistence` — 10 результатов, новых URL: 5 (fixture)
- `агент состояние безопасность хранение секретов в чекпоинтах` — 10 результатов, новых URL: 5 (fixture)
- `agent state backend comparison sqlite postgres redis` — 10 результатов, новых URL: 8 (fixture)
- `идемпотентность инструментов агента повторные вызовы side effects` — 10 результатов, новых URL: 2 (fixture)
- `LLM agent memory consolidation forgetting stale facts` — 10 результатов, новых URL: 0 (fixture)

## Приложение C. Источники

Найдено: 55, отобрано: 24, прочитано: 8, ошибок: 0, отклонено: 8, не разобрано (found): 23, в очереди (pending): 16.

| статус | URL | обнаружен | причина / ошибка |
|---|---|---|---|
| done | http://127.0.0.1:8765/p/checkpoint-1 | search:LangGraph checkpointing state persistence LLM agents | Основной обзор чекпоинтов: состав записи, ветвление |
| pending | http://127.0.0.1:8765/p/checkpoint-2 | search:LangGraph checkpointing state persistence LLM agents | Практика чекпоинтов: возврат к шагу, альтернативные ветки |
| pending | http://127.0.0.1:8765/p/checkpoint-3 | search:LangGraph checkpointing state persistence LLM agents | Типичные ошибки чекпоинтов |
| done | http://127.0.0.1:8765/p/memory-1 | search:LLM agent state storage backends memory long-term | Обзор долговременной памяти: метаданные записей |
| pending | http://127.0.0.1:8765/p/memory-2 | search:LLM agent state storage backends memory long-term | Практика памяти: явные операции записи |
| pending | http://127.0.0.1:8765/p/memory-3 | search:LLM agent state storage backends memory long-term | Типичные ошибки памяти |
| skipped | http://127.0.0.1:8765/p/memory-4 | search:LLM agent state storage backends memory long-term | Дублирует memory-1 (сравнение подходов, тот же сниппет) |
| skipped | http://127.0.0.1:8765/p/memory-5 | search:LLM agent state storage backends memory long-term | Чек-лист памяти, дублирует memory-1/2 |
| skipped | http://127.0.0.1:8765/p/memory-6 | search:LLM agent state storage backends memory long-term | Разбор случая памяти, дублирует memory-1 |
| skipped | http://127.0.0.1:8765/p/memory-7 | search:LLM agent state storage backends memory long-term | FAQ памяти, дублирует memory-2 |
| done | http://127.0.0.1:8765/p/durable-2 | search:LLM agent state storage backends memory long-term | Практика durable execution: activities с повторами |
| skipped | http://127.0.0.1:8765/p/durable-5 | search:LLM agent state storage backends memory long-term | Чек-лист durable, дублирует durable-2 |
| skipped | http://127.0.0.1:8765/p/durable-7 | search:LLM agent state storage backends memory long-term | FAQ durable, дублирует durable-2 |
| done | http://127.0.0.1:8765/p/compaction-1 | search:LLM agent context compaction summarization token lim… | Обзор компактификации: что сохранять в summary |
| pending | http://127.0.0.1:8765/p/compaction-2 | search:LLM agent context compaction summarization token lim… | Практика компактификации |
| pending | http://127.0.0.1:8765/p/compaction-3 | search:LLM agent context compaction summarization token lim… | Типичные ошибки: неразрывность пары вызов/результат |
| pending | http://127.0.0.1:8765/p/durable-1 | search:durable execution LLM agents idempotency retries | Обзор durable execution |
| pending | http://127.0.0.1:8765/p/durable-3 | search:durable execution LLM agents idempotency retries | Типичные ошибки durable execution: таймауты |
| skipped | http://127.0.0.1:8765/p/durable-4 | search:durable execution LLM agents idempotency retries | Сравнение подходов durable, дублирует durable-1/3 |
| skipped | http://127.0.0.1:8765/p/durable-6 | search:durable execution LLM agents idempotency retries | Разбор случая durable, дублирует durable-2 |
| done | http://127.0.0.1:8765/p/schema-1 | search:схема состояния LLM агента state schema threads | Обзор схемы состояния |
| pending | http://127.0.0.1:8765/p/schema-2 | search:схема состояния LLM агента state schema threads | Практика схемы состояния |
| pending | http://127.0.0.1:8765/p/schema-3 | search:LLM agent state security prompt injection persistence | Типичные ошибки схемы |
| done | http://127.0.0.1:8765/p/security-1 | search:агент состояние безопасность хранение секретов в чек… | Обзор безопасности: секреты в состоянии |
| pending | http://127.0.0.1:8765/p/security-2 | search:агент состояние безопасность хранение секретов в чек… | Практика безопасности |
| pending | http://127.0.0.1:8765/p/security-3 | search:агент состояние безопасность хранение секретов в чек… | Типичные ошибки безопасности |
| done | http://127.0.0.1:8765/p/backends-1 | search:agent state backend comparison sqlite postgres redis | Обзор бэкендов хранения (SQLite и др.) |
| pending | http://127.0.0.1:8765/p/backends-2 | search:agent state backend comparison sqlite postgres redis | Практика бэкендов хранения |
| pending | http://127.0.0.1:8765/p/backends-3 | search:agent state backend comparison sqlite postgres redis | Типичные ошибки бэкендов |
| pending | http://127.0.0.1:8765/p/idempotency-1 | search:идемпотентность инструментов агента повторные вызовы… | Обзор идемпотентности: ключи операций |
| pending | http://127.0.0.1:8765/p/idempotency-2 | search:идемпотентность инструментов агента повторные вызовы… | Практика идемпотентности: upsert по устойчивому id |
| done | http://127.0.0.1:8765/p/idempotency-3 | agent | fetched directly |

Непроверенные источники (не прочитаны): 39
- http://127.0.0.1:8765/p/checkpoint-2 (pending)
- http://127.0.0.1:8765/p/checkpoint-3 (pending)
- http://127.0.0.1:8765/p/checkpoint-4 (found)
- http://127.0.0.1:8765/p/checkpoint-5 (found)
- http://127.0.0.1:8765/p/checkpoint-6 (found)
- http://127.0.0.1:8765/p/checkpoint-7 (found)
- http://127.0.0.1:8765/loop (found)
- http://127.0.0.1:8765/large (found)
- http://127.0.0.1:8765/p/memory-2 (pending)
- http://127.0.0.1:8765/p/memory-3 (pending)
- http://127.0.0.1:8765/p/compaction-2 (pending)
- http://127.0.0.1:8765/p/compaction-3 (pending)
- http://127.0.0.1:8765/p/compaction-4 (found)
- http://127.0.0.1:8765/p/compaction-5 (found)
- http://127.0.0.1:8765/p/compaction-6 (found)
- http://127.0.0.1:8765/p/compaction-7 (found)
- http://127.0.0.1:8765/p/durable-1 (pending)
- http://127.0.0.1:8765/p/durable-3 (pending)
- http://127.0.0.1:8765/go2/durable-2 (found)
- http://127.0.0.1:8765/down (found)
- http://127.0.0.1:8765/p/schema-2 (pending)
- http://127.0.0.1:8765/p/schema-3 (pending)
- http://127.0.0.1:8765/p/schema-4 (found)
- http://127.0.0.1:8765/p/schema-5 (found)
- http://127.0.0.1:8765/p/schema-6 (found)
- http://127.0.0.1:8765/p/schema-7 (found)
- http://127.0.0.1:8765/p/security-2 (pending)
- http://127.0.0.1:8765/p/security-3 (pending)
- http://127.0.0.1:8765/p/security-4 (found)
- http://127.0.0.1:8765/p/security-6 (found)
- http://127.0.0.1:8765/p/backends-2 (pending)
- http://127.0.0.1:8765/p/backends-3 (pending)
- http://127.0.0.1:8765/p/backends-4 (found)
- http://127.0.0.1:8765/p/backends-5 (found)
- http://127.0.0.1:8765/p/backends-6 (found)
- http://127.0.0.1:8765/p/backends-7 (found)
- http://127.0.0.1:8765/binary (found)
- http://127.0.0.1:8765/p/idempotency-1 (pending)
- http://127.0.0.1:8765/p/idempotency-2 (pending)

## Критерии завершения

- OK min_evidence: 12/6
- OK required_sections: all present
- OK min_web_pages_read: 8/5
- OK min_web_sources_cited: 8/4
- OK min_search_queries: 10/3

## Расход

```json
{
  "tokens": {
    "spent": 370198,
    "reserved": 0,
    "limit": 1000000,
    "of_which": {
      "input_tokens": 335703,
      "output_tokens": 11339,
      "compaction_input_tokens": 19509,
      "compaction_output_tokens": 3647,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 28,
    "limit": 60
  },
  "compaction_calls": 3,
  "failed_calls": 0,
  "tool_calls": {
    "used": 49,
    "limit": 300
  },
  "search_calls": {
    "used": 10,
    "limit": 10
  },
  "http_requests": {
    "used": 8,
    "limit": 18
  },
  "cache_hits": 4,
  "bytes_downloaded": 11770,
  "active_seconds": {
    "used": 136.7,
    "limit": 5400
  },
  "peak_input_tokens": 17306
}
```
