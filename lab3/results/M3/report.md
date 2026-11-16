# Частичный результат (задача не завершена): M3

- Статус: **paused** — waiting for user input
- Вид задачи: main

## Запрос

Диалог пользователя с основным агентом по корпусу.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 1

| id | тип | вывод | источник | стр. | строки | цитата |
|---|---|---|---|---|---|---|
| ev_4ab84c207ebc | fact | В runbook RB-09 допустимое время переключения на реплику — 15 минут | data/ops/runbook_rb09.md | — | 11–11 | Шаг 3: переключить агентов на реплику; допустимое время переключения - 15 минут. |

## Расход

```json
{
  "tokens": {
    "spent": 732765,
    "reserved": 0,
    "limit": 4000000,
    "of_which": {
      "input_tokens": 651279,
      "output_tokens": 7685,
      "compaction_input_tokens": 26858,
      "compaction_output_tokens": 4800,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 42143
    }
  },
  "model_calls": {
    "used": 29,
    "limit": 300
  },
  "compaction_calls": 1,
  "failed_calls": 0,
  "tool_calls": {
    "used": 22,
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
    "used": 97.7,
    "limit": 10800
  },
  "peak_input_tokens": 16202
}
```
