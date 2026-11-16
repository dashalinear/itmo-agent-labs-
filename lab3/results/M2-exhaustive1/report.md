# Итоговый отчёт: M2-exhaustive1

- Статус: **completed** — Найдено подтверждений: 40 в 10 документах: data/storage/st_checkpoint_postgres.md — 4; data/storage/st_checkpoint_sqlite.md — 5; data/storage/st_encryption_state.md — 4; data/storage/st_event_sourcing
- Вид задачи: exhaustive

## Запрос

Сделай полный проход по папке storage (все 10 документов: st_checkpoint_sqlite.md, st_checkpoint_postgres.md, st_redis_state.md, st_event_sourcing.md, st_snapshot_policy.md, st_encryption_state.md и остальные). Найди ВСЕ упоминания ограничений — это строки, содержащие слово «Ограничение» или коды вида L-ST-NN, а также связанные с ними ограничения (таймауты, лимиты, запреты). Для каждого найденного ограничения сообщи: путь к документу, document_id, fragment_id, номер строки, точную цитату (10..600 символов) и код ограничения (L-ST-NN) если есть. Перечисли ВСЕ без top-k, в порядке документов.

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 40

| id | тип | вывод | источник | стр. | строки | цитата |
|---|---|---|---|---|---|---|
| ev_59cff02d6a40 | limitation | L-ST-07: запрет/невозможность отката миграции без потери чекпоинтов (строка 37, st_checkpoint_postgres.md) | data/storage/st_checkpoint_postgres.md | — | 37–37 | Откат миграции после записи новых чекпоинтов невозможен без потери этих чекпоинтов [L-ST-07]. |
| ev_71e18b7366f9 | limitation | L-ST-06: исчерпание пула соединений → ожидание и таймаут (строка 27, st_checkpoint_postgres.md) | data/storage/st_checkpoint_postgres.md | — | 27–27 | Ограничение: при исчерпании пула новые шаги агента ждут свободного соединения и могут упереться в таймаут [L-ST-06]. |
| ev_96b3c317a7b9 | limitation | L-ST-05: лимит размера значения JSONB < 1 МБ (строка 17, st_checkpoint_postgres.md) | data/storage/st_checkpoint_postgres.md | — | 17–17 | Ограничение: размер одного значения в столбце JSONB лучше держать меньше 1 МБ, большие артефакты выносят в объектное хранилище [L-ST-05]. |
| ev_c10e0410f011 | limitation | Лимит: 20 соединений пула на рабочий процесс (строка 25, st_checkpoint_postgres.md) | data/storage/st_checkpoint_postgres.md | — | 25–25 | Пул соединений ограничивают 20 соединениями на рабочий процесс. |
| ev_d40809c0e1a0 | limitation | L-ST-01: один писатель в SQLite, второй ждёт блокировку (строка 17, st_checkpoint_sqlite.md) | data/storage/st_checkpoint_sqlite.md | — | 17–17 | Ограничение: одновременно пишет только один процесс; второй писатель ждёт освобождения блокировки [L-ST-01]. |
| ev_15cf931b1614 | limitation | L-ST-29: запрет записи секретов в состояние/трассы (строка 27, st_encryption_state.md) | data/storage/st_encryption_state.md | — | 27–27 | Ограничение: токены и пароли не записываются ни в состояние, ни в трассы [L-ST-29]. |
| ev_1e1941e103f9 | limitation | L-ST-27: запрет хранения ключа рядом с базой (строка 17, st_encryption_state.md) | data/storage/st_encryption_state.md | — | 17–17 | Ограничение: ключ шифрования нельзя хранить в той же директории, что и базу [L-ST-27]. |
| ev_21c97de13245 | limitation | L-ST-03: запрет сетевых дисков для SQLite (строка 29, st_checkpoint_sqlite.md) | data/storage/st_checkpoint_sqlite.md | — | 29–29 | Сетевые диски не поддерживают надёжные блокировки файлов SQLite, поэтому базу держат только на локальном диске [L-ST-03]. |
| ev_2928bbb85e8e | limitation | Таймаут блокировки 5 секунд (строка 25, st_checkpoint_sqlite.md) | data/storage/st_checkpoint_sqlite.md | — | 25–25 | Ошибка ERR_LOCK_TIMEOUT возникает, если блокировка не освобождается за 5 секунд; тикет CKPT-7731 описывает воспроизведение. |
| ev_2e1d02b0b882 | limitation | L-ST-04: лимит размера базы 2 ГБ (строка 39, st_checkpoint_sqlite.md) | data/storage/st_checkpoint_sqlite.md | — | 39–39 | Ограничение: размер одной базы на учебном стенде не должен превышать 2 ГБ [L-ST-04]. |
| ev_6a0537881bad | limitation | L-ST-30: обязательная проверка трасс на утечку секретов (строка 29, st_encryption_state.md) | data/storage/st_encryption_state.md | — | 29–29 | Трассы перед публикацией проверяются на утечку секретов автоматически [L-ST-30]. |
| ev_719f10156340 | limitation | L-ST-02: запрет бесконечного таймаута блокировки (строка 27, st_checkpoint_sqlite.md) | data/storage/st_checkpoint_sqlite.md | — | 27–27 | Ограничение: таймаут блокировки нельзя сделать бесконечным, иначе зависший процесс остановит всю задачу [L-ST-02]. |
| ev_778431eb3606 | limitation | L-ST-28: доступ только сервисной роли (строка 25, st_encryption_state.md) | data/storage/st_encryption_state.md | — | 25–25 | Ограничение: доступ к хранилищу состояния есть только у сервисной роли агента [L-ST-28]. |
| ev_02ba2c40e039 | limitation | L-ST-14: запрет чтения чужих пространств имён (строка 17, st_memory_longterm.md) | data/storage/st_memory_longterm.md | — | 17–17 | Ограничение: агент не может читать пространство имён другого пользователя без явного разрешения [L-ST-14]. |
| ev_3302257c5950 | limitation | L-ST-11: линейный рост времени восстановления, нужны снимки (строка 17, st_event_sourcing.md) | data/storage/st_event_sourcing.md | — | 17–17 | Ограничение: время восстановления растёт линейно с длиной журнала, поэтому периодически делают снимок [L-ST-11]. |
| ev_69c02cab9d54 | limitation | L-ST-13: обязательная поддержка всех версий событий (строка 27, st_event_sourcing.md) | data/storage/st_event_sourcing.md | — | 27–27 | Обработчик обязан уметь читать все версии событий, которые остались в журнале [L-ST-13]. |
| ev_98fda84e44e4 | limitation | L-ST-16: факты требуют сверки с документом (строка 29, st_memory_longterm.md) | data/storage/st_memory_longterm.md | — | 29–29 | Факты из памяти не считаются подтверждёнными, пока их не сверили с документом [L-ST-16]. |
| ev_c8431bd83792 | limitation | L-ST-12: запрет изменения формата старых событий (строка 25, st_event_sourcing.md) | data/storage/st_event_sourcing.md | — | 25–25 | Ограничение: формат старых событий нельзя менять задним числом, новые поля добавляются только как необязательные [L-ST-12]. |
| ev_e75c9dd59335 | limitation | L-ST-15: лимит 180 дней для записей памяти (строка 27, st_memory_longterm.md) | data/storage/st_memory_longterm.md | — | 27–27 | Ограничение: записи старше 180 дней не используются в ответах без повторной проверки [L-ST-15]. |
| ev_a2e224a43e50 | limitation | Лимит хранения трасс 60 дней (строка 9, st_retention_policy.txt) | data/storage/st_retention_policy.txt | — | 9–9 | Трассы выполнения хранятся 60 дней в архиве трасс. |
| ev_396e66ca18a3 | limitation | L-ST-31: продление хранения не более 90 дней (строка 7, st_retention_policy.txt) | data/storage/st_retention_policy.txt | — | 7–7 | Ограничение: продлить хранение чекпоинтов можно только по заявке владельца задачи, не более чем до 90 дней [L-ST-31]. |
| ev_6dc1eb091c2d | limitation | Лимит хранения чекпоинтов 30 дней (строка 6, st_retention_policy.txt) | data/storage/st_retention_policy.txt | — | 6–6 | Чекпоинты завершённых задач хранятся 30 дней, затем удаляются вместе с ожидающими записями. |
| ev_7d427a9464d5 | limitation | L-ST-32: секретные трассы удаляются через 7 дней (строка 10, st_retention_policy.txt) | data/storage/st_retention_policy.txt | — | 10–10 | Ограничение: трассы с пометкой "секрет" не архивируются и удаляются через 7 дней [L-ST-32]. |
| ev_9592d8a7eb3f | limitation | L-ST-08: потеря до 1 секунды записей при everysec (строка 17, st_redis_state.md) | data/storage/st_redis_state.md | — | 17–17 | Ограничение: режим appendfsync everysec допускает потерю последней секунды записей [L-ST-08]. |
| ev_e43ca79ad72f | limitation | L-ST-10: maxmemory и noeviction для чекпоинтов (строка 27, st_redis_state.md) | data/storage/st_redis_state.md | — | 27–27 | При превышении maxmemory политика вытеснения удаляет ключи, поэтому для чекпоинтов используют noeviction [L-ST-10]. |
| ev_eab68c1fb955 | limitation | L-ST-33: запрет выгрузки трасс за пределы контура (строка 11, st_retention_policy.txt) | data/storage/st_retention_policy.txt | — | 11–11 | Выгрузка трасс за пределы контура запрещена без согласования с безопасностью [L-ST-33]. |
| ev_f96fae57f726 | limitation | L-ST-09: данные должны помещаться в RAM (строка 25, st_redis_state.md) | data/storage/st_redis_state.md | — | 25–25 | Ограничение: весь набор данных должен помещаться в оперативную память узла [L-ST-09]. |
| ev_098282df477e | limitation | L-ST-36: archived-статус, обращения возвращают ошибку (строка 18, st_retention_policy.txt) | data/storage/st_retention_policy.txt | — | 18–18 | Перед удалением задача получает статус archived, и обращения к её состоянию возвращают ошибку [L-ST-36]. |
| ev_0a8a9506d106 | limitation | L-ST-26: запрет запуска старого кода на мигрированной базе (строка 29, st_schema_migrations.md) | data/storage/st_schema_migrations.md | — | 29–29 | Старый код нельзя запускать на мигрированной базе: он не понимает новые поля [L-ST-26]. |
| ev_0cd8fd6275e0 | limitation | L-ST-35: запрет удаления данных running-задач (строка 17, st_retention_policy.txt) | data/storage/st_retention_policy.txt | — | 17–17 | Ограничение: ночное задание не удаляет данные задач со статусом running [L-ST-35]. |
| ev_8a1a71e6971d | limitation | L-ST-34: лимит 5 ГБ на артефакты задачи (строка 14, st_retention_policy.txt) | data/storage/st_retention_policy.txt | — | 14–14 | Ограничение: общий объём артефактов одной задачи не должен превышать 5 ГБ [L-ST-34]. |
| ev_9c2355c1151a | limitation | L-ST-24: запрет запуска новых задач во время миграции (строка 25, st_schema_migrations.md) | data/storage/st_schema_migrations.md | — | 25–25 | Ограничение: во время миграции новые задачи не запускаются [L-ST-24]. |
| ev_d746d62e796f | limitation | L-ST-25: пакеты миграции не более 10 000 записей (строка 27, st_schema_migrations.md) | data/storage/st_schema_migrations.md | — | 27–27 | Ограничение: миграция длинной истории выполняется пакетами не больше 10 000 записей [L-ST-25]. |
| ev_ecff2752739b | limitation | L-ST-23: переименование поля требует миграции (строка 17, st_schema_migrations.md) | data/storage/st_schema_migrations.md | — | 17–17 | Ограничение: переименование поля требует миграции всех сохранённых чекпоинтов [L-ST-23]. |
| ev_0c872d65a370 | limitation | L-ST-18: смена модели требует пересчёта векторов (строка 17, st_vector_memory.md) | data/storage/st_vector_memory.md | — | 17–17 | Ограничение: смена модели эмбеддингов требует пересчёта всех векторов памяти [L-ST-18]. |
| ev_1b77aa96c558 | limitation | L-ST-20: не более 14 суточных копий (строка 15, st_snapshot_policy.md) | data/storage/st_snapshot_policy.md | — | 15–15 | Ограничение: на резервном томе одновременно держат не больше 14 суточных копий [L-ST-20]. |
| ev_32bedcb4fb80 | limitation | L-ST-17: нет гарантии точного совпадения (строка 15, st_vector_memory.md) | data/storage/st_vector_memory.md | — | 15–15 | Ограничение: векторный поиск не гарантирует точного совпадения кодов и чисел [L-ST-17]. |
| ev_c8aeeea8ec49 | limitation | L-ST-19: лимит 2 млн векторов на учебном стенде (строка 27, st_vector_memory.md) | data/storage/st_vector_memory.md | — | 27–27 | Ограничение: на учебном стенде индекс памяти ограничен 2 миллионами векторов [L-ST-19]. |
| ev_dd32a744af9a | limitation | L-ST-22: до 40 минут на восстановление, запись чекпоинтов запрещена (строка 27, st_snapshot_policy.md) | data/storage/st_snapshot_policy.md | — | 27–27 | Ограничение: восстановление полной копии занимает до 40 минут, в это время запись новых чекпоинтов запрещена [L-ST-22]. |
| ev_fb08a94b563c | limitation | L-ST-21: копии старше 2 недель удаляются безвозвратно (строка 17, st_snapshot_policy.md) | data/storage/st_snapshot_policy.md | — | 17–17 | Копии старше двух недель удаляются автоматически без возможности восстановления [L-ST-21]. |

## Расход

```json
{
  "tokens": {
    "spent": 300524,
    "reserved": 0,
    "limit": 700000,
    "of_which": {
      "input_tokens": 268108,
      "output_tokens": 9171,
      "compaction_input_tokens": 19645,
      "compaction_output_tokens": 3600,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 24,
    "limit": 70
  },
  "compaction_calls": 3,
  "failed_calls": 0,
  "tool_calls": {
    "used": 57,
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
    "used": 131.7,
    "limit": 10800
  },
  "peak_input_tokens": 17313
}
```
