## Цель
Организовать файлы из `input/_batches/4d4e776d7c` в `data` по темам, добавить документы в корпус (L0, L1, L2 в Elasticsearch), провалидировать корпус и завершить задачу.

## Ограничения пользователя
- [C1] Организуй все файлы из папки input/_batches/4d4e776d7c в data по темам (по содержимому, тематические подпапки входа сохраняй, если они подходят)
- [C2] Добавь документы в корпус: каталог L0, структура и summary L1, тексты L2 в Elasticsearch
- [C3] Неизменённые документы, уже имеющие актуальные L1/L2, не обрабатывай повторно
- [C4] В конце проверь корпус validate_corpus и заверши задачу

## Решения и договорённости
- Загружен скилл `build_corpus_levels` для определения процедуры инжеста.
- Файлы распределены по тематическим подпапкам `data/`:
  - `data/search/`: bm25_basics.md, vector_search.md, hybrid_ranking.md
  - `data/storage/`: checkpoint_sqlite.md, checkpoint_postgres.md, redis_cache.md
  - `data/ops/`: monitoring.md, oncall.md, incident_101.md
  - `data/meetings/`: meeting_m05.md
- Все 10 файлов успешно перемещены, получены `document_id` и `content_hash`.
- Каталог L0 обновлён для всех 10 документов (title, description, note). Статус индексации: `not_indexed`.
- Проведено чтение всех 10 файлов с нумерацией строк для точного определения диапазонов секций.
- Успешно записаны L1 (структура и summary) для 5 документов:
  1. `bm25_basics.md` (id: `a401ef12-7eb0-4a47-976c-b1d6bead53b9`)
  2. `vector_search.md` (id: `5b92a5cc-0c13-4f40-97ef-52442f7af415`)
  3. `hybrid_ranking.md` (id: `bffbf9ae-845d-4b16-9b7d-ae3c6b8757a5`)
  4. `checkpoint_sqlite.md` (id: `63c545d8-6f2a-4bef-8a1b-c2e1144cbcc0`)
  5. `checkpoint_postgres.md` (id: `bd2ea05b-e77b-44a3-805f-c4e50aa2e667`)
- Для оставшихся 5 документов (`redis_cache.md`, `monitoring.md`, `oncall.md`, `incident_101.md`, `meeting_m05.md`) L1 не записаны.

## Подтверждённые факты (с источниками)
- **bm25_basics.md** (`data/search/bm25_basics.md`, id: `a401ef12-7eb0-4a47-976c-b1d6bead53b9`): BM25, k1=1.2, b=0.75, snowball. Секции: Принцип [1-5], Настройки стенда [7-9], Ограничения [11-13].
- **vector_search.md** (`data/search/vector_search.md`, id: `5b92a5cc-0c13-4f40-97ef-52442f7af415`): embeddings dim=384, HNSW m=16, ef_construction=100. Секции: Модель [1-5], Индекс HNSW [7-9], Ограничения [11-13].
- **hybrid_ranking.md** (`data/search/hybrid_ranking.md`, id: `bffbf9ae-845d-4b16-9b7d-ae3c6b8757a5`): RRF constant=60. Секции: Идея [1-5], Когда помогает [7-8], Ограничения [10-12].
- **checkpoint_sqlite.md** (`data/storage/checkpoint_sqlite.md`, id: `63c545d8-6f2a-4bef-8a1b-c2e1144cbcc0`): код ST-CHK-01, max checkpoint 2 МБ, запрет SMB. Секции: Назначение [1-6], Ограничения [8-11], Восстановление [13-15].
- **checkpoint_postgres.md** (`data/storage/checkpoint_postgres.md`, id: `bd2ea05b-e77b-44a3-805f-c4e50aa2e667`): решение РС-12, PostgreSQL 16, ответственный Мария Белова, срок