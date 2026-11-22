# Итоговый отчёт: ING-2619a135a3

- Статус: **completed** — Один файл из input/_batches/2619a135a3 (query_rewrite.md) перенесён в data/search/ по содержимому (механизм переписывания поисковых запросов, код SRCH-RW-3). L0: каталог с title/description/note; L1: 2 секции (Идея, Ограничение) с summary; L2: 2 фрагмента в Elasticsearch; lab-1 excerpt (строки 3-8) 
- Вид задачи: ingest

## Запрос

Организуй все файлы из папки input/_batches/2619a135a3 в data по темам (по содержимому, тематические подпапки входа сохраняй, если они подходят) и добавь документы в корпус: каталог L0, структура и summary L1, тексты L2 в Elasticsearch. Неизменённые документы, уже имеющие актуальные L1/L2, не обрабатывай повторно. В конце проверь корпус validate_corpus и заверши задачу.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 0


## Критерии завершения

- OK input_folder_organized: 0 files left
- OK l1_l2_complete: need L1: 0, need L2: 0
- OK validation: 195 fragments checked, 0 problems

## Расход

```json
{
  "tokens": {
    "spent": 118204,
    "reserved": 0,
    "limit": 4000000,
    "of_which": {
      "input_tokens": 116484,
      "output_tokens": 1720,
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
    "used": 14,
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
    "used": 33.5,
    "limit": 5400
  },
  "peak_input_tokens": 12248
}
```
