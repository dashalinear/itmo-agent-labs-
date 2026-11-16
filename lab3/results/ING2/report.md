# Итоговый отчёт: ING2

- Статус: **completed** — 3 файла из input/new_files разложены по темам (agents, search, ops), для каждого записаны L0 (каталог), L1 (структура + summary) и L2 (7 фрагментов в ES). validate_corpus: ok, 56 документов, 323 фрагмента, проблем нет.
- Вид задачи: ingest

## Запрос

Организуй все файлы из папки input/new_files в data по темам (по содержимому, тематические подпапки входа сохраняй, если они подходят) и добавь документы в корпус: каталог L0, структура и summary L1, тексты L2 в Elasticsearch. Неизменённые документы, уже имеющие актуальные L1/L2, не обрабатывай повторно. В конце проверь корпус validate_corpus и заверши задачу.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 0


## Критерии завершения

- ✔ input_folder_organized: 0 files left
- ✔ l1_l2_complete: need L1: 0, need L2: 0
- ✔ validation: 323 fragments checked, 0 problems

## Расход

```json
{
  "tokens": {
    "spent": 100325,
    "reserved": 0,
    "limit": 8000000,
    "of_which": {
      "input_tokens": 97812,
      "output_tokens": 2513,
      "compaction_input_tokens": 0,
      "compaction_output_tokens": 0,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 9,
    "limit": 450
  },
  "compaction_calls": 0,
  "failed_calls": 0,
  "tool_calls": {
    "used": 21,
    "limit": 2000
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
    "used": 31.7,
    "limit": 10800
  },
  "peak_input_tokens": 13710
}
```
