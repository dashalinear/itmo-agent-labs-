# Частичный результат (задача не завершена): A1

- Статус: **paused** — LLM unavailable after 3 attempts: OpenAIConnectionError: Connection error.
- Вид задачи: local

## Запрос

Изучи документы в data/search по теме поиска: выдели подходы, ограничения и спорные утверждения. Сохраняй выдержки с источниками и постепенно собирай обзор. Обязательно найди решения архитектурного совета (АС-…) в справочнике, решение кафедры о метрике качества в конспекте курса и разбор инцидента INC-2026-017. После остановки продолжи с сохранённого места. Три решения архитектурного совета сохрани выдержками в индекс Elasticsearch через MCP (skill index_excerpts: build_excerpts по найденным строкам справочника, затем upsert_excerpts) и проверь, что они находятся поиском search_excerpts.

## Ограничения пользователя

- [C1] Каждый проверяемый вывод обзора ссылается на документ и страницу через evidence.
- [C2] Использовать только документы из data/search, без интернета.
- [C3] Обзор не длиннее 1200 слов.
- [C4] Спорные утверждения — отдельным разделом, с аргументами за и против.
- [C5] Решения архитектурного совета (АС-01, АС-02, АС-03) привести дословно с номером страницы.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 0


## Приложение D. Покрытие локальных документов

| документ | страниц | прочитано стр. | поиск |
|---|---|---|---|
| data/search/ir_course_lectures.md | 90 | 1 | да |
| data/search/search_engineering_handbook.md | 520 | 3 | нет |
| data/search/search_incident_reports.md | 45 | 2 | да |

## Расход

```json
{
  "tokens": {
    "spent": 1185711,
    "reserved": 0,
    "limit": 1500000,
    "of_which": {
      "input_tokens": 839641,
      "output_tokens": 14263,
      "compaction_input_tokens": 140339,
      "compaction_output_tokens": 19200,
      "failed_call_tokens": 172268,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 62,
    "limit": 90
  },
  "compaction_calls": 16,
  "failed_calls": 13,
  "tool_calls": {
    "used": 234,
    "limit": 300
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
    "used": 365.5,
    "limit": 5400
  },
  "peak_input_tokens": 17992
}
```
