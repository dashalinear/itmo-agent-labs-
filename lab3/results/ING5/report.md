# Итоговый отчёт: ING5

- Статус: **completed** — Папка input/new_files_repeat пуста: harness уже удалил 3 файла с дублирующимся содержимым (agent_memory_compression.md, query_rewriting.md, runbook_rb11.md — они уже в корпусе). Новых файлов для организации нет. Корпус: 59 документов, 58 с L1, 332 фрагментов, validate_corpus ok=true, проблем 0.
- Вид задачи: ingest

## Запрос

Организуй все файлы из папки input/new_files_repeat в data по темам (по содержимому, тематические подпапки входа сохраняй, если они подходят) и добавь документы в корпус: каталог L0, структура и summary L1, тексты L2 в Elasticsearch. Неизменённые документы, уже имеющие актуальные L1/L2, не обрабатывай повторно. В конце проверь корпус validate_corpus и заверши задачу. Уже были в корпусе с тем же содержимым и убраны harness из входной папки (не индексируй их): input/new_files_repeat/agent_memory_compression.md = data/agents/agent_memory_compression.md; input/new_files_repeat/query_rewriting.md = data/search/query_rewriting.md; input/new_files_repeat/runbook_rb11.md = data/ops/runbook_rb11.md.

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
    "spent": 41353,
    "reserved": 0,
    "limit": 8000000,
    "of_which": {
      "input_tokens": 40991,
      "output_tokens": 362,
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
    "used": 9.0,
    "limit": 10800
  },
  "peak_input_tokens": 8728
}
```
