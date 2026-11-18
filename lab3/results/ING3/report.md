# Итоговый отчёт: ING3

- Статус: **completed** — Папка input пуста — новых файлов нет. Корпус уже актуален: 56 документов, 55 с L1 (пустой файл data/_unsorted/empty_todo.txt не индексируется), 323 фрагмента L2, validate_corpus ok=true, проблем 0. Повторная обработка не требовалась.
- Вид задачи: ingest

## Запрос

Организуй все файлы из папки input в data по темам (по содержимому, тематические подпапки входа сохраняй, если они подходят) и добавь документы в корпус: каталог L0, структура и summary L1, тексты L2 в Elasticsearch. Неизменённые документы, уже имеющие актуальные L1/L2, не обрабатывай повторно. В конце проверь корпус validate_corpus и заверши задачу.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 0


## Критерии завершения

- OK input_folder_organized: 0 files left
- OK l1_l2_complete: need L1: 0, need L2: 0
- OK validation: 323 fragments checked, 0 problems

## Расход

```json
{
  "tokens": {
    "spent": 39894,
    "reserved": 0,
    "limit": 8000000,
    "of_which": {
      "input_tokens": 39540,
      "output_tokens": 354,
      "compaction_input_tokens": 0,
      "compaction_output_tokens": 0,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 5,
    "limit": 450
  },
  "compaction_calls": 0,
  "failed_calls": 0,
  "tool_calls": {
    "used": 6,
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
    "used": 7.8,
    "limit": 10800
  },
  "peak_input_tokens": 8467
}
```
