# Частичный результат (задача не завершена): M6

- Статус: **paused** — waiting for user input
- Вид задачи: main

## Запрос

Диалог пользователя с основным агентом по корпусу.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 1

| id | тип | вывод | источник | стр. | строки | цитата |
|---|---|---|---|---|---|---|
| ev_0f3d3a639675 | fact | Старый ключ шифрования отзывается через 7 дней после перешифровки чекпоинтов | data/ops/runbook_rb12.md | — | 9–9 | Шаг 3: отозвать старый ключ через 7 дней после перешифровки. |

## Расход

```json
{
  "tokens": {
    "spent": 900466,
    "reserved": 0,
    "limit": 4000000,
    "of_which": {
      "input_tokens": 833950,
      "output_tokens": 14218,
      "compaction_input_tokens": 28464,
      "compaction_output_tokens": 4800,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 19034
    }
  },
  "model_calls": {
    "used": 15,
    "limit": 300
  },
  "compaction_calls": 0,
  "failed_calls": 0,
  "tool_calls": {
    "used": 12,
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
    "used": 44.1,
    "limit": 10800
  },
  "peak_input_tokens": 16312
}
```
