# Частичный результат (задача не завершена): EXP1

- Статус: **paused** — budget exhausted before the call (tokens: needed 13242, remaining 9957 of 700000)
- Вид задачи: exhaustive

## Запрос

Просмотри все документы папки storage и перечисли все упоминания ограничений с источниками (документ, fragment_id, строки).

## Обзор

_Разделы обзора ещё не написаны._

## Приложение A. Подтверждения (evidence): 38

| id | тип | вывод | источник | стр. | строки | цитата |
|---|---|---|---|---|---|---|
| ev_0a72f2e58268 | limitation | Ограничение L-ST-06: исчерпание пула соединений → ожидание/таймаут (st_checkpoint_postgres.md, строка 27) | data/storage/st_checkpoint_postgres.md | — | 27–27 | Ограничение: при исчерпании пула новые шаги агента ждут свободного соединения и могут упереться в таймаут [L-ST-06]. |
| ev_2b7e15332a3c | limitation | Ограничение L-ST-01: в SQLite одновременно пишет только один процесс (st_checkpoint_sqlite.md, строка 17) | data/storage/st_checkpoint_sqlite.md | — | 17–17 | Ограничение: одновременно пишет только один процесс; второй писатель ждёт освобождения блокировки [L-ST-01]. |
| ev_9b013178cc7a | limitation | Ограничение L-ST-05: размер значения JSONB < 1 МБ (st_checkpoint_postgres.md, строка 17) | data/storage/st_checkpoint_postgres.md | — | 17–17 | Ограничение: размер одного значения в столбце JSONB лучше держать меньше 1 МБ, большие артефакты выносят в объектное хранилище [L-ST-05]. |
| ev_c2932a405828 | limitation | Ограничение L-ST-07: откат миграции после записи чекпоинтов невозможен без их потери (st_checkpoint_postgres.md, строка… | data/storage/st_checkpoint_postgres.md | — | 37–37 | Откат миграции после записи новых чекпоинтов невозможен без потери этих чекпоинтов [L-ST-07]. |
| ev_f75b2324e12d | limitation | Лимит пула соединений: 20 на рабочий процесс (st_checkpoint_postgres.md, строка 25) | data/storage/st_checkpoint_postgres.md | — | 25–25 | Пул соединений ограничивают 20 соединениями на рабочий процесс. |
| ev_1a0e489b6af7 | limitation | Ограничение L-ST-29: токены и пароли не записываются в состояние/трассы (st_encryption_state.md, строка 27) | data/storage/st_encryption_state.md | — | 27–27 | Ограничение: токены и пароли не записываются ни в состояние, ни в трассы [L-ST-29]. |
| ev_1b14bd3e879f | limitation | Ограничение L-ST-03: сетевые диски не поддерживают надёжные блокировки SQLite (st_checkpoint_sqlite.md, строка 29) | data/storage/st_checkpoint_sqlite.md | — | 29–29 | Сетевые диски не поддерживают надёжные блокировки файлов SQLite, поэтому базу держат только на локальном диске [L-ST-03]. |
| ev_3e79158a3706 | limitation | Ограничение L-ST-02: таймаут блокировки нельзя сделать бесконечным (st_checkpoint_sqlite.md, строка 27) | data/storage/st_checkpoint_sqlite.md | — | 27–27 | Ограничение: таймаут блокировки нельзя сделать бесконечным, иначе зависший процесс остановит всю задачу [L-ST-02]. |
| ev_4b23c9ea68ab | limitation | Ограничение L-ST-04: размер базы ≤ 2 ГБ на учебном стенде (st_checkpoint_sqlite.md, строка 39) | data/storage/st_checkpoint_sqlite.md | — | 39–39 | Ограничение: размер одной базы на учебном стенде не должен превышать 2 ГБ [L-ST-04]. |
| ev_5eb04bc2e94d | limitation | Лимит: таймаут блокировки 5 секунд, ошибка ERR_LOCK_TIMEOUT (st_checkpoint_sqlite.md, строка 25) | data/storage/st_checkpoint_sqlite.md | — | 25–25 | Ошибка ERR_LOCK_TIMEOUT возникает, если блокировка не освобождается за 5 секунд; тикет CKPT-7731 описывает воспроизведение. |
| ev_a140d476cf6a | limitation | Требование L-ST-30: автоматическая проверка трасс на утечку секретов (st_encryption_state.md, строка 29) | data/storage/st_encryption_state.md | — | 29–29 | Трассы перед публикацией проверяются на утечку секретов автоматически [L-ST-30]. |
| ev_b471d6f741d2 | limitation | Ограничение L-ST-28: доступ к хранилищу только у сервисной роли агента (st_encryption_state.md, строка 25) | data/storage/st_encryption_state.md | — | 25–25 | Ограничение: доступ к хранилищу состояния есть только у сервисной роли агента [L-ST-28]. |
| ev_f8e9a669cc6e | limitation | Ограничение L-ST-27: ключ шифрования нельзя хранить в той же директории, что и базу (st_encryption_state.md, строка 17) | data/storage/st_encryption_state.md | — | 17–17 | Ограничение: ключ шифрования нельзя хранить в той же директории, что и базу [L-ST-27]. |
| ev_281ccb7dff0b | limitation | Ограничение L-ST-16: факты из памяти не подтверждены до сверки с документом (st_memory_longterm.md, строка 29) | data/storage/st_memory_longterm.md | — | 29–29 | Факты из памяти не считаются подтверждёнными, пока их не сверили с документом [L-ST-16]. |
| ev_598aa5e29c37 | limitation | Требование L-ST-13: обработчик обязан читать все версии событий в журнале (st_event_sourcing.md, строка 27) | data/storage/st_event_sourcing.md | — | 27–27 | Обработчик обязан уметь читать все версии событий, которые остались в журнале [L-ST-13]. |
| ev_955baf090962 | limitation | Ограничение L-ST-15: записи старше 180 дней не используются без повторной проверки (st_memory_longterm.md, строка 27) | data/storage/st_memory_longterm.md | — | 27–27 | Ограничение: записи старше 180 дней не используются в ответах без повторной проверки [L-ST-15]. |
| ev_c48a24ae1e98 | limitation | Ограничение L-ST-14: чтение чужого пространства имён только с явного разрешения (st_memory_longterm.md, строка 17) | data/storage/st_memory_longterm.md | — | 17–17 | Ограничение: агент не может читать пространство имён другого пользователя без явного разрешения [L-ST-14]. |
| ev_f90052b0bb4e | limitation | Ограничение L-ST-12: формат старых событий нельзя менять задним числом (st_event_sourcing.md, строка 25) | data/storage/st_event_sourcing.md | — | 25–25 | Ограничение: формат старых событий нельзя менять задним числом, новые поля добавляются только как необязательные [L-ST-12]. |
| ev_fcda45f99201 | limitation | Ограничение L-ST-11: время восстановления растёт линейно с длиной журнала (st_event_sourcing.md, строка 17) | data/storage/st_event_sourcing.md | — | 17–17 | Ограничение: время восстановления растёт линейно с длиной журнала, поэтому периодически делают снимок [L-ST-11]. |
| ev_2374bae5ce43 | limitation | Ограничение L-ST-31: продление хранения чекпоинтов только по заявке, не более 90 дней (st_retention_policy.txt, строка … | data/storage/st_retention_policy.txt | — | 7–7 | Ограничение: продлить хранение чекпоинтов можно только по заявке владельца задачи, не более чем до 90 дней [L-ST-31]. |
| ev_2e893c511fe6 | limitation | Ограничение L-ST-08: appendfsync everysec допускает потерю последней секунды записей (st_redis_state.md, строка 17) | data/storage/st_redis_state.md | — | 17–17 | Ограничение: режим appendfsync everysec допускает потерю последней секунды записей [L-ST-08]. |
| ev_5a3f87dd41d8 | limitation | Ограничение L-ST-32: секретные трассы не архивируются, удаляются через 7 дней (st_retention_policy.txt, строка 10) | data/storage/st_retention_policy.txt | — | 10–10 | Ограничение: трассы с пометкой "секрет" не архивируются и удаляются через 7 дней [L-ST-32]. |
| ev_6270ee9817a2 | limitation | Ограничение L-ST-33: выгрузка трасс за пределы контура запрещена без согласования с безопасностью (st_retention_policy.… | data/storage/st_retention_policy.txt | — | 11–11 | Выгрузка трасс за пределы контура запрещена без согласования с безопасностью [L-ST-33]. |
| ev_c06b69f9d677 | limitation | Ограничение L-ST-09: весь набор данных должен помещаться в оперативную память узла (st_redis_state.md, строка 25) | data/storage/st_redis_state.md | — | 25–25 | Ограничение: весь набор данных должен помещаться в оперативную память узла [L-ST-09]. |
| ev_e7766966b719 | limitation | Ограничение L-ST-10: при превышении maxmemory вытеснение удаляет ключи, для чекпоинтов используют noeviction (st_redis_… | data/storage/st_redis_state.md | — | 27–27 | При превышении maxmemory политика вытеснения удаляет ключи, поэтому для чекпоинтов используют noeviction [L-ST-10]. |
| ev_01c89f1e19ef | limitation | Ограничение L-ST-23: переименование поля требует миграции всех чекпоинтов (st_schema_migrations.md, строка 17) | data/storage/st_schema_migrations.md | — | 17–17 | Ограничение: переименование поля требует миграции всех сохранённых чекпоинтов [L-ST-23]. |
| ev_3abe4c7409a2 | limitation | Ограничение L-ST-34: общий объём артефактов одной задачи не более 5 ГБ (st_retention_policy.txt, строка 14) | data/storage/st_retention_policy.txt | — | 14–14 | Ограничение: общий объём артефактов одной задачи не должен превышать 5 ГБ [L-ST-34]. |
| ev_9f9658ad5ad3 | limitation | Ограничение L-ST-35: ночное задание не удаляет данные задач со статусом running (st_retention_policy.txt, строка 17) | data/storage/st_retention_policy.txt | — | 17–17 | Ограничение: ночное задание не удаляет данные задач со статусом running [L-ST-35]. |
| ev_b96c331bf425 | limitation | Ограничение L-ST-25: миграция длинной истории пакетами не более 10 000 записей (st_schema_migrations.md, строка 27) | data/storage/st_schema_migrations.md | — | 27–27 | Ограничение: миграция длинной истории выполняется пакетами не больше 10 000 записей [L-ST-25]. |
| ev_c57e34b1b99c | limitation | Ограничение L-ST-36: после archived обращения к состоянию возвращают ошибку (st_retention_policy.txt, строка 18) | data/storage/st_retention_policy.txt | — | 18–18 | Перед удалением задача получает статус archived, и обращения к её состоянию возвращают ошибку [L-ST-36]. |
| ev_da1e74453783 | limitation | Ограничение L-ST-26: старый код нельзя запускать на мигрированной базе (st_schema_migrations.md, строка 29) | data/storage/st_schema_migrations.md | — | 29–29 | Старый код нельзя запускать на мигрированной базе: он не понимает новые поля [L-ST-26]. |
| ev_ecaf1cdabfc6 | limitation | Ограничение L-ST-24: во время миграции новые задачи не запускаются (st_schema_migrations.md, строка 25) | data/storage/st_schema_migrations.md | — | 25–25 | Ограничение: во время миграции новые задачи не запускаются [L-ST-24]. |
| ev_35e9fbe725e4 | limitation | Ограничение L-ST-20: на резервном томе не более 14 суточных копий (st_snapshot_policy.md, строка 15) | data/storage/st_snapshot_policy.md | — | 15–15 | Ограничение: на резервном томе одновременно держат не больше 14 суточных копий [L-ST-20]. |
| ev_5bcdfc9a257d | limitation | Ограничение L-ST-17: векторный поиск не гарантирует точного совпадения кодов и чисел (st_vector_memory.md, строка 15) | data/storage/st_vector_memory.md | — | 15–15 | Ограничение: векторный поиск не гарантирует точного совпадения кодов и чисел [L-ST-17]. |
| ev_5dc0bfe2dc3f | limitation | Ограничение L-ST-22: восстановление полной копии до 40 минут, запись чекпоинтов запрещена (st_snapshot_policy.md, строк… | data/storage/st_snapshot_policy.md | — | 27–27 | Ограничение: восстановление полной копии занимает до 40 минут, в это время запись новых чекпоинтов запрещена [L-ST-22]. |
| ev_77a08fc753a1 | limitation | Ограничение L-ST-19: на учебном стенде индекс памяти ограничен 2 млн векторов (st_vector_memory.md, строка 27) | data/storage/st_vector_memory.md | — | 27–27 | Ограничение: на учебном стенде индекс памяти ограничен 2 миллионами векторов [L-ST-19]. |
| ev_9480053248c1 | limitation | Ограничение L-ST-21: копии старше двух недель удаляются без возможности восстановления (st_snapshot_policy.md, строка 1… | data/storage/st_snapshot_policy.md | — | 17–17 | Копии старше двух недель удаляются автоматически без возможности восстановления [L-ST-21]. |
| ev_ce1de31b1dd6 | limitation | Ограничение L-ST-18: смена модели эмбеддингов требует пересчёта всех векторов (st_vector_memory.md, строка 17) | data/storage/st_vector_memory.md | — | 17–17 | Ограничение: смена модели эмбеддингов требует пересчёта всех векторов памяти [L-ST-18]. |

## Расход

```json
{
  "tokens": {
    "spent": 690043,
    "reserved": 0,
    "limit": 700000,
    "of_which": {
      "input_tokens": 638393,
      "output_tokens": 17339,
      "compaction_input_tokens": 28311,
      "compaction_output_tokens": 6000,
      "failed_call_tokens": 0,
      "interrupted_call_tokens": 0
    }
  },
  "model_calls": {
    "used": 62,
    "limit": 120
  },
  "compaction_calls": 5,
  "failed_calls": 0,
  "tool_calls": {
    "used": 94,
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
    "used": 248.1,
    "limit": 10800
  },
  "peak_input_tokens": 16885
}
```
