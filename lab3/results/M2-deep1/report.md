# Частичный результат (задача не завершена): M2-deep1

- Статус: **paused** — agent ended the turn without complete_task
- Вид задачи: deep

## Запрос

Исследуй в корпусе itmo-lab3 все бэкенды хранения состояния агентов (state/checkpoint storage). Нужно: 1) перечислить все описанные бэкенды (например SQLite, PostgreSQL, другие — ищи в папке storage и по всему корпусу: ст_*.md, docsearch_architecture.md, протоколы совещаний, runbook'и); 2) какой бэкенд выбран для DocSearch (решение РС-7, PostgreSQL 16) и кто ответственный (Ирина Ковалёва, срок 20.11.2026) — подтвердить цитатами; 3) для каждого бэкенда собрать ограничения из текстов (коды L-ST-*, ERR_*, таймауты, лимиты, блокировки, например ERR_LOCK_TIMEOUT/CKPT-7731 для SQLite, лимиты копий L-ST-20/21/22, шифрование L-ST-27/28, версии событий L-ST-12/13 и т.п.). Для каждого вывода дай document_id, fragment_id, строки и точную цитату. Отчет структурируй по бэкендам.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 9

| id | тип | вывод | источник | стр. | строки | цитата |
|---|---|---|---|---|---|---|
| ev_67435a120e8e | fact | В архитектуре DocSearch выбор бэкенда чекпоинтов ссылается на решение РС-7 протокола M-12 | data/projects/docsearch_architecture.md | — | 15–15 | Выбор бэкенда хранения чекпоинтов утверждён решением РС-7 (см. протокол совещания M-12). |
| ev_aedd8b238764 | decision | Для DocSearch выбран PostgreSQL 16 (решение РС-7), ответственный Ирина Ковалёва, срок 20.11.2026 | data/meetings/meeting_m12.md | — | 15–15 | Решение РС-7: бэкенд хранения чекпоинтов DocSearch - PostgreSQL 16; ответственный - Ирина Ковалёва; срок перехода - 20.11.2026. |
| ev_2241060540dc | limitation | SQLite: один писатель, L-ST-01 | data/storage/st_checkpoint_sqlite.md | — | 17–17 | Ограничение: одновременно пишет только один процесс; второй писатель ждёт освобождения блокировки [L-ST-01]. |
| ev_fd8af12acd07 | limitation | SQLite: ERR_LOCK_TIMEOUT при 5-секундной блокировке, тикет CKPT-7731 | data/storage/st_checkpoint_sqlite.md | — | 25–25 | Ошибка ERR_LOCK_TIMEOUT возникает, если блокировка не освобождается за 5 секунд; тикет CKPT-7731 описывает воспроизведение. |
| ev_dc70d4667a59 | limitation | SQLite: L-ST-02 (бесконечный таймаут недопустим), L-ST-03 (только локальный диск) | data/storage/st_checkpoint_sqlite.md | — | 27–29 | Ограничение: таймаут блокировки нельзя сделать бесконечным, иначе зависший процесс остановит всю задачу [L-ST-02]. Сетевые диски не поддерживают надёжные блокировки файлов SQLite, поэтому базу держат… |
| ev_f9b317e8332e | limitation | SQLite: бэкап через backup API, лимит 2 ГБ (L-ST-04) | data/storage/st_checkpoint_sqlite.md | — | 37–39 | Копию базы делают через backup API, а не простым копированием файла во время записи. Ограничение: размер одной базы на учебном стенде не должен превышать 2 ГБ [L-ST-04]. |
| ev_0aa01522c5c6 | limitation | PostgreSQL: пул 20 соединений на процесс, таймауты (L-ST-06) | data/storage/st_checkpoint_postgres.md | — | 25–27 | Пул соединений ограничивают 20 соединениями на рабочий процесс. Ограничение: при исчерпании пула новые шаги агента ждут свободного соединения и могут упереться в таймаут [L-ST-06]. |
| ev_69c57c7d0c7b | limitation | PostgreSQL: схема checkpoints/checkpoint_writes, JSONB < 1 МБ (L-ST-05) | data/storage/st_checkpoint_postgres.md | — | 15–17 | Чекпоинты хранятся в таблице checkpoints с ключом (thread_id, checkpoint_id), ожидающие записи - в checkpoint_writes. Ограничение: размер одного значения в столбце JSONB лучше держать меньше 1 МБ, бо… |
| ev_b75a2d71c6bd | limitation | PostgreSQL: миграции до запуска, откат невозможен (L-ST-07) | data/storage/st_checkpoint_postgres.md | — | 35–37 | Миграции схемы применяются до запуска новых рабочих процессов. Откат миграции после записи новых чекпоинтов невозможен без потери этих чекпоинтов [L-ST-07]. |

## Расход

```json
{
  "tokens": {
    "spent": 388140,
    "reserved": 0,
    "limit": 450000,
    "of_which": {
      "input_tokens": 369133,
      "output_tokens": 3872,
      "compaction_input_tokens": 12735,
      "compaction_output_tokens": 2400,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 30,
    "limit": 40
  },
  "compaction_calls": 2,
  "failed_calls": 0,
  "tool_calls": {
    "used": 44,
    "limit": 160
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
    "used": 93.5,
    "limit": 10800
  },
  "peak_input_tokens": 16961
}
```
