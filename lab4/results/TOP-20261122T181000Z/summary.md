## Цель
Выделить основные темы зафиксированного корпуса (16 документов), объединить близкие, подтвердить каждую цитатами из L2 и выполнить `submit_report`.

## Ограничения пользователя
*   [C1] Выдели основные темы всего зафиксированного корпуса, объедини близкие темы и подтверди каждую цитатами из L2; затем submit_report.

## Решения и договорённости
*   План: просмотр L1 всех 16 документов → группировка в 3–8 тем → чтение L2 для цитирования → `topic_upsert`/`merge` → `topics_status`/`submit_report`.
*   Статус: L1 просмотрен полностью. L2-цитирование завершено для всех ключевых документов (15 цитат сохранено).

## Подтверждённые факты (с источниками)
*   **M-05** (`data/meetings/meeting_m05.md`, L2 lines 7-8, `ev_e69b660a2f33`): РС-12 (PostgreSQL 16, отв. Мария Белова), РС-13 (grep для кодов, векторный поиск для перефразов, отв. Анна Ершова).
*   **Checkpoint Postgres** (`data/storage/checkpoint_postgres.md`, L2 lines 8-9, `ev_3594ff4b1002`): РС-12, PostgreSQL 16, срок 01.12.2026.
*   **Checkpoint SQLite** (`data/storage/checkpoint_sqlite.md`, L2 lines 9-10, `ev_fc8add65a311`): ST-CHK-01, один писатель, запрет SMB-дисков, лимит 2 МБ.
*   **INC-2026-101** (`data/ops/incident_101.md`, L2 lines 12-13, `ev_3023f2afe6a8`): Запрет сетевых дисков, переход на PostgreSQL по РС-12.
*   **Vector Search** (`data/search/vector_search.md`, L2 lines 4-5, `ev_276bb820c37b`): Embeddings dim=384, kNN косинус.
*   **Vector Search Limits** (`data/search/vector_search.md`, L2 lines 12-13, `ev_c172054e2838`): Плохо различает коды/версии, смена модели требует перестроения индекса.
*   **Query Cache** (`data/search/query_cache.md`, L2 line 4, `ev_8ba7719bef97`): SRCH-CACHE-2, введён по итогам M-05.
*   **Query Cache Details** (`data/search/query_cache.md`, L1): TTL 10 мин, лимит 10 000 запросов, риск устаревших данных после переиндексации.
*   **Redis Cache** (`data/storage/redis_cache.md`, L2 line 4, `ev_de8ebab708fe`): Кэш промежуточного состояния, приоритет скорости. L1: TTL 24ч, лимит 512 КБ, ключи `agent:{task_id}:step:{n}`.
*   **Snapshot Policy** (`data/storage/snapshot_policy.md`, L2 lines 4-5, `ev_41e489d1d1ab`): Снимки каждые 6 часов, хранение 8 последних. L1: потеря до 6 часов данных.
*   **Monitoring** (`data/ops/monitoring.md`, L2 line 4, `ev_fec6a144001f`): Метрики p95 задержки, ошибки tools, токены, очередь индексации.
*   **Latency Budget** (`data/ops/latency_budget.md`, L2 lines 4-5, `ev_0be67f2c126d`): LAT-2026-07, ответ за 8 секунд для 95% запросов.
*   **Onboarding** (`data/ops/onboarding_engineers.md`, L2 lines 4-5, `ev_978204523ff5`): ONB-2026-02, три модуля первой недели (harness, корпус L0/L1/L2, дежурства).
*   **Oncall** (`data/ops/oncall.md`, L2 lines 4-5, `ev_e48d3fcbbf29`): Недельные смены, передача в понедельник 10:00, ответ на алерт за 15 минут.
*   **Accessibility** (`data/ops/ui_accessibility.md`, L2 lines 4-5, `ev_...`): ACC-2026-01, WCAG 2.2 AA, контраст 4,5:1, клавиатурная доступность.
*   **Inference Energy** (`data/ops/inference_energy.md`, L2 lines 4-5): 0,9 Вт·ч на запрос (27 млрд параметров), 2,4 кВт на сервер с 4 GPU.
*   **BM25** (`data/search/bm25_basics.md`, L2 lines 4-5, `ev_62aebbbba379`): Ранжирование по частоте/редкости, параметры k1 и b.
*   **Hybrid Ranking** (`data/search/hybrid_ranking.md`, L2 lines 4-5, `ev_16991a55ab54`): RRF, константа 60.

## Прогресс
*   Просмотрены L1 всех 16 документов.
*   Прочитаны L2-фрагменты: M-05, checkpoint_postgres, checkpoint_sqlite, incident_101, vector_search (2), query_cache, monitoring, latency_budget, onboarding, oncall, bm25, hybrid, redis_cache, snapshot_policy, ui_accessibility, inference_energy.
*   Сохранено 15 цитат (`cite_fragment`).
*   Осталось: создать темы, `topic_upsert`, `submit_report`.

## Открытые вопросы и следующий шаг
*   Сгруппировать в темы: 1) Хранение чекпоинтов (SQLite/Postgres/Redis/Snapshots), 2) Поиск (BM25/Vector/Hybrid/Cache), 3) Ops/Надежность (Latency/Monitoring/Incident/Energy), 4) Process/People (Onboarding/Oncall/Accessibility).
*   Выполнить `topic_upsert` и `submit_report`.

## Артефакты
*   `run_key`: `lab4-topics-run-2026-11-22T18:10:00Z`