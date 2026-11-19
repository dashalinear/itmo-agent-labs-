# Частичный результат (задача не завершена): M5-deep1

- Статус: **paused** — budget exhausted before the call (model_calls: needed 1, remaining 0 of 60)
- Вид задачи: deep

## Запрос

Исследуй в корпусе все подходы к хранению состояния (state/memory) агентов. Задача:

1. Найди ВСЕ документы, где описаны подходы к хранению состояния/памяти агентов (state, memory, persistence, storage, checkpoint, compaction, context window, etc.). Ищи в папках agents, projects, meetings, search, storage, courses — везде, где есть упоминания. Используй search_fulltext (L1 и L2), search_vector (L1 и L2), search_grep для точных терминов (state, memory, persistence, checkpoint, compaction, context, storage, состояние, память, хранение).

2. Для каждого найденного подхода:
   - Как он называется и в каком документе описан (document_id, path)
   - Краткое описание механизма
   - Ограничения (лимиты, потери данных, стоимость, сложность)
   - Цитаты из L2-текста (fragment_id, lines) как подтверждение

3. Определи, какой подход выбран для проекта DocSearch:
   - Ищи в meetings (kickoff, weekly, retro, M-12, M-13), projects, agents
   - Найди явные решения/решения о выборе (decision)
   - Подтверди цитатами из L2

4. Сравни подходы по ограничениям (таблица или список)

5. Выпиши открытые вопросы (open questions) — что в корпусе НЕ подтверждено, что осталось неясным, противоречия между документами.

Формат ответа: структурированный отчёт с разделами, каждый факт — с document_id, fragment_id, lines. Отдельный раздел "Открытые вопросы".

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 2

| id | тип | вывод | источник | стр. | строки | цитата |
|---|---|---|---|---|---|---|
| ev_92026907c483 | decision | Резервные копии состояния DocSearch хранятся на отдельном томе backup-02 (2 ТБ) — решение РС-9, M-13 | data/meetings/meeting_m13.md | — | 7–7 | Решение РС-9: резервные копии хранятся на отдельном томе backup-02, объём 2 ТБ. |
| ev_f73c292cf34f | decision | Для DocSearch выбран PostgreSQL 16 как бэкенд хранения чекпоинтов (решение РС-7, совещание M-12) | data/meetings/meeting_m12.md | — | 15–15 | Решение РС-7: бэкенд хранения чекпоинтов DocSearch - PostgreSQL 16; ответственный - Ирина Ковалёва; срок перехода - 20.11.2026. |

## Расход

```json
{
  "tokens": {
    "spent": 791129,
    "reserved": 0,
    "limit": 1200000,
    "of_which": {
      "input_tokens": 752728,
      "output_tokens": 7879,
      "compaction_input_tokens": 25722,
      "compaction_output_tokens": 4800,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 60,
    "limit": 60
  },
  "compaction_calls": 4,
  "failed_calls": 0,
  "tool_calls": {
    "used": 112,
    "limit": 240
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
    "used": 209.9,
    "limit": 10800
  },
  "peak_input_tokens": 16745
}
```
