# Частичный результат (задача не завершена): M4

- Статус: **paused** — waiting for user input
- Вид задачи: main

## Запрос

Диалог пользователя с основным агентом по корпусу.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 1

| id | тип | вывод | источник | стр. | строки | цитата |
|---|---|---|---|---|---|---|
| ev_756fc9b5b737 | fact | В runbook RB-09 допустимое время переключения агентов на реплику — 15 минут | data/ops/runbook_rb09.md | — | 11–11 | Шаг 3: переключить агентов на реплику; допустимое время переключения - 15 минут. |

## Расход

```json
{
  "tokens": {
    "spent": 929292,
    "reserved": 0,
    "limit": 4000000,
    "of_which": {
      "input_tokens": 797392,
      "output_tokens": 23329,
      "compaction_input_tokens": 33011,
      "compaction_output_tokens": 6000,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 69560
    }
  },
  "model_calls": {
    "used": 18,
    "limit": 300
  },
  "compaction_calls": 0,
  "failed_calls": 0,
  "tool_calls": {
    "used": 10,
    "limit": 1200
  },
  "search_calls": {
    "used": 0,
    "limit": 12
  },
  "http_requests": {
    "used": 0,
    "limit": 80
  },
  "cache_hits": 0,
  "bytes_downloaded": 0,
  "active_seconds": {
    "used": 64.6,
    "limit": 10800
  },
  "peak_input_tokens": 8886
}
```
