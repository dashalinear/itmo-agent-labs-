## Цель
Экстрагировать все упоминания ограничений из документов папки `storage` с указанием источников (документ, fragment_id, строки).

## Ограничения пользователя
*   Просмотри все документы папки storage и перечисли все упоминания ограничений с источниками (документ, fragment_id, строки).

## Решения и договорённости
*   Использовать инструменты `area_define`, `area_next`, `record_mention` для последовательного обхода фрагментов.
*   Фиксировать только прямые упоминания ограничений (constraints, limits, requirements).
*   Задача приостановлена из-за исчерпания бюджета вызовов модели (0 из 8), прогресс сохранен.

## Подтверждённые факты (с источниками)
*   **Postgres**: Атомарность (`st_checkpoint_postgres.md`, `f357c648...`, L9, `ev_e8e000476368`); JSONB < 1 МБ (`st_checkpoint_postgres.md`, `11b79fd6...`, L17, `ev_ff1b91ba3f32`); Пул 20 conn (`st_checkpoint_postgres.md`, `6225b442...`, L25, `ev_a7a36a01d73d`); Ожидание при исчерпании пула (`st_checkpoint_postgres.md`, `6225b442...`, L27, `ev_06130029d64e`); Откат миграции невозможен [L-ST-07] (`st_checkpoint_postgres.md`, `ed0f165c...`, L37, `ev_a45658c78e1e`); Проверка схемы на копии (`st_checkpoint_postgres.md`, `ed0f165c...`, L41, `ev_f3c7d24ebd67`).
*   **SQLite**: Один Writer [L-ST-01] (`st_checkpoint_sqlite.md`, `d1af8d09...`, L17, `ev_e4f641d72a3c`); Таймаут блокировки [L-ST-02] (`st_checkpoint_sqlite.md`, `7245ffda...`, L27, `ev_6ef2d1663728`); Нет сетевых дисков [L-ST-03] (`st_checkpoint_sqlite.md`, `7245ffda...`, L29, `ev_26809587aca0`); База ≤ 2 ГБ [L-ST-04] (`st_checkpoint_sqlite.md`, `5871bd63...`, L39, `ev_e0a535210067`); Проверка схемы (`st_checkpoint_sqlite.md`, `5871bd63...`, L43, `ev_b5d58ad56fd2`).
*   **Encryption**: Проверка схемы (`st_encryption_state.md`, `41d07f0d...`, L9, `ev_d39db5135e52`); Атомарность (`st_encryption_state.md`, `3a94a829...`, L11, `ev_7fbe33c7a344`); Ключ вне директории [L-ST-27] (`st_encryption_state.md`, `f4aa4b3e...`, L17, `ev_981e587af80d`).
*   **Event Sourcing**: Линейный рост восстановления [L-ST-11] (`st_event_sourcing.md`, `98898177...`, L17, `ev_b2e15c7648d9`); Атомарность (`st_event_sourcing.md`, `98898177...`, L21, `ev_1e3def788c18`); Формат событий неизменяем [L-ST-12] (`st_event_sourcing.md`, `da281a19...`, L25, `ev_c0398eb2a44f`); Обработчик читает все версии [L-ST-13] (`st_event_sourcing.md`, `da281a19...`, L27, `ev_b60228729fb0`); Атомарность (`st_event_sourcing.md`, `da281a19...`, L31, `ev_0feac7109b14`).
*   **Memory**: Чтение чужого NS с разрешением [L-ST-14] (`st_memory_longterm.md`, `6caea44f...`, L17, `ev_e454e1ecfed8`); Записи > 180 дней [L-ST-15] (`st_memory_longterm.md`, `a6a2a1f2...`, L27, `ev_ddc69922ccdc`).

## Прогресс
(дополнено правилами: модель не дописала раздел) по состоянию harness: evidence 44; ; разделы отчёта: нет; в сжатом фрагменте 9 вызовов tools: area_next×1, record_mention×8

## Открытые вопросы и следующий шаг
(дополнено правилами: модель не дописала раздел) по плану задачи

## Артефакты
(дополнено правилами: модель не дописала раздел) —