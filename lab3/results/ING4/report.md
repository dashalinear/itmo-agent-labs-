# Итоговый отчёт: ING4

- Статус: **completed** — Все 3 файла из input/new_files2 разложены по темам (data/agents, data/ops, data/search), для каждого создан L1 (title/description/summary/sections), L2 фрагменты и эмбеддинги загружены в Elasticsearch (по 3 фрагмента), по одному ключевому excerpt с кодом NEWDOC-2026-* сохранён в MCP-индекс и найден 
- Вид задачи: ingest

## Запрос

Организуй все файлы из папки input/new_files2 в data по темам (по содержимому, тематические подпапки входа сохраняй, если они подходят) и добавь документы в корпус: каталог L0, структура и summary L1, тексты L2 в Elasticsearch. Неизменённые документы, уже имеющие актуальные L1/L2, не обрабатывай повторно. В конце проверь корпус validate_corpus и заверши задачу.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 0


## Критерии завершения

- OK input_folder_organized: 0 files left
- OK l1_l2_complete: need L1: 0, need L2: 0
- OK validation: 332 fragments checked, 0 problems

## Расход

```json
{
  "tokens": {
    "spent": 165759,
    "reserved": 0,
    "limit": 8000000,
    "of_which": {
      "input_tokens": 154109,
      "output_tokens": 3775,
      "compaction_input_tokens": 6675,
      "compaction_output_tokens": 1200,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 13,
    "limit": 450
  },
  "compaction_calls": 1,
  "failed_calls": 0,
  "tool_calls": {
    "used": 33,
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
    "used": 57.9,
    "limit": 10800
  },
  "peak_input_tokens": 17472
}
```
