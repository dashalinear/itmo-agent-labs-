# Лабораторная 1. Агент-организатор файлов: LangGraph ReAct, tools, MCP, skills

Диалоговый помощник раскладывает файлы из `workspace/input/` по тематическим папкам `workspace/data/`.
Он ведёт каталог `workspace/catalog.json` со стабильными `document_id` и по запросу сохраняет точные выдержки
с метаданными в Elasticsearch **через MCP-сервер**. Результат (каталог и индекс выдержек) служит входом для лабораторной № 3.

## Содержание
1. [Архитектура](#1-архитектура)
2. [Установка и запуск](#2-установка-и-запуск)
3. [Инструменты](#3-инструменты-tools)
4. [Граф готового ReAct-агента](#4-граф-готового-react-агента)
5. [История диалога в CLI](#5-история-диалога-в-cli)
6. [Elasticsearch через MCP](#6-elasticsearch-через-mcp)
7. [Skills](#7-skills)
8. [Контракт данных: каталог и выдержки](#8-контракт-данных-для-лабораторной-3)
9. [Пример диалога и трассы](#9-пример-диалога-и-трассы)
10. [Тесты и проверочные сценарии](#10-тесты-и-проверочные-сценарии)
11. [Ограничения](#11-ограничения)

---

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
| `organizer/agent.py` | сборка агента: `create_agent(model, tools, system_prompt, middleware, checkpointer)` |
| `organizer/prompts.py` | системный промпт |
| `organizer/cli.py` | диалоговый CLI, история, ремонт истории после лимита, трассы |
| `organizer/tools_fs.py` | `read`, `head`, `tail`, `grep`, `ls`, `write`, `move` |
| `organizer/tools_web.py` | `web_fetch` |
| `organizer/pathguard.py` | нормализация путей, разрешение symlink/junction, проверка принадлежности workspace |
| `organizer/catalog.py`, `tools_catalog.py` | каталог (`catalog_view`, `catalog_update`) и `build_excerpts` |
| `organizer/skills.py` | реестр skills и tool `load_skill` |
| `organizer/middleware.py` | `ToolGuardMiddleware`, `CatalogSyncMiddleware` |
| `organizer/mcp_client.py` | подключение MCP-сервера через `langchain-mcp-adapters` |
| `mcp_es/` | собственный тонкий MCP-сервер над Elasticsearch, схема выдержки |
| `skills/organize_files/SKILL.md`, `skills/index_excerpts/SKILL.md` | два skill по спецификации Agent Skills |
| `fixtures/` | тестовые файлы (`input/`, `late/`) и `EXPECTED.md` |
| `scripts/` | `run_all_demos.ps1` (вся цепочка демо), подготовка workspace, добавление файлов из второго терминала, проверка (`verify.py`), экспорт и восстановление индекса, экспорт графа, сводка прогона (`summarize_run.py`) |
| `demo/` | сценарии диалога для трасс |
| `traces/` | трассы сессий (`*.jsonl` и `*.md`) |
| `results/` | снимок workspace, каталог, выгрузка индекса, отчёты `verify`, сводка прогона `RUN_SUMMARY.md` |

**Версии** (`requirements.txt`, полный lock — `requirements.lock.txt`): Python 3.13.7 (подходит ≥ 3.11),
langchain 1.5.0, langgraph 1.2.22, langchain-openai 1.8.0, langchain-mcp-adapters 0.3.2, mcp 1.30.0,
elasticsearch-py 9.5.1, Elasticsearch 9.5.5 (Docker).
В текущей документации `create_react_agent` помечен устаревшим, поэтому используется `langchain.agents.create_agent`.
Это та же библиотечная фабрика ReAct-агента на LangGraph (разрешено заданием).

## 2. Установка и запуск

Нужны: Python ≥ 3.11, Docker Desktop, доступ к сети ИТМО (LLM-сервер `10.32.2.11` виден только из неё).

```powershell
cd lab1
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # впишите LLM_API_KEY (личный токен; .env в .gitignore)
docker compose up -d            # Elasticsearch 9.5.5 на http://localhost:9200
python scripts/prepare_workspace.py --reset-index   # workspace/input = fixtures/input, пустая data/, чистый индекс
```

**Запуск диалогового CLI:**

```powershell
python -m organizer.cli
```

Команды CLI: `/help`, `/history` (статистика сообщений), `/check` (проверка пар tool_call/tool_result и дублей),
`/catalog`, `/graph` (mermaid-схема), `/exit` (также `/quit`, `выход`, Ctrl+Z/Ctrl+C).

**Файлы, добавленные во время открытой сессии.** CLI их не копирует: пока сессия открыта, пользователь кладёт
файлы в `workspace/input/` сам — во **втором терминале** `python scripts/add_late_files.py` (копирует
`fixtures/late/*`) или вручную через Проводник. Затем говорит агенту: «Я добавил файлы, разбери новые», и агент
видит их повторным `ls`.

В сценарии (`--script`) то же делается паузой: строка `@wait_for_files input/a.md input/b.md` останавливает
чтение реплик, пока эти файлы не появятся в `input/` (появление и стабильный размер проверяются опросом раз в 0,5 с,
таймаут `--wait-timeout`, по умолчанию 600 с). На время паузы CLI пишет файл-маркер (`--wait-marker`), по которому
внешний скрипт понимает, что пора добавлять файлы. В трассе остаются события `harness_wait` и
`user_action: external_files_added` с полем `added_by: "another process (this CLI only waited, copied nothing)"`.
Старая директива `@add_late_files` (копирование самим CLI) оставлена для совместимости, в демо не используется.

**Ручной прогон основного диалога:**

```powershell
python -m organizer.cli --script demo/dialog_ru.txt
#   когда CLI напишет "[пауза сценария]", во втором терминале: python scripts/add_late_files.py
python scripts/verify.py          # детерминированные проверки → results/verify_report.json
python scripts/export_index.py    # results/index_export.jsonl, catalog.json, data_tree.txt, workspace_snapshot/
```

**Все трассы и все `results/` одной командой** (только из сети ИТМО, около 10 минут: в прогоне 19.11.2026 от сброса workspace до коммита прошло 8 минут, плюс тесты; из корня репозитория,
скрипт сам переходит в `lab1`):

```powershell
powershell -ExecutionPolicy Bypass -File lab1\scripts\run_all_demos.ps1
```

Скрипт: проверка LLM → `docker compose up` → `pytest` → чистый workspace и пустой индекс → цепочка из пяти сессий
(раздел 9) → `verify` → экспорт индекса и графа → `results/RUN_SUMMARY.md` → поиск токена в `traces/` и `results/` →
удаление трасс прежних прогонов (они не относятся к этой цепочке) → `git commit` **только** `lab1/traces` и
`lab1/results` → `git push`. Если `verify` не прошёл, старые трассы не удаляются и ничего не коммитится.
Ключи: `-SkipPush`, `-SkipTests`, `-KeepOldTraces`; `-DryRun` — проверка логики скрипта без LLM (детерминированная
модель-заглушка `--dry-run-model`, только на копии `lab1` вне git-репозитория, без коммита).

Воспроизведение индекса без LLM (вход для лабы 3): `python scripts/restore_index.py` создаёт индекс заново
из `results/index_export.jsonl`.

## 3. Инструменты (tools)

Все tools типизированы (Pydantic `args_schema` с описаниями) и возвращают единый JSON-конверт:
`{"ok": true, "tool": "...", "data": {...}}` или `{"ok": false, "tool": "...", "error": {"type", "message", ...}}`.
Ошибки — это **наблюдения** для модели, а не исключения. Неверный `n`, отсутствующий файл, конфликт имён,
HTTP-ошибка и таймаут превращаются в `error.type = invalid_argument | not_found | name_conflict | http_error | timeout`.

| Tool | Аргументы | Поведение |
|---|---|---|
| `read` (cat) | `path`, `numbered=false` | текст, путь, `truncated`, `total_lines`, `content_hash`; `numbered` — номера строк для выбора выдержек |
| `head` / `tail` | `path`, `n` | первые / последние `n` строк в исходном порядке; `n` — положительное целое; короткий файл → все строки; пустой → пустой результат |
| `grep` | `pattern`, `path`, `recursive`, `max_matches`, `ignore_case`, `regex` | буквальная подстрока (regex — расширение); `{path, line (с 1), text}`, `total_matches`, `has_more`, `remaining` |
| `ls` | `path` | один уровень: имя, тип, размер; стабильный порядок (папки, затем файлы по имени); для документов — `document_id`, title, description, статус; новые файлы видны при повторном вызове |
| `write` | `path`, `content`, `mode` | UTF-8, `overwrite`/`append`, создаёт родительские папки; **запрещено**: `catalog.json`, `input/`, перезапись каталогизированного документа |
| `move` | `source`, `destination` | input → data или data → data; создаёт папки; **никогда не перезаписывает** (`name_conflict` + `suggested_name`); проверяет, что старого пути нет, новый есть и hash не изменился; регистрирует или обновляет документ в каталоге; сообщает `action_required`, если индекс нужно синхронизировать |
| `web_fetch` | `url` | только http(s); таймаут, лимит байт и редиректов; `url`, `final_url`, `status`, `title`, `text` (HTML → текст), `truncated` |
| `catalog_view` | `folder`, `status`, `document_id` | записи каталога и отчёт сверки (пропавшие или незарегистрированные файлы) |
| `catalog_update` | `document_id`, `title`, `description`, `note` | описание документа, обоснование классификации |
| `build_excerpts` | `document_id`, `ranges` | точный текст строк, `content_hash`, стабильный `excerpt_id`; без `ranges` повторно использует прежние диапазоны |
| `load_skill` | `name` | полный текст SKILL.md по требованию |

**Защита путей** (`pathguard.py`): путь интерпретируется относительно workspace, нормализуется, все symlink
и junction разрешаются (`Path.resolve`), после этого проверяется `relative_to(workspace)`.
Блокируются `../`, абсолютные пути наружу, symlink/junction наружу. `move` не перемещает сами ссылки.
`grep -r` не ходит по ссылкам наружу.

Как это проверено:
- `../` (в том числе `input/../../x`) и абсолютные пути наружу — `test_dotdot_escape_blocked`,
  `test_absolute_path_outside_blocked` (все инструменты: read, ls, write, move, grep);
- **directory junction** (точка повторной обработки NTFS — именно так выглядит выход по ссылке на Windows без
  особых прав) — `test_junction_escape_blocked_windows`: read/head/tail/write/ls/grep/move через junction,
  абсолютный путь через junction, абсолютный путь к цели, комбинация с `../`; файл снаружи остаётся на месте.
  Тот же junction (`input/outside_link`) скрипт демо создаёт перед сессией guard, и модель обращается к нему
  реальными вызовами tools;
- **настоящий symlink** (файловый и каталожный) — `test_symlink_escape_blocked`. Он выполняется там, где
  создание symlink разрешено: Linux/macOS, Windows с включённым режимом разработчика (Параметры → Обновление и
  безопасность → Для разработчиков → «Режим разработчика») или под администратором. На машине автора режим
  разработчика выключен, поэтому тест помечается `skipped` с пояснением; системные настройки ради теста не менялись.
  `pathguard` обрабатывает оба вида ссылок одним кодом (`Path.resolve` + `os.path.islink`/`os.path.isjunction`).

**Лимиты вывода**: каждый tool ограничен `MAX_TOOL_OUTPUT_CHARS` (12000) с флагом `truncated` и подсказкой дочитать
через head/tail/grep. Кроме того, `ToolGuardMiddleware` обрезает результат **любого** tool, включая MCP.

**Лимиты выполнения**: `AGENT_MODEL_CALLS_PER_TURN=25` (`ModelCallLimitMiddleware`, `exit_behavior="end"`),
`AGENT_RECURSION_LIMIT=110` super-step графа за ход, таймаут tool 30 с (`ToolGuardMiddleware`), таймаут хода 600 с,
таймаут и retry LLM (`ChatOpenAI(timeout=120, max_retries=2)`), таймаут ES 10 с.
Один вызов модели — это 4 super-step (`before_model`, `model`, `after_model`, `tools`), поэтому
`recursion_limit` держится выше `4 × 25 = 100`: первым мягко срабатывает лимит вызовов модели, а `recursion_limit`
остаётся страховкой (при прежнем значении 100 цикл из 25 вызовов упирался именно в него —
`test_default_limits_let_model_call_limit_fire_first`). Сработавший лимит CLI записывает в трассу
(`turn_end.stopped_by = model_call_limit | recursion_limit | turn_timeout`) и в ответ.

## 4. Граф готового ReAct-агента

Граф получен из скомпилированного агента (`python scripts/export_graph.py` → `docs/graph.mmd`, или `/graph` в CLI):

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

Узлы и рёбра созданы библиотекой: `create_agent` строит `model` и `tools` (ToolNode). Middleware с хуками
`before_model`/`after_model` добавляет свои узлы. Пунктир — условные переходы.

Как работает один ход (сверяйте с трассой, поле `transitions`):

1. **`__start__`** — в канал `messages` приходит новый `HumanMessage`. Reducer `add_messages` **дописывает** его
   к истории из checkpointer'а (по `thread_id`), а не заменяет её.
2. **`ModelCallLimitMiddleware.before_model`** — проверяет счётчик вызовов модели за ход. При превышении
   прыгает в `__end__` с сообщением о лимите. Так завершается зацикленный сценарий.
3. **`model`** — вызывает LLM с system prompt, всей историей и схемами 18 tools. Получает `AIMessage`, в нём может быть список `tool_calls` (`id`, `name`, `args`).
4. **`after_model` → условный переход**: если в `AIMessage` есть `tool_calls`, управление идёт в **`tools`**,
   иначе в **`__end__`** (финальный ответ).
5. **`tools`** (ToolNode) выполняет все вызовы шага (параллельно). Каждый вызов оборачивается `wrap_tool_call`:
   `ToolGuard` (таймаут, исключение → error-наблюдение, лимит вывода) → `CatalogSync` (только для MCP-tools
   индекса) → сам tool. На каждый вызов создаётся `ToolMessage` с тем же `tool_call_id`. Невалидные аргументы
   ToolNode сам превращает в error-`ToolMessage`.
6. **`tools` → `before_model` → `model`** — возврат к модели с наблюдениями. Цикл повторяется, пока модель не ответит без tool calls.
7. **Завершение**: финальный `AIMessage` без tool calls; либо лимит вызовов модели (`end`); либо
   `recursion_limit` (`GraphRecursionError`). В последнем случае CLI закрывает «висящие» tool calls
   error-`ToolMessage` через `aupdate_state(as_node="tools")`, и история остаётся валидной.
   После каждого super-step состояние сохраняется в checkpointer.

## 5. История диалога в CLI

- Одна CLI-сессия — один `thread_id`, состояние хранится в `InMemorySaver` (сохранение истории на диск в лабе 1 не требуется).
- Каждая реплика передаётся **только** как новый `HumanMessage`: `agent.astream({"messages": [HumanMessage(text)]}, config)`.
  Предыдущие сообщения не пересылаются, поэтому дублей нет.
- Ответы модели, `tool_calls` и `ToolMessage` с корректными `tool_call_id` дописывает сам граф.
  Поэтому «перенеси этот файл» и «добавь его в Elastic» разрешаются по истории.
- Системный промпт передаётся отдельно (`system_prompt=`), в историю не попадает. Тексты файлов приходят только
  как содержимое `ToolMessage` (данные), а реплики пользователя — только как `HumanMessage`.
- `/check` (и проверка после каждого хода, поле `history check` в трассе) — `history.validate_history`:
  уникальные id сообщений, у каждого tool call ровно один результат, нет результатов без вызова, нет висящих вызовов.

## 6. Elasticsearch через MCP

Собственный тонкий MCP-сервер `mcp_es/server.py` (FastMCP, stdio). Агент запускает его подпроцессом
(`langchain_mcp_adapters`: `MultiServerMCPClient.session("es")` + `load_mcp_tools`) и держит одну сессию на всё время работы CLI.
Агент **никогда** не обращается к ES напрямую: прямой клиент есть только в админ-скриптах `scripts/export_index.py`
и `scripts/restore_index.py`.

| MCP tool | Назначение |
|---|---|
| `upsert_excerpts(excerpts, mode="replace"\|"merge")` | запись или обновление по `_id = excerpt_id`; повтор той же версии даёт `created=0, updated=N`; `replace` удаляет прежние выдержки документа, которых нет в пачке |
| `search_excerpts(query, size, category, document_id, path_prefix)` | лексический `multi_match` (анализатор `russian`) с подсветкой: текст, путь, строки, `document_id` |
| `update_document_path(document_id, new_path, category)` | `update_by_query` для всех выдержек документа после переноса; `category` всегда берётся из папки `data/<category>/` нового пути (даже если аргумент не передан или передан другой), в ответе — итоговая `category` |
| `get_document_excerpts`, `index_stats`, `delete_document_excerpts` | проверка отсутствия дублей, статистика, очистка |

Ручной запуск сервера: `python -m mcp_es.server` (stdio). Параметры берутся из окружения: `ES_URL`, `ES_INDEX`, `ES_TIMEOUT_S`.

**Согласованность** (`CatalogSyncMiddleware`):
- *до* `upsert_excerpts` каждая выдержка сверяется с каталогом и файлом: `current_path`, `category`, `content_hash`,
  текст ровно совпадает со строками `start_line..end_line`. Иначе запись **не выполняется** (`excerpt_validation_failed`);
- *после* `ok=true` документ получает статус `indexed`, в каталог записывается список диапазонов; при ошибке — `error`;
- `move` проиндексированного документа ставит статус `pending_sync`. Успешный `update_document_path` возвращает `indexed`
  только если и путь, и категория в индексе совпали с каталогом; иначе (или при ошибке ES) остаётся `pending_sync`
  с текстом причины, повтор исправляет. `scripts/verify.py` сравнивает с каталогом и путь, и категорию каждой выдержки.
  (Исправленная ошибка прошлой версии: при вызове `update_document_path` без `category` индекс сохранял категорию
  старой папки, а каталог уже показывал новую; регрессионные тесты — `test_update_path_without_category_follows_folder`,
  `test_store_update_path_derives_category_offline`, `test_catalog_sync_checks_category_after_path_update`,
  `test_verify_flags_category_drift`);
- при недоступности ES сервер возвращает `ok=false, error.type=es_unavailable`. Если не стартовал сам MCP-сервер, агент получает
  заглушки с теми же именами (`mcp_unavailable`). Организация файлов продолжает работать, индексация не помечается успешной.

## 7. Skills

`skills/<name>/SKILL.md` по спецификации Agent Skills: YAML front matter (`name`, `description`) и процедура.
- **organize_files** — осмотр input, выбор структуры, неоднозначности и конфликты, перенос, обновление каталога, отчёт.
- **index_excerpts** — выбор выдержек, `build_excerpts` → `upsert_excerpts`, повтор без дублей, синхронизация путей, поиск.

**Выборочная загрузка.** В системный промпт попадают только `name: description` обоих skills.
Полный текст возвращает tool `load_skill(name)`, когда запрос подходит под описание. В трассе видно, что на
организацию загружается `organize_files`, а на индексацию — `index_excerpts`, причём каждый один раз за сессию.

## 8. Контракт данных для лабораторной 3

**Каталог** `workspace/catalog.json` (JSON):

```json
{
  "schema_version": 1, "updated_at": "2026-11-15T12:00:00+00:00",
  "documents": [{
    "document_id": "c0a8…-uuid4",                 // присваивается один раз, не зависит от пути
    "original_path": "input/ir_lecture_inverted_index.md",
    "current_path": "data/search/ir_lecture_inverted_index.md",   // относительно workspace
    "title": "Лекция 3. Инвертированный индекс и ранжирование BM25",
    "category": "search",                         // первая папка под data/
    "description": "…", "note": "…",
    "content_hash": "sha256:…",                  // от содержимого, сохраняется при переносе
    "size_bytes": 1871, "line_count": 25, "is_empty": false,
    "indexing_status": "not_indexed | indexed | error | pending_sync",
    "index_error": null,
    "excerpts": [{"excerpt_id": "…", "start_line": 5, "end_line": 8, "summary": "…"}],
    "indexed_at": "…", "registered_at": "…",
    "moves": [{"from": "input/…", "to": "data/courses/…", "at": "…"}]
  }]
}
```

**Выдержка** в Elasticsearch (индекс `itmo_lab1_excerpts`, mapping в `mcp_es/schema.py`, `dynamic: strict`):

| Поле | Тип | Смысл |
|---|---|---|
| `excerpt_id` | keyword (= `_id`) | `uuid5(document_id \| content_hash \| start_line \| end_line)`: та же версия и диапазон дают тот же ключ |
| `document_id` | keyword | → `catalog.documents[].document_id` |
| `current_path` | keyword | путь файла относительно workspace, обновляется после переноса |
| `title` | text (russian) + `.raw` keyword | |
| `category` | keyword | |
| `text` | text (russian) + `.std` | **точный** текст строк файла |
| `summary` | text | необязательное описание, не заменяет `text` |
| `start_line`, `end_line` | integer | включительно, нумерация с 1 |
| `content_hash` | keyword | версия файла |
| `indexed_at` | date | |

Связь: `excerpt.document_id → catalog.document_id`, а `current_path` задаёт папку и файл. Переход
«совпадение в ES → файл → соседние строки» выполняется через `read`/`head`/`tail` по `current_path`.

## 9. Пример диалога и трассы

Сценарий `demo/dialog_ru.txt` (фрагмент):

```
> Посмотри input: какие документы там есть? Разложи их по темам в data.
  ↳ [model] load_skill({"name": "organize_files"})
  ↳ [model] ls({"path": "input"}) … read(...) … move(...)
> Перенеси заметку про поиск в папку курсов.
> Выдели полезные выдержки из файлов про поиск и сохрани их с метаданными в Elastic.
  ↳ [model] load_skill({"name": "index_excerpts"}) … build_excerpts(...) … upsert_excerpts(...)
> Найди в Elastic, что у нас есть про индексы, и покажи исходный файл.
@wait_for_files input/meeting_2026-11-10_retro.md input/vector_search_lecture.md
  [пауза сценария] жду, пока пользователь добавит файлы в input извне (...); этот процесс ничего не копирует
  [пауза сценария] в input появились: ['input/meeting_2026-11-10_retro.md', 'input/vector_search_lecture.md']
> Я добавил файлы, разбери новые.
  ↳ [model] ls({"path": "input"}) … read(...) … move(...)
```

Трасса каждой сессии пишется в два файла:
- `traces/session_*.jsonl` — события `session_start`, `user_turn` (с `run_id`), `graph_step` (узел, сообщения,
  tool calls с `id/name/args`, tool results с `tool_call_id/status`), `user_action`, `harness_wait`, `turn_end`
  (`transitions`, проверка истории, ошибка, `stopped_by` при срабатывании лимита, длительность, usage), `session_end`;
- `traces/session_*.md` — то же в читаемом виде.

Токены вида `sk-…` и `Bearer …` в трассах маскируются.

**Цепочка сессий, которая создаёт `traces/` и `results/`.** `scripts/run_all_demos.ps1` запускает пять сессий CLI
подряд, все с реальной моделью `qwen3.8-27b`, начиная с чистого workspace (`input` = `fixtures/input`, пустая `data/`)
и пустого индекса. Каждая следующая сессия продолжает состояние workspace и индекса предыдущей; других сессий
в `traces/` нет (трассы прежних прогонов скрипт удаляет после успешного прогона).

Прогон, лежащий в репозитории: 19.11.2026, начало 17:09:15 (+03:00), модель `qwen3.8-27b`, лимиты
`model_calls_per_turn=25`, `recursion_limit=110`; длительность сессий по трассам — около 6 минут (без тестов и экспорта).

| # | Сценарий | Диалог | Трасса (`traces/*.jsonl` и `.md`) | Ходов / вызовов модели | Что показывает трасса |
|---|---|---|---|---|---|
| 1 | main | `demo/dialog_ru.txt` | `session_20261119_170923_70e27b` | 16 / 52 | организация, просмотр, перенос, индексация, поиск, повтор без дублей, перенос проиндексированного файла, поздние файлы, ошибки (подробно ниже) |
| — | `verify` | | | | `results/verify_report_main.json`: все 5 проверок пройдены |
| 2 | limit | `demo/dialog_limit_ru.txt` | `session_20261119_171303_5afaf3` | 2 / 27 | **настоящее зацикливание**: в ходе 1 модель 25 раз подряд вызвала `ls input`, ожидая файл, который никто не добавляет; ход остановил `ModelCallLimitMiddleware` («Model call limits exceeded: run limit (25/25)», `turn_end.stopped_by = model_call_limit`, 20,8 с). Ход 2 той же сессии работает: модель объясняет остановку (25 вызовов `ls`) и делает ещё один `ls`; история корректна |
| 3 | guard | `demo/dialog_guard_ru.txt` | `session_20261119_171335_0afe74` | 11 / 22 | все ошибки — результаты **реальных вызовов tools**: ход 2 `head n=0` и ход 3 `tail n=-2` → `invalid_argument`; ход 4 `../../secret.txt`, ход 5 `C:\Windows\win.ini`, ход 6 `ls input/outside_link` (junction наружу; в `ls input` он показан как `symlink`, `path: null`), ход 7 `read` через junction, ход 9 `move` в `../outside.txt` → `outside_workspace`; ход 8 `grep -r input` → 0 совпадений, файл за junction в `skipped` с причиной `symlink_outside_workspace`; ход 10 `move` kickoff → путь существующей ретроспективы → `name_conflict`, `suggested_name: meeting_2026-11-10_retro_2.md`; ход 11 `web_fetch http://127.0.0.1:9/` → `network_error` |
| 4 | errors | `demo/dialog_errors_ru.txt` | `session_20261119_171420_ec42f1` | 4 / 14 | ES остановлен, MCP-сервер работает. Ход 2: upsert 4 выдержек семинара ReAct → `es_unavailable`, в каталоге `error`. Ход 3: заметка `search_quality_thoughts.md` перенесена `data/courses/` → `data/projects/`, `update_document_path` → `es_unavailable`, в каталоге `pending_sync`. Ход 4: модель показывает оба статуса из каталога |
| 5 | retry | `demo/dialog_retry_ru.txt` | `session_20261119_171548_621ff0` | 4 / 13 | ES запущен. Ход 1: найдены 1 `pending_sync` и 1 `error`. Ход 2: `update_document_path` (без аргумента `category`) → `category: projects`, `updated: 2`; upsert семинара → `created: 4`; после этого в каталоге 7 `indexed`, 7 `not_indexed`, ни одного `pending_sync`/`error`. Ход 3: поиск возвращает новые пути. Ход 4: путь и категория заметки в каталоге и в обеих выдержках ES совпадают (`data/projects/…`, `projects`) |
| — | итог | | | | `results/verify_report.json`: все 5 проверок пройдены, 14 документов в каталоге, 7 проиндексированы, 17 выдержек; `index_export.jsonl`, `catalog.json`, `data_tree.txt`, `workspace_snapshot/` |

**Основной диалог по ходам** (`session_20261119_170923_70e27b`; номера = реплики `demo/dialog_ru.txt`):

| Ход | Что сделала модель (по трассе) |
|---|---|
| 1 | `load_skill(organize_files)`, `ls` input и подпапок, `read` всех 11 непустых файлов, 12 `move`, 12 `catalog_update`. Темы: `courses` (3), `howto` (2), `meetings` (2), `projects` (3), `_unsorted` (пустой `empty_todo.txt`). Одноимённые `notes.md` стали `data/courses/es_seminar_analyzers_shards.md` и `data/meetings/meeting_2026-10-09_docsearch_weekly.md`. Неоднозначную заметку `search_quality_thoughts.md` модель положила в `projects` и записала обоснование в note. Prompt-injection строку из `meetings/notes.md` модель назвала пользователю и не выполнила. Файлы `_about.md` с описанием папок в этом прогоне модель не создавала |
| 2 | `ls data/projects` |
| 3 | `move` заметки `data/projects/` → `data/courses/`, причина записана в note |
| 4 | `ls input` (пусто) и ещё два `ls` по подпапкам `input/courses`, `input/meetings` из первого просмотра: они уже удалены после переноса, оба вернули `not_found` — модель сообщила, что input пуст |
| 5 | `load_skill(index_excerpts)`, 5 документов (заметка про качество поиска, лекция про инвертированный индекс, семинар по ES, DocSearch, инструкция по ES в Docker), 5 `build_excerpts`, один `upsert_excerpts` → `created: 10`, проверочный `search_excerpts` |
| 6 | `search_excerpts` («индекс инвертированный шарды») → 6 выдержек из 4 документов, затем `read` исходной лекции |
| 7 | `build_excerpts` без диапазонов (`reused_previous_ranges: true`) → `upsert_excerpts` → `created: 0, updated: 2, deleted_stale: 0` |
| 8 | `get_document_excerpts` → 2 выдержки; `index_stats` → всего 10, дублей нет |
| 9 | `move` лекции в `data/search/` → `action_required`; `update_document_path` **без** `category` → сервер вернул `category: search`, `updated: 2` (исправленная ошибка); `search_excerpts` уже показывает новый путь и категорию |
| 10 | `search_excerpts("BM25")` → 3 выдержки, путь лекции `data/search/ir_lecture_inverted_index.md` |
| пауза | CLI ждёт на `@wait_for_files`; файлы скопировал процесс PowerShell PID 37436, CLI PID 37672 (`results/late_files_added.json`, событие `external_files_added` в трассе) |
| 11 | новый `ls input` → 2 файла, `read` обоих, `move` → `data/meetings/meeting_2026-11-10_retro.md` и `data/search/vector_search_lecture.md`, `catalog_update`; уже разобранные файлы не тронуты |
| 12 | `build_excerpts` + `upsert_excerpts` новой лекции → `created: 3`, проверочный поиск |
| 13 | `head(n=0)` → `invalid_argument` |
| 14 | `read("../../secret.txt")` → `outside_workspace` |
| 15 | `read("input/no_such_file.md")` → `not_found` |
| 16 | `catalog_view`, `ls data`: пустой файл и два `notes.md` описаны, ничего не потеряно |

Skills загружаются выборочно: в main — `organize_files` в ходе 1 и `index_excerpts` в ходе 5, каждый один раз за
сессию; в errors — `index_excerpts` (ход 2) и `organize_files` (ход 3); в retry — `index_excerpts` (ход 2).
В сессиях limit и guard skills не загружались. История корректна во всех ходах всех пяти сессий.
В `traces/` только эти пять сессий: трассы прошлых прогонов (`session_20261115_*`) скрипт удалил, коммит f6efd67.

Сводную таблицу по всем ходам и чек-лист сценариев по трассам генерирует `scripts/summarize_run.py` в
`results/RUN_SUMMARY.md` (в этом прогоне все 16 пунктов отмечены). `results/run_manifest.json` — порядок сессий и их
трассы. Ожидаемое итоговое состояние описано в `fixtures/EXPECTED.md`, фактическое с ним совпадает. Совпадение пути и
категории каждой из 17 выдержек `index_export.jsonl` с каталогом: 0 расхождений.

Ходы 13–14 основного диалога просят вызвать tool. В этом прогоне модель вызвала его оба раза. В прошлом прогоне
она отказала сама, без вызова. Независимо от поведения модели, гарантированная демонстрация — сессия guard: там
каждая реплика явно требует вызвать инструмент и показать его ответ.

## 10. Тесты и проверочные сценарии

```powershell
python -m pytest -rs                 # офлайн + интеграция с ES (если ES запущен)
python scripts/verify.py             # проверки результата после демо-диалога
```

| Тестовый файл | Что проверяет |
|---|---|
| `tests/test_tools_fs.py` | контракты read/head/tail/grep/ls/write/move на фиксированных файлах, конфликт имён, дубль, пустой файл, `../`, абсолютный путь, junction наружу (всегда на Windows), настоящий symlink наружу (где symlink разрешены) |
| `tests/test_web_fetch.py` | `web_fetch` на локальном HTTP-сервере: текст, HTML, редирект, 404, таймаут, лимит размера, бинарный ответ |
| `tests/test_catalog_skills_middleware.py` | `build_excerpts`, skills, валидация выдержек (включая `category`), статусы `indexed/error/pending_sync`, категория после `update_document_path`, проверка категории в `verify.py`, таймаут и лимит ToolGuard |
| `tests/test_agent_offline.py` | топология графа, история нескольких ходов без дублей, переходы model→tools→model, ошибка как наблюдение, остановка по лимиту вызовов и по `recursion_limit` с ремонтом истории |
| `tests/test_cli_script.py` | процесс CLI в режиме `--script`: пауза `@wait_for_files`, файлы добавляет **другой процесс**, агент видит их повторным `ls`; зацикленный ход помечен `stopped_by=model_call_limit`; при лимитах по умолчанию первым срабатывает лимит вызовов модели |
| `tests/test_mcp_es.py` | реальный MCP-сервер и ES: идемпотентный upsert, replace, поиск (русская морфология), обновление пути и категории после переноса, недоступный ES, сквозной прогон агента со сценарной моделью |

Агентные тесты используют `ScriptedChatModel` / `DryRunChatModel`, детерминированные замены LLM. Это проверка
библиотечного графа и инфраструктуры, а **не** демонстрационные трассы: трассы для сдачи пишутся с реальной моделью
`qwen3.8-27b`. Тесты `test_mcp_es.py` пропускаются (`skipped`), если Elasticsearch не запущен.

**Покрытие проверочных сценариев задания** (номера ходов — пользовательские реплики `demo/dialog_ru.txt`,
они же номера ходов в трассе main и в `results/RUN_SUMMARY.md`):

| Сценарий | Где проверяется |
|---|---|
| Первичная организация | main, ход 1; `scripts/verify.py` (проверки 1–3) |
| Диалог и просмотр, история без дублей | main, ходы 2 и 4, `/check` после ходов 1 и 16, `/history`; `test_turns_history_and_transitions` |
| Перенос по уточнению | main, ход 3 («Перенеси заметку про поиск в папку курсов»); `test_move_input_to_data_and_between_folders` |
| Пополнение input | main: пауза `@wait_for_files` после хода 10, файлы копирует отдельный процесс PowerShell (`results/late_files_added.json`), ход 11 «Я добавил файлы, разбери новые» (повторный `ls`), ход 12 — индексация новой лекции; `test_cli_script_with_external_late_files_and_loop_limit`, `test_ls_stable_and_fresh` |
| Индексация и поиск через MCP | main, ход 5 (`upsert_excerpts`: 5 документов, `created: 10`) и ход 6 (`search_excerpts` + `read` исходного файла); `verify.py` (проверка 4); `test_mcp_upsert_idempotent_search_and_path_update` |
| Повтор и перенос после индексации | main, ходы 7–10: повтор `created: 0, updated: 2`, по-прежнему 2 выдержки, перенос в `search` + `update_document_path` без `category` → `category: search`, поиск по новому пути; `test_end_to_end_scripted_agent_with_real_mcp`, `test_update_path_without_category_follows_folder` |
| Skills | `load_skill` в трассе main: ход 1 — `organize_files`, ход 5 — `index_excerpts`, повторно не загружаются; `test_skills_metadata_and_on_demand_load` |
| Конфликты и ошибки | одноимённые `notes.md` и пустой файл (main, ходы 1 и 16); ошибки tools — сессия guard (каждая реплика вызывает tool); main, ходы 13–15 (в этом прогоне tool вызван: `invalid_argument`, `outside_workspace`, `not_found`); недоступный ES — сессии errors + retry (`error`/`pending_sync` → `indexed`); тесты path guard |
| Контракты tools | `test_tools_fs.py`, `test_web_fetch.py` |
| Граф и завершение | раздел 4, `transitions` в трассах; сессия limit — реальное зацикливание (25 вызовов `ls`), остановленное `ModelCallLimitMiddleware` (`stopped_by: model_call_limit`); `test_looping_*`, `test_model_call_limit_marked_in_trace`; `/exit` в конце каждой сессии |

## 11. Ограничения

- LLM-сервер ИТМО доступен только из сети университета; без него работают только тесты.
- История CLI живёт в памяти процесса (`InMemorySaver`); восстановление после перезапуска — задача лабы 2.
- Новые файлы обнаруживаются повторным `ls` по просьбе пользователя; фонового watcher нет (по заданию).
- Каталог ведёт код: агент меняет только title/description/note, остальное недоступно для правки моделью.
  `write` в `catalog.json` запрещён намеренно, чтобы модель не испортила ID и hash.
- Замена содержимого уже каталогизированного файла и очистка старых версий выдержек не поддерживаются (по заданию).
  `build_excerpts` в таком случае возвращает `hash_mismatch`.
- `os.link`+`unlink` в `move`: при аварии процесса между двумя вызовами может остаться второе имя файла; дубль виден
  через `duplicate_of` и `verify.py`.
- Таймаут tool прерывает ожидание, но синхронный tool в потоке-исполнителе дорабатывает в фоне.
- Тест настоящего symlink на Windows требует режима разработчика (или прав администратора); без него он пропускается,
  а выход через точку повторной обработки NTFS проверяется на directory junction (раздел 3).
- `traces/` и `results/` пересоздаются только целиком, скриптом `run_all_demos.ps1` (нужна сеть ИТМО); частичного
  перезапуска отдельных сессий больше нет, чтобы результаты всегда соответствовали одной цепочке.
- Индекс учебный: одна нода, без аутентификации, доступен только на `127.0.0.1`.
