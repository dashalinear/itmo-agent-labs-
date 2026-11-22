# Частичный результат (задача не завершена): T1

- Статус: **paused** — LLM unavailable after 3 attempts: TimeoutError: 
- Вид задачи: local

## Запрос

Изучи документы в data/search по теме поиска: выдели подходы, ограничения и спорные утверждения. Сохраняй выдержки с источниками и постепенно собирай обзор. Обязательно найди решения архитектурного совета (АС-…) в справочнике, решение кафедры о метрике качества в конспекте курса и разбор инцидента INC-2026-017. После остановки продолжи с сохранённого места.

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
| data/search/ir_course_lectures.md | 90 | 0 | нет |
| data/search/search_engineering_handbook.md | 520 | 0 | нет |
| data/search/search_incident_reports.md | 45 | 0 | нет |

## Расход

```json
{
  "tokens": {
    "spent": 53317,
    "reserved": 0,
    "limit": 1500000,
    "of_which": {
      "input_tokens": 7298,
      "output_tokens": 54,
      "compaction_input_tokens": 0,
      "compaction_output_tokens": 0,
      "failed_call_tokens": 45965,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 1,
    "limit": 90
  },
  "compaction_calls": 0,
  "failed_calls": 4,
  "tool_calls": {
    "used": 2,
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
    "used": 12.9,
    "limit": 5400
  },
  "peak_input_tokens": 7298
}
```
