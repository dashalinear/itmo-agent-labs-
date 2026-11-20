## Цель
Экстрагировать все упоминания ограничений из 10 документов папки `storage` с указанием источников (документ, fragment_id, строки).

## Ограничения пользователя
- Просмотри все документы папки storage и перечисли все упоминания ограничений с источниками (документ, fragment_id, строки).

## Решения и договорённости
- Использовать последовательное чтение через `area_next` (батчи по 8 фрагментов).
- Фиксировать каждое упоминание через `record_mention` с цитатой и заметкой.
- Отслеживать прогресс по `fragments_total` (47 всего).
- Использовать `area_grep` для верификации полноты поиска (40 совпадений).

## Подтверждённые факты (с источниками)
- **JSONB < 1 МБ**: `data/storage/st_checkpoint_postgres.md`, frag `11b79fd6...`, строка 17, [L-ST-05], ev `ev_6f3629d37309`.
- **Пул соединений = 20**: `data/storage/st_checkpoint_postgres.md`, frag `6225b442...`, строка 25, ev `ev_c2b2dd0b597d`.
- **Таймаут при исчерпании пула**: `data/storage/st_checkpoint_postgres.md`, frag `6225b442...`, строка 27, [L-ST-06], ev `ev_ae000a02c413`.
- **Откат миграции невозможен**: `data/storage/st_checkpoint_postgres.md`, frag `ed0f165c...`, строка 37, [L-ST-07], ev `ev_1dfcd8b6000b`.
- **Один писатель в SQLite**: `data/storage/st_checkpoint_sqlite.md`, frag `d1af8d09...`, строка 17, [L-ST-01], ev `ev_9d32b5da4b49`.
- **Таймаут блокировки не бесконечный**: `data/storage/st_checkpoint_sqlite.md`, frag `7245ffda...`, строка 27, [L-ST-02], ev `ev_78d42db79464`.
- **Сетевые диски не поддерживают блокировки**: `data/storage/st_checkpoint_sqlite.md`, frag `7245ffda...`, строка 29, [L-ST-03], ev `ev_3c43bfeb14b2`.
- **Размер базы ≤ 2 ГБ**: `data/storage/st_checkpoint_sqlite.md`, frag `5871bd63...`, строка 39, [L-ST-04], ev `ev_c9383f123e17`.
- **Ключ шифрования не рядом с базой**: `data/storage/st_encryption_state.md`, frag `f4aa4b3e...`, строка 17, [L-ST-27], ev `ev_5aa1a51932f0`.
- **Доступ только у сервисной роли**: `data/storage/st_encryption_state.md`, frag `6f53f38e...`, строка 25, [L-ST-28], ev `ev_b0e5a9bab115`.
- **Токены/пароли не в состоянии**: `data/storage/st_encryption_state.md`, frag `6f53f38e...`, строка 27, [L-ST-29], ev `ev_59bb8d024da6`.
- **Линейный рост времени восстановления**: `data/storage/st_event_sourcing.md`, frag `98898177...`, строка 17, [L-ST-11], ev `ev_001a5ab357fd`.
- **Формат старых событий неизменяем**: `data/storage/st_event_sourcing.md`, frag `da281a19...`, строка 25, [L-ST-12], ev `ev_b624e7fdfe63`.
- **Обработчик читает все версии событий**: `data/storage/st_event_sourcing.md`, frag `da281a19...`, строка 27, [L-ST-13], ev `ev_f027df819d14`.
- **Чтение чужого пространства имён с разрешения**: `data/storage/st_memory_longterm.md`, frag `6caea44f...`, строка 17, [L-ST-14], ev `ev_8a761ed21f8e`.
- **Записи старше 180 дней не используются**: `data/storage/st_memory_longterm.md`, frag `a6a2a1f2...`, строка 27, [L-ST-15], ev `ev_3178970d1fcd`.

## Прогресс
(дополнено правилами: модель не дописала раздел) в сжатом фрагменте 3 вызовов tools: area_grep×2, area_next×1

## Открытые вопросы и следующий шаг
(дополнено правилами: модель не дописала раздел) по плану задачи

## Артефакты
(дополнено правилами: модель не дописала раздел) —