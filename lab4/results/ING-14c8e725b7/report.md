# Итоговый отчёт: ING-14c8e725b7

- Статус: **completed** — Организован 1 файл: input/_batches/14c8e725b7/onboarding_engineers.md -> data/ops/onboarding_engineers.md (документ f2367418). Написан L1 (3 секции), проиндексирован L2 (3 фрагмента), сохранён 1 ключевой excerpt в MCP-индекс (проверен поиском ONB-2026-02). validate_corpus: ok, 24 документа, 193 фраг
- Вид задачи: ingest

## Запрос

Организуй все файлы из папки input/_batches/14c8e725b7 в data по темам (по содержимому, тематические подпапки входа сохраняй, если они подходят) и добавь документы в корпус: каталог L0, структура и summary L1, тексты L2 в Elasticsearch. Неизменённые документы, уже имеющие актуальные L1/L2, не обрабатывай повторно. В конце проверь корпус validate_corpus и заверши задачу.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 0


## Критерии завершения

- OK input_folder_organized: 0 files left
- OK l1_l2_complete: need L1: 0, need L2: 0
- OK validation: 193 fragments checked, 0 problems

## Расход

```json
{
  "tokens": {
    "spent": 131094,
    "reserved": 0,
    "limit": 4000000,
    "of_which": {
      "input_tokens": 129310,
      "output_tokens": 1784,
      "compaction_input_tokens": 0,
      "compaction_output_tokens": 0,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 13,
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
    "used": 28.4,
    "limit": 5400
  },
  "peak_input_tokens": 12514
}
```
