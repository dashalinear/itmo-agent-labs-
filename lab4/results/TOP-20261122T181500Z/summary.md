## Цель
Выделить основные темы зафиксированного корпуса (17 документов), объединить близкие, подтвердить каждую цитатами из L2 и выполнить `submit_report`.

## Ограничения пользователя
- [C1] Выдели основные темы всего зафиксированного корпуса, объедини близкие темы и подтверди каждую цитатами из L2; затем submit_report.

## Решения и договорённости
- План: L1 всех 17 док. → группировка в 3–8 тем → L2-цитаты → `topic_upsert` + `merge` → `topics_status` + `submit_report`.
- Статус: шаг 1 (L1) завершён, шаг 2 (L2) в процессе.
- При сохранении цитат (`cite_fragment`) требуется дословное совпадение текста с фрагментом; при ошибке `quote_not_found` необходимо перечитать фрагмент (`read_fragment`) и повторить попытку с точным текстом.

## Подтверждённые факты (с источниками)
- Корпус: 17 док., `run_key: lab4-topics-run-2026-11-22T18:15:00Z`, `frozen_at: 2026-11-22T18:15:00+00:00` (tool: `frozen_documents`).
- `data/meetings/meeting_m05.md`: РС-12 (PostgreSQL 16, Мария Белова), РС-13 (grep для кодов, векторный для перефразов, Анна Ершова) (L2, frag `5979c9f1-0e91-55e0-aaa6-7938bb8fd807`, lines 6-8, evidence_id `ev_b5b208cfbcf5`).
- `data/ops/incident_101.md`: INC-2026-101, ST-CHK-01 дополнен запретом сетевых дисков (L2, frag `2d9887ba-ee74-5b34-8322-309cd0778d44`, lines 11-13, evidence_id `ev_55c9d566c00f`).
- `data/storage/checkpoint_postgres.md`: РС-12, PostgreSQL 16, срок 01.12.2026 (L2, frag `16a5d354-ac51-5d86-966f-00d599486d0d`, lines 7-9, evidence_id `ev_072a619ea9d6`).
- `data/storage/checkpoint_sqlite.md`: ST-CHK-01, один писатель, запрет SMB, ≤2 МБ (L2, frag `4930ecf5-950e-5cf8-b390-10213366255d`, lines 8-11, evidence_id `ev_e154c4e6aebb`).
- `data/storage/snapshot_policy.md`: снимки каждые 6 ч, хранится 8 последних (L2, frag `29905377-4e64-590b-92fa-55ddf0e9129d`, lines 1-5, evidence_id `ev_88ba33cec557`).
- `data/search/query_cache.md`: SRCH-CACHE-2, кэш 10 мин, лимит 10 000 запросов (L2, frag `3310f38a-b3e2-5adf-8ab1-683b59250854`, lines 1-4, evidence_id `ev_a21ed8f4bd34`).
- `data/search/query_rewrite.md`: SRCH-RW-3, переписывание моделью, лимит 300 символов (L2, frag `4958c19a-0f24-59f9-9ac7-85b281c09668`, lines 1-5, evidence_id `ev_9be1beeda171`).
- `data/search/vector_search.md`: embeddings dim=384, HNSW m=16, ef_construction=100 (L2, frag `8c46b6b9-2fec-51fc-8590-6a2cc06b2c73`, lines 1-5, evidence_id `ev_afb49cb0ea5f`).
- `data/search/hybrid_ranking.md`: RRF, константа 60 (L2, frag `61f872a1-1ede-58a7-a2d9-ec0d736d63a8`, lines 1-5, evidence_id `ev_4d27dac37de0`).
- `data/search/bm25_basics.md`: BM25, параметры k1, b (L2, frag `76eb41be-dbe6-5663-bf46-3b71d420cecc`, lines 1-5, evidence_id `ev_0d29c2772bcd`).
- `data/ops/latency_budget.md`: LAT-2026-07, 8 сек для 95% (L2, frag `ec67f2a4-d120-5971-b666-ad0428e989eb`, lines 1-5, evidence_id `ev_b928b164a98f`).
- `data/ops/monitoring.md`: метрики p95, ошибки tools, токены (L2, frag `b05d9461-82c0-5ec7-bd7d-f7df653fcfaa`, lines 1-4, evidence_id `ev_c0430e827364`).
- `data/ops/inference_energy.md`: 0,9 Вт·ч/запрос, 2,4 кВт сервер (L2, frag `7dc424b5-a893-5de6-84cb-70a20f248bad`, lines 1-5, evidence_id `ev_5c1dfe24d94a`).
- `data/ops/oncall.md`: смена неделя, ответ 15 мин (L2, frag `fd0e8493-5dce-503f-b8f4-a516b7e7d8ba`, lines 1-5, evidence_id `ev_d789c2f19f56`).

## Прогресс
(дополнено правилами: модель не дописала раздел) по состоянию harness: evidence 17; ; разделы отчёта: нет; в сжатом фрагменте 27 вызовов tools: cite_fragment×20, read_fragment×5, read_section×2

## Открытые вопросы и следующий шаг
(дополнено правилами: модель не дописала раздел) невыполненные шаги плана: Просмотреть L1 всех 17 документов; Сгруппировать в 3-8 тем; Прочитать L2 и собрать цитаты по темам; topic_upsert + merge; topics_status и submit_report

## Артефакты
(дополнено правилами: модель не дописала раздел) —