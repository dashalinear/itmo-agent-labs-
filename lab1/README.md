# Лабораторная 1. Агент-организатор файлов: LangGraph ReAct, tools, MCP, skills

Я сделала диалогового помощника, который раскладывает файлы из `workspace/input/` по тематическим папкам `workspace/data/`.
Он ведёт каталог `workspace/catalog.json` со стабильными `document_id`, а по запросу сохраняет точные выдержки
с метаданными в Elasticsearch через MCP-сервер. Каталог и индекс выдержек дальше пойдут на вход лабораторной № 3.
Как проект менялся и какие ошибки я по дороге исправила, записано в [docs/history.md](docs/history.md).

## 1. Архитектура

```
 ┌──────────────── CLI (organizer/cli.py) ──────────────────────────────┐
 │ HumanMessage → agent.astream(stream_mode="updates")  → трасса JSONL+MD│
 │ thread_id = id сессии, InMemorySaver checkpointer                     │
 └───────────────┬──────────────────────────────────────────────────────┘
                 ▼
 ┌──────── langchain.agents.create_agent (готовый ReAct на LangGraph) ──┐
 │  model (ChatOpenAI → qwen3.8-27b, ИТМО)  ⇄  tools (ToolNode)          │
 │  middleware: ModelCallLimit (лимит вызовов модели за ход),            │
 │              ToolGuard (таймаут / ошибки → наблюдение / лимит вывода),│
 │              CatalogSync (проверка выдержек и статус индексации)      │
 └──────┬──────────────────────────────┬────────────────────────────────┘
        │ 12 локальных tools           │ 6 MCP tools (stdio)
        ▼                              ▼
 workspace/ (Workspace guard)    mcp_es/server.py (FastMCP) ──► Elasticsearch 9.5.5
  input/  data/  catalog.json      upsert/search/update_path/...      индекс itmo_lab1_excerpts
```

| Каталог / файл | Назначение |
|---|---|
| `organizer/agent.py`, `prompts.py`, `cli.py` | сборка агента, системный промпт, диалоговый CLI и трассы |
| `organizer/tools_fs.py`, `tools_web.py`, `pathguard.py` | файловые tools, `web_fetch`, границы workspace |
| `organizer/catalog.py`, `tools_catalog.py`, `skills.py` | каталог, `build_excerpts`, `load_skill` |
| `organizer/middleware.py`, `mcp_client.py` | `ToolGuard`, `CatalogSync`, подключение MCP и заглушки |
| `mcp_es/`, `skills/*/SKILL.md` | MCP-сервер над Elasticsearch; skills `organize_files` и `index_excerpts` |
| `fixtures/`, `demo/`, `scripts/`, `traces/`, `results/` | тестовые файлы, сценарии, скрипты, трассы и итоги прогона |

`create_react_agent` в документации помечен устаревшим, поэтому я взяла `langchain.agents.create_agent`, ту же
библиотечную фабрику ReAct-агента на LangGraph.

## 2. Установка и запуск

Нужны Python ≥ 3.11 (я работала на 3.13.7), Docker Desktop и сеть ИТМО: LLM-сервер `10.32.2.11` виден только из неё.
Версии зафиксированы в `requirements.txt` (lock в `requirements.lock.txt`): langchain 1.5.0, langgraph 1.2.22,
langchain-openai 1.8.0, langchain-mcp-adapters 0.3.2, mcp 1.30.0, elasticsearch-py 9.5.1; Elasticsearch 9.5.5 в Docker.

```powershell
cd lab1
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # впишите LLM_API_KEY (личный токен; .env в .gitignore)
docker compose up -d            # Elasticsearch 9.5.5 на http://localhost:9200
python scripts/prepare_workspace.py --reset-index   # workspace/input = fixtures/input, пустая data/, чистый индекс
python -m organizer.cli         # диалоговый CLI
```

Команды CLI: `/help`, `/history`, `/check` (пары tool_call/tool_result и дубли), `/catalog`, `/graph`, `/exit`
(также `/quit`, `выход`, Ctrl+Z/Ctrl+C). Новые файлы во время сессии CLI сам не копирует: их кладёт в
`workspace/input/` пользователь (во втором терминале `python scripts/add_late_files.py` или через Проводник) и
пишет «Я добавил файлы, разбери новые», а агент находит их повторным `ls`. В режиме `--script` для этого есть строка
`@wait_for_files input/a.md ...`: CLI ждёт, пока файлы появятся и их размер перестанет меняться (`--wait-timeout`,
по умолчанию 600 с), и на это время пишет файл-маркер `--wait-marker`. В трассу попадает только имя маркера.

```powershell
python -m organizer.cli --script demo/dialog_ru.txt
#   когда CLI напишет "[пауза сценария]", во втором терминале: python scripts/add_late_files.py
python scripts/verify.py          # детерминированные проверки → results/verify_report.json
python scripts/export_index.py    # results/index_export.jsonl, catalog.json, data_tree.txt, workspace_snapshot/
python scripts/restore_index.py   # восстановить индекс из results/index_export.jsonl без LLM
```

Все трассы и все `results/` пересоздаются одной командой из корня репозитория (только из сети ИТМО):

```powershell
powershell -ExecutionPolicy Bypass -File lab1\scripts\run_all_demos.ps1 -Push
```

Скрипт запускает `pytest` и тесты выхода через ссылки (`results/symlink_test.txt`), сбрасывает workspace и индекс,
записывает шесть сессий (раздел 9), потом `verify`, экспорт, `results/RUN_SUMMARY.md` и поиск токена в результатах.
С `-Push` он в конце коммитит только `lab1/traces` и `lab1/results` и делает `git push`; без `-Push` только
записывает результаты. Если `verify` не прошёл, коммита нет. Ключи: `-SkipTests`, `-KeepOldTraces`, `-DryRun`.

## 3. Инструменты (tools)

Все tools типизированы (Pydantic `args_schema`) и возвращают один JSON-конверт: `{"ok": true, "tool", "data"}`
или `{"ok": false, "tool", "error": {"type", "message", ...}}`. Ошибки уходят модели как наблюдения:
`invalid_argument`, `not_found`, `name_conflict`, `outside_workspace`, `http_error`, `timeout`, `network_error`.
`ToolGuardMiddleware` ставит такому `ToolMessage` `status = "error"`, в том числе для JSON с отступами от MCP.

| Tool | Аргументы | Что возвращает |
|---|---|---|
| `read` (cat) | `path`, `numbered` | текст, `truncated`, `total_lines`, `content_hash` |
| `head` / `tail` | `path`, `n` | первые / последние `n` строк |
| `grep` | `pattern`, `path`, `recursive`, `max_matches`, `ignore_case`, `regex` | `{path, line, text}`, `total_matches`, `has_more` |
| `ls` | `path` | один уровень: имя, тип, размер, данные каталога |
| `write` | `path`, `content`, `mode` | путь, `created`, `bytes_written` |
| `move` | `source`, `destination` | старый и новый путь, `document_id`, `verified` |
| `web_fetch` | `url` | `status`, `final_url`, `title`, текст |
| `catalog_view` | `folder`, `status`, `document_id` | записи каталога и отчёт сверки |
| `catalog_update` | `document_id`, `title`, `description`, `note` | обновлённая запись |
| `build_excerpts` | `document_id`, `ranges` | точный текст строк и `excerpt_id` |
| `load_skill` | `name` | полный текст SKILL.md |

`head`/`tail` принимают только положительное `n`; `grep` ищет буквальную подстроку (regex по флагу); `ls` даёт
стабильный порядок. `write` не пишет в `catalog.json`, в `input/` и поверх каталогизированного документа. `move`
никогда не перезаписывает (`name_conflict` + `suggested_name`), проверяет hash, обновляет каталог и сообщает
`action_required`, если надо синхронизировать индекс. `web_fetch` берёт только http(s), с таймаутом и лимитами.
Защита путей (`pathguard.py`): все symlink и junction разрешаются (`Path.resolve`), потом проверяется
`relative_to(workspace)`. Так блокируются `../`, абсолютные пути и ссылки наружу. `grep -r` проверяет подкаталог до
обхода: ссылка наружу попадает в `skipped` с причиной `link_outside_workspace`, файлы за ней в ответ не попадают.
Тесты: `test_dotdot_escape_blocked`, `test_absolute_path_outside_blocked`, `test_junction_escape_blocked_windows` и
`test_symlink_escape_blocked` (настоящий symlink; без права создавать symlink он `skipped`). У меня включён режим
разработчика Windows, в `results/symlink_test.txt` записано `real symlink test PASSED` и `2 passed, 33 deselected`.

Лимиты: вывод tool до `MAX_TOOL_OUTPUT_CHARS=12000` (флаг `truncated`); за ход `AGENT_MODEL_CALLS_PER_TURN=25`
(`ModelCallLimitMiddleware`, `exit_behavior="end"`) и `AGENT_RECURSION_LIMIT=110` super-step; таймауты: tool 30 с,
ход 600 с, LLM 120 с с двумя повторами, ES 10 с. Один вызов модели занимает 4 super-step, поэтому
`recursion_limit` больше `4 × 25 = 100` и первым срабатывает лимит вызовов модели (`turn_end.stopped_by`).

## 4. Граф готового ReAct-агента

Граф выгружен из скомпилированного агента (`python scripts/export_graph.py` → `docs/graph.mmd`, или `/graph`):

```mermaid
graph TD;
  __start__([__start__]) --> before["ModelCallLimitMiddleware.before_model"];
  before -.-> model;
  before -.-> __end__([__end__]);
  model --> after["ModelCallLimitMiddleware.after_model"];
  after -.-> tools;
  after -.-> __end__;
  after -.-> before;
  tools -.-> before;
```

Узлы `model` и `tools` строит `create_agent`, `before_model`/`after_model` добавляет middleware, пунктир означает
условный переход. Reducer `add_messages` дописывает новый `HumanMessage` к истории из checkpointer'а, `before_model`
проверяет счётчик, `model` вызывает LLM со схемами 18 tools. Есть `tool_calls` → `tools` (`ToolGuard`, `CatalogSync`,
сам tool, `ToolMessage` с тем же `tool_call_id`) и снова модель, нет → `__end__`. Если ход оборвал `recursion_limit`,
CLI закрывает висящие вызовы error-`ToolMessage` через `aupdate_state(as_node="tools")`. Переходы: `turn_end.transitions`.

Параллельные вызовы идут в `tools` отдельными задачами (`Send`), поэтому N вызовов дают N записей `tools` подряд,
в порядке завершения. В retry, ход 2, после второго вызова модели пять `tools` подряд (`update_document_path`, два
`get_document_excerpts`, два `read`); в guard, ход 9, ответ на `ls input/outside_link` записан раньше `ls input`.

## 5. История диалога в CLI

- Одна CLI-сессия соответствует одному `thread_id`, состояние хранится в `InMemorySaver`. Каждая реплика
  передаётся только новым `HumanMessage`, прежние сообщения я не пересылаю.
- Ответы модели и `ToolMessage` дописывает граф, поэтому «перенеси этот файл» агент понимает по истории.
- Системный промпт передаётся отдельно (`system_prompt=`); тексты файлов приходят только в `ToolMessage`.
- После каждого хода и по `/check` работает `history.validate_history`: id уникальны, у каждого tool call один
  результат, нет висящих вызовов. Пустой ответ модели CLI заменяет пометкой `empty_answer_note`.

## 6. Elasticsearch через MCP

MCP-сервер `mcp_es/server.py` (FastMCP, stdio) я написала сама. Агент запускает его подпроцессом
(`MultiServerMCPClient.session("es")` + `load_mcp_tools`) на всё время работы CLI; прямого клиента ES у агента нет.

| MCP tool | Назначение |
|---|---|
| `upsert_excerpts(excerpts, mode)` | запись по `_id = excerpt_id`, режимы `replace` и `merge` |
| `search_excerpts(query, size, category, document_id, path_prefix)` | лексический поиск с подсветкой |
| `update_document_path(document_id, new_path, category)` | новый путь для всех выдержек документа |
| `get_document_excerpts`, `index_stats`, `delete_document_excerpts` | проверка дублей, статистика, очистка |

Повтор той же версии даёт `created=0, updated=N`. Поиск идёт через `multi_match` с анализатором `russian`.
`update_document_path` всегда берёт `category` из папки `data/<category>/` нового пути, иначе индекс оставлял бы
категорию старой папки. Сервер можно запустить вручную: `python -m mcp_es.server` (`ES_URL`, `ES_INDEX`, `ES_TIMEOUT_S`).

Каталог и индекс согласует `CatalogSyncMiddleware`:
- перед `upsert_excerpts` выдержка сверяется с каталогом и файлом (путь, категория, hash, текст ровно равен строкам);
  при расхождении записи нет (`excerpt_validation_failed`);
- после `ok=true` статус `indexed`, при ошибке `error`; `move` проиндексированного документа ставит `pending_sync`,
  и `indexed` возвращается, только когда путь и категория в индексе совпали с каталогом;
- ES недоступен → `es_unavailable`; MCP-сервер не стартовал → заглушки с теми же именами и аргументами сразу
  отвечают `mcp_unavailable`. Организация файлов работает и тогда, а `excerpts` строкой JSON тоже разбирается.

## 7. Skills

`skills/<name>/SKILL.md` написаны по спецификации Agent Skills: YAML front matter (`name`, `description`) и процедура.
`organize_files` описывает осмотр input, выбор структуры, конфликты, перенос и отчёт. `index_excerpts` описывает
выбор выдержек, `build_excerpts` → `upsert_excerpts`, повтор без дублей, синхронизацию путей и поиск; в ответе на
поиск каждая выдержка даётся цитатой в «» с `current_path`, строками и `document_id`. В системный промпт попадают
только `name: description`, полный текст отдаёт `load_skill(name)`, по одному разу за сессию (раздел 9).

## 8. Контракт данных для лабораторной 3

Каталог `workspace/catalog.json` (`content_hash` считается от содержимого и при переносе не меняется):

```json
{
  "schema_version": 1, "updated_at": "2026-11-22T15:35:43+00:00",
  "documents": [{
    "document_id": "c0a8…-uuid4",                 // присваивается один раз, не зависит от пути
    "original_path": "input/ir_lecture_inverted_index.md",
    "current_path": "data/search/ir_lecture_inverted_index.md",   // относительно workspace
    "title": "…", "category": "search", "description": "…", "note": "…",   // category = папка под data/
    "content_hash": "sha256:…", "size_bytes": 1871, "line_count": 24, "is_empty": false,
    "indexing_status": "not_indexed | indexed | error | pending_sync", "index_error": null,
    "excerpts": [{"excerpt_id": "…", "start_line": 6, "end_line": 8, "summary": "…"}],
    "indexed_at": "…", "registered_at": "…", "moves": [{"from": "input/…", "to": "data/courses/…", "at": "…"}]
  }]
}
```

Выдержка в Elasticsearch (индекс `itmo_lab1_excerpts`, mapping в `mcp_es/schema.py`, `dynamic: strict`):

| Поле | Тип | Смысл |
|---|---|---|
| `excerpt_id` | keyword (= `_id`) | `uuid5(document_id \| content_hash \| start_line \| end_line)` |
| `document_id` | keyword | ссылка на `catalog.documents[].document_id` |
| `current_path`, `category` | keyword | путь файла и папка под `data/` |
| `title`, `text`, `summary` | text (russian); `.std`, `title.raw` | заголовок, точный текст строк, описание |
| `start_line`, `end_line` | integer | включительно, с 1 |
| `content_hash`, `indexed_at` | keyword, date | версия файла и время записи |

Та же версия файла и тот же диапазон дают тот же `excerpt_id`, поэтому повторная индексация не плодит дублей.
От совпадения в ES к файлу и соседним строкам можно перейти через `read`/`head`/`tail` по `current_path`.

## 9. Пример диалога и трассы

```
> Посмотри input: какие документы там есть? Разложи их по темам в data.
  ↳ [model] load_skill({"name": "organize_files"}) … ls … read(...) … move(...)
> Выдели полезные выдержки из файлов про поиск и сохрани их с метаданными в Elastic.
  ↳ [model] load_skill({"name": "index_excerpts"}) … build_excerpts(...) … upsert_excerpts(...)
```

Трасса пишется в `traces/session_*.jsonl` и `traces/session_*.md`: `session_start`, `user_turn`, `graph_step`
(узел, сообщения, tool calls, результаты со `status`), `user_action`, `harness_wait`, `turn_end` (переходы, проверка
истории, `stopped_by`, usage), `cli_command`, `session_end`; токены маскируются. Шесть сессий с моделью `qwen3.8-27b`
идут подряд от чистого workspace и пустого индекса, каждая продолжает состояние предыдущей. В `traces/` и `results/`
лежит прогон 22.11.2026 (начало 18:27:46 +03:00), по ходам он расписан в `results/RUN_SUMMARY.md`.

| # | Сценарий | Трасса | Ходов / вызовов модели | Итог |
|---|---|---|---|---|
| 1 | main | `session_20261122_182754_5a4d88` | 16 / 52 | организация, индексация, поиск, поздние файлы |
| 2 | limit | `session_20261122_183132_0ebee5` | 2 / 27 | ход 1 остановлен `model_call_limit` |
| 3 | guard | `session_20261122_183207_e2adbb` | 17 / 34 | каждый ход вызывает tool, 11 ошибок |
| 4 | errors | `session_20261122_183315_427dff` | 4 / 14 | ES остановлен: `error`, `pending_sync` |
| 5 | mcp_down | `session_20261122_183439_e2dbe1` | 3 / 12 | `mcp_unavailable`, перенос работает |
| 6 | retry | `session_20261122_183508_292b8b` | 4 / 12 | всё проиндексировано и синхронизировано |

| Ход main | Что сделала модель |
|---|---|
| 1 | `organize_files`, 4 `ls`, 11 `read`, 12 `move`, 12 `catalog_update` |
| 2–4 | `ls data/projects`; заметка в `data/courses/`; input пуст, бывших подпапок нет (2 `not_found`) |
| 5 | `index_excerpts`, 5 документов, `upsert_excerpts` → `created: 10` |
| 6 | `search_excerpts` («индекс») → 4 выдержки, `read` исходного файла |
| 7–8 | повтор → `created: 0, updated: 2`; у лекции 2 выдержки, в индексе 10 |
| 9–10 | перенос в `data/search/`, `update_document_path` → `category: search`; поиск «BM25» |
| пауза | файлы скопировал отдельный процесс (PID 33996, CLI PID 36580) |
| 11–12 | новый `ls input`, перенос в `meetings` и `search`; 3 выдержки новой лекции |
| 13–16 | `invalid_argument`, `outside_workspace`, `not_found`; пустой файл и два `notes.md` |

Папки модель выбрала сама, промпт имён не подсказывает: `courses` (лекции и семинары), `howto` (инструкции),
`meetings` (протоколы), `projects` (проекты) и `_unsorted` для пустого `empty_todo.txt`. Одноимённые `notes.md`
легли в разные папки (`data/courses/notes.md`, `data/meetings/notes.md`), поэтому конфликта имён не было.
Неоднозначную `search_quality_thoughts.md` модель положила в `projects` и записала причину в note, а строку с prompt
injection из `meetings/notes.md` процитировала пользователю как данные и не выполнила. Папку `search` создал запрос
в ходе 9, `guides` запрос в mcp_down. После хода 12 в индексе 13 выдержек из 6 документов.

Ответы на поиск дают каждую выдержку цитатой в «» с путём, строками и полным `document_id`. В ходе 6 это 4 выдержки
из 3 документов (лекция про инвертированный индекс, строки 6–8 и 18–20; инструкция по ES в Docker, 9–10; описание
DocSearch, 7–10) и текст исходной лекции. В ходе 10 все 3 выдержки по «BM25» идут с новым путём лекции в `data/search/`.

- limit: модель 25 раз подряд вызвала `ls input`, ожидая файл, который никто не добавит, и ход остановил
  `ModelCallLimitMiddleware` («run limit (25/25)», 21,27 с). В ходе 2 она объяснила остановку.
- guard: успешные `head n=3`, `tail n=2` и `write` (`data/howto/_about.md`, 53 байта); `head n=0`, `tail n=-2` →
  `invalid_argument`; `../../secret.txt`, `C:\Windows\win.ini`, junction `input/outside_link`, `move` в `../outside.txt`
  → `outside_workspace`; `grep -r input` → `files_scanned: 0`, ссылка в `skipped`; `move` на занятое имя →
  `name_conflict`; `web_fetch` к `scripts/http_fixture_server.py` → 200, 404, `timeout` через 10 с; порт 9 → `network_error`.
- errors: upsert семинара ReAct → `es_unavailable`, статус `error`; перенос заметки из `courses` в `projects`,
  `update_document_path` → `es_unavailable`, статус `pending_sync`.
- mcp_down: MCP-сервер не запустился (`FileNotFoundError`). Модель передала 2 выдержки инструкции по git в
  `upsert_excerpts` строкой JSON, заглушка сразу ответила `mcp_unavailable`, статус `error`
  (`results/catalog_after_mcp_down.json`). Перенос инструкции в `data/guides/` прошёл.
- retry: `update_document_path` → `category: projects`, один upsert → `created: 6`; после этого 8 `indexed`,
  6 `not_indexed`, а путь и категория заметки в каталоге и ES совпали.

Итог (`results/verify_report.json`, `RUN_SUMMARY.md`): все 5 проверок `verify` пройдены, 14 документов,
8 проиндексированы, 19 выдержек, расхождений пути и категории с каталогом 0. Проверка истории прошла во всех
ходах, каждая сессия закончилась `/exit`, все 33 пункта чек-листа отмечены. У каждого результата с `ok: false`
(5 в main, 11 в guard, 2 в errors, 1 в mcp_down) стоит `status: error`. Ожидаемое состояние: `fixtures/EXPECTED.md`.

## 10. Тесты и проверочные сценарии

```powershell
python -m pytest -rs                 # офлайн + интеграция с ES (если ES запущен)
python scripts/verify.py             # проверки результата после демо-диалога
```

В наборе 73 теста, `python -m pytest tests` с запущенным ES даёт `73 passed` (`test_mcp_es.py` без ES пропускается).
Агентные тесты используют детерминированные `ScriptedChatModel` / `DryRunChatModel`, трассы для сдачи пишутся с моделью.

| Сценарий | Трасса | Тест |
|---|---|---|
| Первичная организация, перенос | main, ходы 1 и 3 | `test_move_input_to_data_and_between_folders` |
| Просмотр, история без дублей | main, ходы 2 и 4, `/check` | `test_turns_history_and_transitions` |
| Пополнение input | main, пауза и ход 11 | `test_cli_script_with_external_late_files_and_loop_limit` |
| Индексация и поиск | main, ходы 5–6 | `test_mcp_upsert_idempotent_search_and_path_update` |
| Повтор и перенос после индексации | main, ходы 7–10 | `test_update_path_without_category_follows_folder` |
| Skills | main, ходы 1 и 5 | `test_skills_metadata_and_on_demand_load` |
| Ошибки tools | guard; main, ходы 13–15 | `test_tools_fs.py`, `test_web_fetch.py` |
| Недоступный ES и MCP | errors, mcp_down, retry | `test_mcp_server_unavailable_session` |
| Лимит и завершение | limit, `session_end` | `test_model_call_limit_marked_in_trace` |

В конце main `/check` вернул `history_ok: true` при 153 сообщениях; ошибки в guard получены настоящими вызовами tools.

## 11. Ограничения

- Без сети ИТМО работают только тесты. История CLI живёт в памяти процесса, её восстановление будет в лабе 2.
- Новые файлы агент находит повторным `ls` по просьбе пользователя; фонового watcher нет (по заданию не нужен).
- Модель меняет в каталоге только title, description и note. Замену содержимого каталогизированного файла я не
  поддерживаю: `build_excerpts` вернёт `hash_mismatch`.
- `move` сделан через `os.link` + `unlink`: при падении между ними останется второе имя (его видит `verify.py`).
  Таймаут tool прерывает ожидание, но синхронный tool в потоке-исполнителе дорабатывает в фоне.
- Без права создавать symlink выход по ссылке на Windows проверяется только на directory junction.
- Модель не всегда держит формат ответа: в retry (ход 3) она дала `document_id` первыми 8 символами.
- `traces/` и `results/` пересоздаются только целиком, а индекс учебный: одна нода без аутентификации на `127.0.0.1`.

## 12. Выводы

Готовый ReAct-агент из `create_agent` закрывает цикл model → tools → model, а надёжность пришлось строить вокруг
него: единый JSON-конверт ошибок, проверка путей, лимиты и middleware, которое не пускает в индекс выдержку,
не совпадающую с файлом. Каталог со стабильным `document_id` связывает файлы, индекс и статусы, и после переноса
или отказа ES состояние доводится до согласованного повторным вызовом. Модель иногда передаёт аргументы строкой
JSON или сокращает идентификаторы, поэтому проверки я держу в коде и в `verify.py`, а не только в промпте.
