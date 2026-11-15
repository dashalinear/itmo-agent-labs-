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
| `scripts/` | подготовка workspace, добавление файлов, проверка, экспорт и восстановление индекса, экспорт графа |
| `demo/` | сценарии диалога для трасс |
| `traces/` | трассы сессий (`*.jsonl` и `*.md`) |
| `results/` | снимок workspace, каталог, выгрузка индекса, отчёт `verify` |

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

Чтобы добавить файлы во время открытой сессии, во **втором терминале** выполните
`python scripts/add_late_files.py` (или скопируйте `fixtures/late/*` в `workspace/input/` вручную).
Затем скажите агенту: «Я добавил файлы, разбери новые».

**Демонстрация одним прогоном** (реплики из файла; строка `@add_late_files` — ручное действие пользователя посреди сессии):

```powershell
python -m organizer.cli --script demo/dialog_ru.txt
python scripts/verify.py          # детерминированные проверки → results/verify_report.json
python scripts/export_index.py    # results/index_export.jsonl, catalog.json, data_tree.txt, workspace_snapshot/
```

**Все трассы одной командой** (из сети ИТМО): проверка LLM, запуск ES, сброс workspace, основной диалог,
`verify` и экспорт, сценарий лимита, сценарий ошибок при остановленном ES, повторная синхронизация,
поиск токена в результатах, коммит и пуш `traces/` и `results/`:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1
```

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

**Лимиты вывода**: каждый tool ограничен `MAX_TOOL_OUTPUT_CHARS` (12000) с флагом `truncated` и подсказкой дочитать
через head/tail/grep. Кроме того, `ToolGuardMiddleware` обрезает результат **любого** tool, включая MCP.

**Лимиты выполнения**: `AGENT_MODEL_CALLS_PER_TURN=25` (`ModelCallLimitMiddleware`, `exit_behavior="end"`),
`recursion_limit=100` шагов графа за ход, таймаут tool 30 с (`ToolGuardMiddleware`), таймаут хода 600 с,
таймаут и retry LLM (`ChatOpenAI(timeout=120, max_retries=2)`), таймаут ES 10 с.

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
| `update_document_path(document_id, new_path, category)` | `update_by_query` для всех выдержек документа после переноса |
| `get_document_excerpts`, `index_stats`, `delete_document_excerpts` | проверка отсутствия дублей, статистика, очистка |

Ручной запуск сервера: `python -m mcp_es.server` (stdio). Параметры берутся из окружения: `ES_URL`, `ES_INDEX`, `ES_TIMEOUT_S`.

**Согласованность** (`CatalogSyncMiddleware`):
- *до* `upsert_excerpts` каждая выдержка сверяется с каталогом и файлом: `current_path`, `content_hash`, текст ровно
  совпадает со строками `start_line..end_line`. Иначе запись **не выполняется** (`excerpt_validation_failed`);
- *после* `ok=true` документ получает статус `indexed`, в каталог записывается список диапазонов; при ошибке — `error`;
- `move` проиндексированного документа ставит статус `pending_sync`. Успешный `update_document_path` возвращает `indexed`,
  неудачный оставляет `pending_sync` с текстом ошибки, повтор исправляет путь;
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
@add_late_files
> Я добавил файлы, разбери новые.
```

Трасса каждой сессии пишется в два файла:
- `traces/session_*.jsonl` — события `session_start`, `user_turn` (с `run_id`), `graph_step` (узел, сообщения,
  tool calls с `id/name/args`, tool results с `tool_call_id/status`), `user_action`, `turn_end` (`transitions`,
  проверка истории, ошибка, длительность, usage), `session_end`;
- `traces/session_*.md` — то же в читаемом виде.

Токены вида `sk-…` и `Bearer …` в трассах маскируются.

| Сценарий | Файл |
|---|---|
| основной диалог (организация, просмотр, перенос, индексация, поиск, повтор, пополнение, ошибки tools) | `demo/dialog_ru.txt` |
| недоступный MCP/ES | `demo/dialog_errors_ru.txt` (при остановленном ES) |
| повторная синхронизация после восстановления ES | `demo/dialog_retry_ru.txt` |
| завершение зацикленного хода по лимиту | `demo/dialog_limit_ru.txt` (`AGENT_MODEL_CALLS_PER_TURN=3`) |

## 10. Тесты и проверочные сценарии

```powershell
python -m pytest -rs                 # офлайн + интеграция с ES (если ES запущен)
python scripts/verify.py             # проверки результата после демо-диалога
```

| Тестовый файл | Что проверяет |
|---|---|
| `tests/test_tools_fs.py` | контракты read/head/tail/grep/ls/write/move на фиксированных файлах, конфликт имён, дубль, пустой файл, `../`, абсолютный путь, symlink/junction наружу |
| `tests/test_web_fetch.py` | `web_fetch` на локальном HTTP-сервере: текст, HTML, редирект, 404, таймаут, лимит размера, бинарный ответ |
| `tests/test_catalog_skills_middleware.py` | `build_excerpts`, skills, валидация выдержек, статусы `indexed/error/pending_sync`, таймаут и лимит ToolGuard |
| `tests/test_agent_offline.py` | топология графа, история нескольких ходов без дублей, переходы model→tools→model, ошибка как наблюдение, остановка по лимиту вызовов и по `recursion_limit` с ремонтом истории |
| `tests/test_mcp_es.py` | реальный MCP-сервер и ES: идемпотентный upsert, replace, поиск (русская морфология), обновление пути, недоступный ES, сквозной прогон агента со сценарной моделью |

Агентные тесты используют `ScriptedChatModel`, детерминированную замену LLM. Это проверка библиотечного графа
и инфраструктуры, а **не** демонстрационные трассы: трассы для сдачи пишутся с реальной моделью `qwen3.8-27b`.

**Покрытие проверочных сценариев задания:**

| Сценарий | Где проверяется |
|---|---|
| Первичная организация | `demo/dialog_ru.txt` ход 1, `scripts/verify.py` (проверки 1–3) |
| Диалог и просмотр, история без дублей | ходы 2–4, `/check`, `test_turns_history_and_transitions` |
| Перенос по уточнению | ход «Перенеси заметку про поиск…», `test_move_input_to_data_and_between_folders` |
| Пополнение input | `@add_late_files` и «Я добавил файлы…», `test_ls_stable_and_fresh` |
| Индексация и поиск через MCP | ходы 6–7, `verify.py` (проверка 4), `test_mcp_upsert_idempotent_search_and_path_update` |
| Повтор и перенос после индексации | ходы 8–11, `test_end_to_end_scripted_agent_with_real_mcp` |
| Skills | `load_skill` в трассе, `test_skills_metadata_and_on_demand_load` |
| Конфликты и ошибки | одноимённые `notes.md`, пустой файл, ходы с `n=0`, `../`, несуществующим файлом, `dialog_errors_ru.txt`, тесты path guard |
| Контракты tools | `test_tools_fs.py`, `test_web_fetch.py` |
| Граф и завершение | раздел 4, `transitions` в трассе, `dialog_limit_ru.txt`, `test_looping_*`, `/exit` |

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
- Тест symlink на Windows требует Developer Mode; без него эквивалентная проверка выполняется на directory junction.
- Индекс учебный: одна нода, без аутентификации, доступен только на `127.0.0.1`.
