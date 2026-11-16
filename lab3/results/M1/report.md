# Частичный результат (задача не завершена): M1

- Статус: **paused** — budget exhausted before the call (model_calls: needed 1, remaining 0 of 200)
- Вид задачи: main

## Запрос

Диалог пользователя с основным агентом по корпусу.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 5

| id | тип | вывод | источник | стр. | строки | цитата |
|---|---|---|---|---|---|---|
| ev_c8f36e146b2b | limitation | Формат старых событий неизменяем; новые поля добавляются только как необязательные | data/storage/st_event_sourcing.md | — | 25–25 | Ограничение: формат старых событий нельзя менять задним числом, новые поля добавляются только как необязательные [L-ST-12]. |
| ev_ddac17ad1ff0 | decision | Решение РС-7: бэкенд хранения чекпоинтов DocSearch — PostgreSQL 16, ответственный Ирина Ковалёва, срок перехода 20.11.2… | data/meetings/meeting_m12.md | — | 15–15 | Решение РС-7: бэкенд хранения чекпоинтов DocSearch - PostgreSQL 16; ответственный - Ирина Ковалёва; срок перехода - 20.11.2026. |
| ev_577b26899c75 | fact | Журнал событий копируют на резервный том каждые 4 часа | data/storage/st_snapshot_policy.md | — | 7–7 | Снимок журнала событий сохраняют на резервный том каждые четыре часа; полную копию базы - раз в сутки в 03:00. |
| ev_3dd8ade7f76c | limitation | На резервном томе держат не больше 14 суточных копий; копии старше двух недель удаляются автоматически без возможности … | data/storage/st_snapshot_policy.md | — | 15–17 | Ограничение: на резервном томе одновременно держат не больше 14 суточных копий [L-ST-20]. Копии старше двух недель удаляются автоматически без возможности восстановления [L-ST-21]. |
| ev_420c05639df1 | fact | Код NEWDOC-2026-B встречается в runbook RB-11 (data/ops/runbook_rb11.md), строка 9 | data/ops/runbook_rb11.md | — | 9–9 | восстановить снимок на узле es-new-01 (код NEWDOC-2026-B) |

## Расход

```json
{
  "tokens": {
    "spent": 2664404,
    "reserved": 0,
    "limit": 4000000,
    "of_which": {
      "input_tokens": 2570326,
      "output_tokens": 16230,
      "compaction_input_tokens": 67048,
      "compaction_output_tokens": 10800,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 200,
    "limit": 200
  },
  "compaction_calls": 9,
  "failed_calls": 0,
  "tool_calls": {
    "used": 191,
    "limit": 800
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
    "used": 506.9,
    "limit": 10800
  },
  "peak_input_tokens": 16989
}
```
