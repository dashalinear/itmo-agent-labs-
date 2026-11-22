# История разработки лабораторной 1

В `README.md` описано текущее состояние проекта и финальный прогон 22.11.2026. Здесь я собрала то, что
менялось по дороге: какие прогоны были, что в них сломалось и что я исправила. Трассы прежних прогонов
`run_all_demos.ps1` удаляет, поэтому факты ниже взяты из моих записей и из тех трасс, пока они лежали в `traces/`.

## Прогоны

| Дата | Что запускалось | Чем закончилось |
|---|---|---|
| 15.11.2026 | первая цепочка демо-сессий | в main, ходы 13–14, модель отказалась вызывать tool сама |
| 20.11.2026 | пробные прогоны цепочки из шести сессий | проблемы с пометкой «20.11» в списке ниже |
| 21.11.2026, 00:30 | полная цепочка после исправлений | все проверки прошли; 14 документов, 8 `indexed`, 17 выдержек |
| 22.11.2026, 18:27 | цепочка после правок промпта и скриптов | текущий результат, описан в README |

Прогон 21.11 по сессиям: main `session_20261121_003012_b75948` (16 ходов, 52 вызова модели), limit
`session_20261121_003359_12b0aa` (2 / 27), guard `session_20261121_003438_d4b94d` (17 / 34), errors
`session_20261121_003555_3a28b8` (4 / 14), mcp_down `session_20261121_003731_a7266f` (3 / 12), retry
`session_20261121_003801_e59103` (4 / 13). Тогда системный промпт ещё подсказывал имена папок
(`courses`, `projects`, `meetings`, `howto`), и модель переименовала два одноимённых `notes.md` в
`data/courses/es_seminar_analyzers_shards.md` и `data/meetings/meeting_2026-10-09_docsearch_weekly.md`.

## Проблемы и исправления

### Модель отказывалась вызывать tool (15.11)
В main реплики 13–14 просят вызвать `head` с `n=0` и `read` с `../../secret.txt`. Модель ответила сама,
без вызова. Я добавила отдельную сессию guard (`demo/dialog_guard_ru.txt`): каждая её реплика прямо требует
вызвать инструмент и показать его ответ. В main эти ходы остались, в прогонах 21.11 и 22.11 tool вызывался.

### Ошибки MCP записывались как успех (20.11)
`ToolGuardMiddleware` ставит `ToolMessage.status = "error"`, если ответ начинается с `{"ok": false`.
Сначала проверялся только компактный JSON, а MCP-сервер отдаёт JSON с отступами. Ответы `es_unavailable`
попали в трассу как `status: success`. Теперь `_FAILED_RX` в `organizer/middleware.py` допускает пробелы и
переводы строк. Тест: `test_failed_tool_result_has_error_status` (оба формата).

### `files_scanned: -1` в `grep -r` (20.11)
Пропущенная ссылка наружу вычиталась из счётчика просмотренных файлов. В сессии guard `grep -r input`
(в `input` только ссылка) вернул `files_scanned: -1`. Теперь пропущенная ссылка не вычитается, и в
прогонах 21.11 и 22.11 там `files_scanned: 0`. Сам пропуск ссылки проверяет `test_junction_escape_blocked_windows`.

### `recursion_limit` срабатывал раньше лимита вызовов модели
Один вызов модели занимает 4 super-step. При `recursion_limit=100` цикл из 25 вызовов упирался в него,
а не в `ModelCallLimitMiddleware`. Я поставила 110. Тест: `test_default_limits_let_model_call_limit_fire_first`.

### Пустой ответ модели (20.11)
В пробной сессии mcp_down модель закончила ход пустым текстом. CLI теперь подставляет пометку
`empty_answer_note` (`organizer/cli.py`): сколько было `output_tokens` и какие tools вернули ошибку.
Тест: `test_empty_model_answer_gets_a_note`. В прогонах 21.11 и 22.11 пометка не понадобилась.

### Проверка выдержек для заглушек MCP (20.11)
Когда MCP-сервер не стартовал, `CatalogSyncMiddleware` всё равно проверял выдержки перед `upsert_excerpts`.
Модель несколько раз получала `excerpt_validation_failed` и переделывала выдержки, которые некуда было
записать. Теперь заглушки в `organizer/mcp_client.py` помечены `metadata={"mcp_unavailable": True}`, проверка
для них пропускается, и заглушка сразу отвечает `mcp_unavailable`. Тест: `test_mcp_server_unavailable_session`.

### Заглушки без схемы аргументов (20.11)
Модель вызвала заглушку `upsert_excerpts` без аргументов. Связать вызов с документом было нечем, и
инструкция по git осталась `not_indexed` вместо `error`. Теперь у заглушек те же обязательные аргументы, что у
сервера (`excerpts`, `document_id`), а строку JSON в `excerpts` разбирает `CatalogSyncMiddleware._after`.
Тест: `test_mcp_stub_requires_excerpts`. В 21.11 и 22.11 модель передала выдержки строкой JSON, и статус стал `error`.

### Пункт сводки про mcp_down
`scripts/summarize_run.py` отмечал пункт «статус `error` после mcp_down» по любому документу с `error`.
20.11 он прошёл из-за семинара ReAct, который получил ошибку от ES в сессии errors. Теперь сводка ищет
документ, у которого в `index_error` сказано про недоступный MCP-сервер, и называет его.

### Категория в индексе после переноса
Если `update_document_path` вызывали без `category`, индекс сохранял категорию старой папки, а каталог уже
показывал новую. Теперь сервер всегда берёт категорию из папки `data/<category>/`, `CatalogSyncMiddleware`
сверяет и путь, и категорию, а `scripts/verify.py` сравнивает категорию каждой выдержки с каталогом. Тесты:
`test_update_path_without_category_follows_folder`, `test_store_update_path_derives_category_offline`,
`test_catalog_sync_checks_category_after_path_update`, `test_verify_flags_category_drift`.

### Опечатка модели в тексте выдержки (20.11)
Модель три раза подряд получила `excerpt_validation_failed`, потому что вписала в выдержку текст с опечаткой.
Это была ошибка модели, а не кода: проверка сработала как надо. Промпт и `skills/index_excerpts/SKILL.md`
требуют передавать выдержки из `build_excerpts` без изменений. Тесты проверки:
`test_catalog_sync_validates_and_records`, `test_catalog_sync_rejects_excerpt_with_wrong_category`.

### Параллельные `move`
Модель часто переносит много файлов одним шагом, и ToolNode выполняет эти вызовы параллельно.
Запись каталога теперь идёт под блокировкой (`organizer/catalog.py`). На Windows `Path.resolve()` для папки,
которую в этот момент создаёт другой поток, может вернуть путь с префиксом `\\?\`, и проверка
принадлежности workspace ошибочно считала его внешним. `_plain` в `organizer/pathguard.py` снимает префикс.
Тесты: `test_parallel_moves_from_one_folder_all_catalogued`, `test_extended_length_prefix_is_not_treated_as_outside`,
`test_parallel_moves_into_a_new_folder_all_succeed`.

## Правки перед прогоном 22.11
- Системный промпт больше не подсказывает имена папок: модель выбирает их по содержимому файлов.
  В прогоне 22.11 она выбрала `courses`, `howto`, `meetings`, `projects` и `_unsorted`, а одноимённые
  `notes.md` оставила под своими именами в разных папках. `fixtures/EXPECTED.md` я обновила под этот результат.
- Ответ на поиск должен цитировать текст выдержки в «», путь, строки и `document_id`
  (`organizer/prompts.py`, `skills/index_excerpts/SKILL.md`).
- В событие `harness_wait` CLI пишет только имя файла-маркера, без пути к папке пользователя.
- `run_all_demos.ps1` делает `git commit` и `git push` только с ключом `-Push`, без него результаты только
  записываются.
