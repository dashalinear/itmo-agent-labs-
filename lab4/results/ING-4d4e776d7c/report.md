# Итоговый отчёт: ING-4d4e776d7c

- Статус: **completed** — Все 10 файлов из input/_batches/4d4e776d7c разложены по темам (search, storage, ops, meetings), каталог L0 заполнен, L1 (структура + summary) записаны для всех 10, L2 (30 фрагментов) проиндексированы в Elasticsearch. validate_corpus: ok, 0 проблем.
- Вид задачи: ingest

## Запрос

Организуй все файлы из папки input/_batches/4d4e776d7c в data по темам (по содержимому, тематические подпапки входа сохраняй, если они подходят) и добавь документы в корпус: каталог L0, структура и summary L1, тексты L2 в Elasticsearch. Неизменённые документы, уже имеющие актуальные L1/L2, не обрабатывай повторно. В конце проверь корпус validate_corpus и заверши задачу.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 0


## Критерии завершения

- OK input_folder_organized: 0 files left
- OK l1_l2_complete: need L1: 0, need L2: 0
- OK validation: 30 fragments checked, 0 problems

## Расход

```json
{
  "tokens": {
    "spent": 193833,
    "reserved": 0,
    "limit": 4000000,
    "of_which": {
      "input_tokens": 158747,
      "output_tokens": 10444,
      "compaction_input_tokens": 21042,
      "compaction_output_tokens": 3600,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 13,
    "limit": 250
  },
  "compaction_calls": 3,
  "failed_calls": 0,
  "tool_calls": {
    "used": 80,
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
    "used": 121.9,
    "limit": 5400
  },
  "peak_input_tokens": 15221
}
```
