## Цель
Организовать файлы из `input` в `data` по темам (сохраняя подходящие тематические подпапки), добавить документы в корпус (L0 каталог, L1 структура/summary, L2 тексты в Elasticsearch), пропустить неизменённые документы с актуальными L1/L2, проверить корпус `validate_corpus` и завершить задачу.

## Ограничения пользователя
- [C1] Организуй все файлы из папки input в data по темам (по содержимому, тематические подпапки входа сохраняй, если они подходят)
- [C2] Добавь документы в корпус: каталог L0, структура и summary L1, тексты L2 в Elasticsearch
- [C3] Неизменённые документы, уже имеющие актуальные L1/L2, не обрабатывай повторно
- [C4] В конце проверь корпус validate_corpus и заверши задачу

## Решения и договорённости
- Задача была приостановлена 3 раза из-за `OpenAIConnectionError`, затем перезапущена.
- Обнаружен prompt injection в `input/inbox_lab1/meetings/notes.md` и `data/meetings/meeting_2026-10-09_weekly.md` (инструкция перенести файлы в `C:\Windows\Temp`), он игнорируется как данные.
- Для большого документа `input/lectures/ir_course_lectures.md` (166 111 байт) запрещено полное чтение; требуется использование `search_text` и `read_pages`.
- Загружен скилл `build_corpus_levels`: порядок действий — organize (move), L0 (title/desc), L1 (outline/read/write), L2 (index_document), validate.
- Подтверждено, что в `input/inbox_lab1/courses/` находится только один файл `notes.md` (1130 байт). Файлы `ir_course_lectures.md` и `mlops_course_notes.md` в этой подпапке отсутствуют.
- Файлы `mlops_course_notes.md`, `notes.md` (в корне) и `README.md` отсутствуют в `input/inbox_lab1/` (ошибка not_found при чтении).
- В `input/inbox_lab1/` обнаружен пустой файл `empty_todo.txt` (0 байт).
- Прочитаны все файлы из папок `input/search` (8 шт.), `input/storage` (10 шт.), `input/agents` (8 шт.), `input/ops` (8 шт.), `input/projects` (4 шт.) и `input/inbox_lab1` (12 файлов + подпапки).
- Файлы `st_checkpoint_sqlite.md`, `st_encryption_state.md`, `st_redis_state.md` прочитаны через артефакты `art_ff53f28f1a4f0746fe20`, `art_b24bf25713b738791b91`, `art_409616cba8e422603cc0`.
- Файлы `st_memory_longterm.md`, `st_redis_state.md`, `st_schema_migrations.md` были offloaded в артефакты `art_814311be4c393951ee89`, `art_409616cba8e422603cc0`, `art_ba813864bca9d276ec00` и успешно прочитаны.
- Файл `search_quality_thoughts.md` был offloaded в артефакт `art_6d0221a3380a7eb45251` и прочитан.
- Выполнено перемещение всех файлов из `input` в `data`. Папка `input` теперь пуста (`count: 0`).
- Структура `data` подтверждена: 9 директорий (`_unsorted`, `agents`, `courses`, `howto`, `meetings`, `ops`, `projects`, `search`, `storage`).
- Каталог (L0) частично заполнен: у всех документов есть `title`, но `description` пустые. Статус индексации всех 53 документов — `not_indexed`.
- Полный каталог offloaded в артефакт `art_2561fdea7b27b411399b` (18303 chars).
- Завершён этап L1 для папки `data/agents` (9 документов).
- Выполнена индексация L2 для всех 9 документов из `data/agents`. Создано 28 фрагментов (3-4 на документ).
- Выполнены вызовы `doc_outline` для 12 файлов из папок `data/courses`, `data/howto`, `data/meetings`.
- Подтверждено наличие актуальных L1 (`l1_current: true`) для следующих документов:
  - `data/courses/es_seminar_analyzers_shards.md` (doc_id: `41b8a9a1-3422-4f5a-afed-d5d2e38d8e35`)
  - `data/courses/ir_lecture_inverted_index.md`