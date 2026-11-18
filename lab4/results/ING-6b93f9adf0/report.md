# Итоговый отчёт: ING-6b93f9adf0

- Статус: **completed** — Организован 1 файл из input/_batches/6b93f9adf0: latency_budget.md -> data/ops/ (SLO-требование LAT-2026-07). Написан L1 (3 секции), проиндексирован L2 (3 фрагмента в ES), каталог обновлён. validate_corpus: ok, 19 документов, 125 фрагментов, проблем нет.
- Вид задачи: ingest

## Запрос

Организуй все файлы из папки input/_batches/6b93f9adf0 в data по темам (по содержимому, тематические подпапки входа сохраняй, если они подходят) и добавь документы в корпус: каталог L0, структура и summary L1, тексты L2 в Elasticsearch. Неизменённые документы, уже имеющие актуальные L1/L2, не обрабатывай повторно. В конце проверь корпус validate_corpus и заверши задачу.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 0


## Критерии завершения

- OK input_folder_organized: 0 files left
- OK l1_l2_complete: need L1: 0, need L2: 0
- OK validation: 125 fragments checked, 0 problems

## Расход

```json
{
  "tokens": {
    "spent": 99430,
    "reserved": 0,
    "limit": 4000000,
    "of_which": {
      "input_tokens": 98241,
      "output_tokens": 1189,
      "compaction_input_tokens": 0,
      "compaction_output_tokens": 0,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 11,
    "limit": 250
  },
  "compaction_calls": 0,
  "failed_calls": 0,
  "tool_calls": {
    "used": 11,
    "limit": 1500
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
    "used": 17.5,
    "limit": 5400
  },
  "peak_input_tokens": 10417
}
```
