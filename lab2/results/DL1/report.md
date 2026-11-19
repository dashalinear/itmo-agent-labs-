# Частичный результат (задача не завершена): DL1

- Статус: **paused** — budget exhausted: task deadline reached (task_deadline_s: needed 47.8, remaining 0 of 45)
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
| data/search/ir_course_lectures.md | 90 | 0 | да |
| data/search/search_engineering_handbook.md | 520 | 0 | да |
| data/search/search_incident_reports.md | 45 | 0 | да |

## Расход

```json
{
  "tokens": {
    "spent": 160975,
    "reserved": 0,
    "limit": 1500000,
    "of_which": {
      "input_tokens": 141843,
      "output_tokens": 3775,
      "compaction_input_tokens": 12957,
      "compaction_output_tokens": 2400,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 11,
    "limit": 90
  },
  "compaction_calls": 2,
  "failed_calls": 0,
  "tool_calls": {
    "used": 27,
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
    "used": 56.6,
    "limit": 45
  },
  "peak_input_tokens": 18119
}
```
