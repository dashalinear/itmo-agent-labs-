# Итоговый отчёт: ING-578a8801b8

- Статус: **completed** — Организованы 3 файла из input/_batches/578a8801b8: inference_energy.md -> data/ops, query_cache.md -> data/search, snapshot_policy.md -> data/storage. Для всех записаны L1 (title, description, summary, секции) и проиндексирован L2 в Elasticsearch (по 3 фрагмента, всего 9). validate_corpus: ok, 14 до
- Вид задачи: ingest

## Запрос

Организуй все файлы из папки input/_batches/578a8801b8 в data по темам (по содержимому, тематические подпапки входа сохраняй, если они подходят) и добавь документы в корпус: каталог L0, структура и summary L1, тексты L2 в Elasticsearch. Неизменённые документы, уже имеющие актуальные L1/L2, не обрабатывай повторно. В конце проверь корпус validate_corpus и заверши задачу.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 0


## Критерии завершения

- ✔ input_folder_organized: 0 files left
- ✔ l1_l2_complete: need L1: 0, need L2: 0
- ✔ validation: 55 fragments checked, 0 problems

## Расход

```json
{
  "tokens": {
    "spent": 146418,
    "reserved": 0,
    "limit": 4000000,
    "of_which": {
      "input_tokens": 143591,
      "output_tokens": 2827,
      "compaction_input_tokens": 0,
      "compaction_output_tokens": 0,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 12,
    "limit": 250
  },
  "compaction_calls": 0,
  "failed_calls": 0,
  "tool_calls": {
    "used": 27,
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
    "used": 32.8,
    "limit": 5400
  },
  "peak_input_tokens": 16319
}
```
