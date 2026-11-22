# Частичный результат (задача не завершена): M11

- Статус: **paused** — waiting for user input
- Вид задачи: main

## Запрос

Диалог пользователя с основным агентом по корпусу.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 3

| id | тип | вывод | источник | стр. | строки | цитата |
|---|---|---|---|---|---|---|
| ev_df923f824589 | fact | Квоты по умолчанию: 2000 вызовов/сутки на команду, 50/минуту, веб-поиск 300/сутки | data/agents/agent_tool_quotas.md | — | 8–9 | Не более 2000 вызовов инструментов в сутки на команду и не более 50 вызовов в минуту. Вызовы веб-поиска считаются отдельно: не более 300 в сутки. |
| ev_1375ef8dae4d | fact | Старый ключ отзывается через 7 дней после перешифровки | data/ops/runbook_rb12.md | — | 9–9 | Шаг 3: отозвать старый ключ через 7 дней после перешифровки. |
| ev_e04fe4d6bddf | fact | Журнал событий копируют на резервный том каждые 4 часа | data/storage/st_snapshot_policy.md | — | 7–7 | Снимок журнала событий сохраняют на резервный том каждые четыре часа; полную копию базы - раз в сутки в 03:00. |

## Расход

```json
{
  "tokens": {
    "spent": 1351352,
    "reserved": 0,
    "limit": 4000000,
    "of_which": {
      "input_tokens": 1135138,
      "output_tokens": 30278,
      "compaction_input_tokens": 101866,
      "compaction_output_tokens": 28850,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 55220
    }
  },
  "model_calls": {
    "used": 33,
    "limit": 300
  },
  "compaction_calls": 3,
  "failed_calls": 0,
  "tool_calls": {
    "used": 27,
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
    "used": 122.5,
    "limit": 10800
  },
  "peak_input_tokens": 15887
}
```
