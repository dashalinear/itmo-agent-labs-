# Сводка демо-прогона лабораторной 1

Файл создан `scripts/summarize_run.py` по трассам этого прогона (`scripts/run_all_demos.ps1`).
Начало прогона: 2026-11-21 00:16:47 +03:00. Модель: `qwen3.8-27b`.

## Цепочка сессий (в порядке запуска, все из чистого workspace и пустого индекса)

| # | Сценарий | Диалог | Трасса | Ходов | Вызовов модели | Длительность, с |
|---|---|---|---|---|---|---|
| 1 | main: organization, browsing, moves, indexing, search, late files, errors | `demo/dialog_ru.txt` | `session_20261121_001656_0e845c` | 16 | 52 | 192 |
| 2 | limit: looping request stopped by the execution limit | `demo/dialog_limit_ru.txt` | `session_20261121_002038_5a9723` | 2 | 26 | 30 |
| 3 | guard: tool contract errors, junction outside the workspace, web_fetch 200/404/timeout | `demo/dialog_guard_ru.txt` | `session_20261121_002115_e002dd` | 17 | 34 | 59 |
| 4 | errors: Elasticsearch unavailable: error and pending_sync | `demo/dialog_errors_ru.txt` | `session_20261121_002224_e7a159` | 4 | 13 | 48 |
| 5 | mcp_down: MCP server unavailable: indexing fails, organization works | `demo/dialog_mcp_down_ru.txt` | `session_20261121_002352_145d67` | 3 | 12 | 23 |
| 6 | retry: retry after Elasticsearch is back | `demo/dialog_retry_ru.txt` | `session_20261121_002420_5324f8` | 4 | 12 | 50 |

Поздние файлы скопировал отдельный процесс PowerShell (PID 19712, CLI PID 2148) в 2026-11-21T00:19:15.2076333+03:00: input/meeting_2026-11-10_retro.md, input/vector_search_lecture.md.

## main: organization, browsing, moves, indexing, search, late files, errors

Трасса `traces/session_20261121_001656_0e845c.jsonl` (и `.md`), MCP: MCP 'es' connected: upsert_excerpts, search_excerpts, update…, лимиты: model_calls_per_turn=25, recursion_limit=110.

| Ход | Реплика пользователя | Вызовы tools | Ошибки tools (tool:type) | Остановка | История |
|---|---|---|---|---|---|
| 1 | Посмотри input: какие документы там есть? Разложи их по темам в data. | catalog_update×12, catalog_view, load_skill, ls×5, move×12, read×11 | — | — | OK |
| 2 | Что теперь лежит в папке с проектами? | ls | — | — | OK |
| 3 | Перенеси заметку про поиск в папку курсов. | catalog_update, move | — | — | OK |
| 4 | Покажи, что осталось в input. | ls | — | — | OK |
| 5 | Выдели полезные выдержки из файлов про поиск и сохрани их с метаданными в Elastic. Возьми … | build_excerpts×4, load_skill, read×4, search_excerpts, upsert_excerpts | — | — | OK |
| 6 | Найди в Elastic, что у нас есть про индексы, и покажи исходный файл. | read, search_excerpts | — | — | OK |
| 7 | Повтори индексацию лекции про инвертированный индекс — файл не менялся. | build_excerpts, upsert_excerpts | — | — | OK |
| 8 | Сколько теперь выдержек у этой лекции в индексе? Дубли появились? | get_document_excerpts, index_stats | — | — | OK |
| 9 | Перенеси лекцию про инвертированный индекс в отдельную папку search и обнови индекс. | catalog_view, get_document_excerpts, move, update_document_path | — | — | OK |
| 10 | Найди в Elastic BM25 и скажи, по какому пути теперь лежит файл. | search_excerpts | — | — | OK |
| — | *действие: external_files_added input/meeting_2026-11-10_retro.md, input/vector_search_lecture.md (another process (this CLI only waited, copied nothing))* | | | | |
| 11 | Я добавил файлы, разбери новые. | catalog_update×2, ls, move×2, read×2 | — | — | OK |
| 12 | Добавь новую лекцию в Elastic. | build_excerpts, read, search_excerpts, upsert_excerpts | — | — | OK |
| 13 | Вызови инструмент head для описания проекта рекомендательной системы с n=0 и покажи, что о… | head | head:invalid_argument | — | OK |
| 14 | Вызови инструмент read с путём ../../secret.txt и покажи, что он вернул. | — | — | — | OK |
| 15 | Прочитай файл input/no_such_file.md | read | read:not_found | — | OK |
| 16 | Есть ли пустые файлы или файлы с одинаковыми именами? Что с ними сделано? | catalog_view, ls | — | — | OK |

Команды CLI (события `cli_command`):

- после хода 1: `/check` -> ok, "history_ok=True, messages=50"
- после хода 10: `@wait_for_files input/meeting_2026-11-10_retro.md input/vector_search_lecture.md` -> ok, {"appeared": ["input/meeting_2026-11-10_retro.md", "input/vector_search_lecture.md"]}
- после хода 16: `/check` -> ok, "history_ok=True, messages=151"
- после хода 16: `/history` -> ok, {"messages": 151, "by_type": {"HumanMessage": 16, "AIMessage": 52, "ToolMessage": 83}, "tool_calls": 83}
- после хода 16: `/exit` -> ok, "exit"

Завершение сессии (`session_end.ended_by`): `exit_command (/exit)`.

## limit: looping request stopped by the execution limit

Трасса `traces/session_20261121_002038_5a9723.jsonl` (и `.md`), MCP: MCP 'es' connected: upsert_excerpts, search_excerpts, update…, лимиты: model_calls_per_turn=25, recursion_limit=110.

| Ход | Реплика пользователя | Вызовы tools | Ошибки tools (tool:type) | Остановка | История |
|---|---|---|---|---|---|
| 1 | Я сейчас копирую в input отчёт final_report.md, он появится через пару минут. Проверяй ls … | ls×25 | — | model_call_limit | OK |
| 2 | Почему предыдущий ход остановился и сколько раз ты успел вызвать ls? | — | — | — | OK |

Команды CLI (события `cli_command`):

- после хода 1: `/check` -> ok, "history_ok=True, messages=52"
- после хода 2: `/check` -> ok, "history_ok=True, messages=54"
- после хода 2: `/exit` -> ok, "exit"

Завершение сессии (`session_end.ended_by`): `exit_command (/exit)`.

## guard: tool contract errors, junction outside the workspace, web_fetch 200/404/timeout

Трасса `traces/session_20261121_002115_e002dd.jsonl` (и `.md`), MCP: MCP 'es' connected: upsert_excerpts, search_excerpts, update…, лимиты: model_calls_per_turn=25, recursion_limit=110.

| Ход | Реплика пользователя | Вызовы tools | Ошибки tools (tool:type) | Остановка | История |
|---|---|---|---|---|---|
| 1 | Через catalog_view найди пути инструкции по веткам git, протокола kickoff-встречи и проток… | catalog_view | — | — | OK |
| 2 | Вызови инструмент head для инструкции по веткам git с n=3 и покажи первые строки. | head | — | — | OK |
| 3 | Вызови инструмент tail для протокола ретроспективы с n=2 и покажи последние строки. | tail | — | — | OK |
| 4 | Вызови инструмент write: в папке, где лежит инструкция по веткам git, создай файл _about.m… | write | — | — | OK |
| 5 | Вызови инструмент head для инструкции по веткам git с n=0 и покажи, что он вернул. | head | head:invalid_argument | — | OK |
| 6 | Вызови инструмент tail для инструкции по веткам git с n=-2 и покажи результат. | tail | tail:invalid_argument | — | OK |
| 7 | Вызови read с путём ../../secret.txt и покажи, что вернул инструмент. | read | read:outside_workspace | — | OK |
| 8 | Вызови read с абсолютным путём C:\Windows\win.ini и покажи результат инструмента. | read | read:outside_workspace | — | OK |
| 9 | Вызови ls для input и затем ls для input/outside_link и покажи оба результата. | ls×2 | ls:outside_workspace | — | OK |
| 10 | Вызови read для input/outside_link/secret.txt и покажи результат. | read | read:outside_workspace | — | OK |
| 11 | Вызови grep рекурсивно по папке input с шаблоном outside-the-workspace и покажи результат. | grep | — | — | OK |
| 12 | Вызови move: перенеси инструкцию по веткам git в ../outside.txt и покажи результат. | move | move:outside_workspace | — | OK |
| 13 | Вызови move: источник — протокол kickoff-встречи, назначение — ТОЧНЫЙ путь существующего п… | move | move:name_conflict | — | OK |
| 14 | Вызови web_fetch для http://127.0.0.1:18765/page.html и покажи статус, final_url, title и … | web_fetch | — | — | OK |
| 15 | Вызови web_fetch для http://127.0.0.1:18765/missing и покажи результат инструмента. | web_fetch | web_fetch:http_error | — | OK |
| 16 | Вызови web_fetch для http://127.0.0.1:18765/slow и покажи результат инструмента. | web_fetch | web_fetch:timeout | — | OK |
| 17 | Вызови web_fetch для http://127.0.0.1:9/ и покажи результат. | web_fetch | web_fetch:network_error | — | OK |

Команды CLI (события `cli_command`):

- после хода 17: `/check` -> ok, "history_ok=True, messages=69"
- после хода 17: `/exit` -> ok, "exit"

Завершение сессии (`session_end.ended_by`): `exit_command (/exit)`.

## errors: Elasticsearch unavailable: error and pending_sync

Трасса `traces/session_20261121_002224_e7a159.jsonl` (и `.md`), MCP: MCP 'es' connected: upsert_excerpts, search_excerpts, update…, лимиты: model_calls_per_turn=25, recursion_limit=110.

| Ход | Реплика пользователя | Вызовы tools | Ошибки tools (tool:type) | Остановка | История |
|---|---|---|---|---|---|
| 1 | Покажи содержимое data. | ls×7 | — | — | OK |
| 2 | Положи семинар про паттерн ReAct в Elastic. | build_excerpts, load_skill, read, upsert_excerpts | upsert_excerpts:es_unavailable | — | OK |
| 3 | Перенеси заметку про оценку качества поиска (search_quality_thoughts.md) в папку projects … | move, update_document_path | update_document_path:es_unavailable | — | OK |
| 4 | Покажи статусы индексации этих двух документов в каталоге. | catalog_view×2 | — | — | OK |

Команды CLI (события `cli_command`):

- после хода 4: `/check` -> ok, "history_ok=True, messages=32"
- после хода 4: `/exit` -> ok, "exit"

Завершение сессии (`session_end.ended_by`): `exit_command (/exit)`.

## mcp_down: MCP server unavailable: indexing fails, organization works

Трасса `traces/session_20261121_002352_145d67.jsonl` (и `.md`), MCP: MCP unavailable (FileNotFoundError: [WinError 2] The system …, лимиты: model_calls_per_turn=25, recursion_limit=110.

| Ход | Реплика пользователя | Вызовы tools | Ошибки tools (tool:type) | Остановка | История |
|---|---|---|---|---|---|
| 1 | Положи инструкцию по веткам git в Elastic. | build_excerpts, catalog_view×2, load_skill, ls, read, upsert_excerpts | upsert_excerpts:mcp_unavailable | — | OK |
| 2 | Хорошо, тогда просто перенеси инструкцию по веткам git в новую папку data/guides. | catalog_update, move | — | — | OK |
| 3 | Покажи статус индексации и текущий путь инструкции по веткам git в каталоге. | catalog_view | — | — | OK |

Команды CLI (события `cli_command`):

- после хода 3: `/catalog` -> ok, {"documents": 14, "status_counts": {"not_indexed": 7, "error": 2, "indexed": 4, "pending_sync": 1}}
- после хода 3: `/check` -> ok, "history_ok=True, messages=25"
- после хода 3: `/exit` -> ok, "exit"

Завершение сессии (`session_end.ended_by`): `exit_command (/exit)`.

## retry: retry after Elasticsearch is back

Трасса `traces/session_20261121_002420_5324f8.jsonl` (и `.md`), MCP: MCP 'es' connected: upsert_excerpts, search_excerpts, update…, лимиты: model_calls_per_turn=25, recursion_limit=110.

| Ход | Реплика пользователя | Вызовы tools | Ошибки tools (tool:type) | Остановка | История |
|---|---|---|---|---|---|
| 1 | Покажи документы каталога со статусом pending_sync или error. | catalog_view×2 | — | — | OK |
| 2 | Синхронизируй индекс для этих документов: обнови пути, а если документ не проиндексирован … | build_excerpts×2, catalog_view×2, get_document_excerpts×2, load_skill, read×2, search_excerpts×2, update_document_path, upsert_excerpts×2 | — | — | OK |
| 3 | Найди в Elastic выдержки про оценку качества поиска и про паттерн ReAct и покажи пути к фа… | search_excerpts×2 | — | — | OK |
| 4 | Покажи для заметки про оценку качества поиска путь и категорию в каталоге и в выдержках El… | catalog_view, get_document_excerpts | — | — | OK |

Команды CLI (события `cli_command`):

- после хода 4: `/check` -> ok, "history_ok=True, messages=36"
- после хода 4: `/exit` -> ok, "exit"

Завершение сессии (`session_end.ended_by`): `exit_command (/exit)`.

## Проверки сценариев по трассам

- [x] main: 2 late files appeared in input/ from outside the CLI (external_files_added)
- [x] main: the turn after the pause calls ls again and moves the new files
- [x] main: load_skill called (organize_files + index_excerpts)
- [x] main: MCP upsert_excerpts and search_excerpts called
- [x] main: update_document_path after moving an indexed file
- [x] limit: the looping turn was stopped by an execution limit (model_call_limit, 25 model calls)
- [x] guard: a tool returned invalid_argument
- [x] guard: a tool returned outside_workspace
- [x] guard: a tool returned name_conflict
- [x] guard: web_fetch succeeded on the local fixture server
- [x] guard: a successful head call
- [x] guard: a successful tail call
- [x] guard: a successful write call
- [x] guard: web_fetch returned http_error
- [x] guard: web_fetch returned timeout
- [x] guard: web_fetch returned network_error
- [x] errors: MCP tools reported unavailable Elasticsearch
- [x] mcp_down: the CLI could not start the MCP server (session_start.mcp)
- [x] mcp_down: an index tool returned mcp_unavailable
- [x] mcp_down: the document whose indexing failed in this session has status error in the catalog (data/guides/howto_git_branches.txt)
- [x] mcp_down: file organization still works (a successful move)
- [x] main: history check OK in every turn
- [x] main: session ended by the exit command (session_end.ended_by=exit_command (/exit))
- [x] limit: history check OK in every turn
- [x] limit: session ended by the exit command (session_end.ended_by=exit_command (/exit))
- [x] guard: history check OK in every turn
- [x] guard: session ended by the exit command (session_end.ended_by=exit_command (/exit))
- [x] errors: history check OK in every turn
- [x] errors: session ended by the exit command (session_end.ended_by=exit_command (/exit))
- [x] mcp_down: history check OK in every turn
- [x] mcp_down: session ended by the exit command (session_end.ended_by=exit_command (/exit))
- [x] retry: history check OK in every turn
- [x] retry: session ended by the exit command (session_end.ended_by=exit_command (/exit))

## Итоговое состояние (results/)

- `results/symlink_test.txt` (выход через ссылки): real symlink test PASSED (symlinks are allowed on this machine)
- `results/http_fixture_requests.log`: локальный HTTP-сервер получил 3 запросов web_fetch: /page.html 200, /missing 404, /slow received, answering in 2…
- `results/verify_report_main.json`: files_present_once OK, topic_folders>=3 OK, catalog_consistent OK, es_consistent OK, indexed_docs>=3 OK
- `results/verify_report.json`: files_present_once OK, topic_folders>=3 OK, catalog_consistent OK, es_consistent OK, indexed_docs>=3 OK
- каталог: 14 документов, статусы: {'not_indexed': 7, 'indexed': 7}
- выгрузка индекса: 17 выдержек, 7 документов
- выдержки, у которых путь или категория расходятся с каталогом: 0

| Документ (current_path) | category | статус | выдержек в индексе |
|---|---|---|---|
| `data/_unsorted/empty_todo.txt` | _unsorted | not_indexed | 0 |
| `data/courses/agents_seminar_react.txt` | courses | indexed | 4 |
| `data/courses/es_seminar_analyzers_shards.md` | courses | indexed | 2 |
| `data/courses/mlops_lecture_monitoring.md` | courses | not_indexed | 0 |
| `data/guides/howto_git_branches.txt` | guides | indexed | 2 |
| `data/howto/howto_elasticsearch_docker.md` | howto | not_indexed | 0 |
| `data/meetings/meeting_2026-10-02_kickoff.md` | meetings | not_indexed | 0 |
| `data/meetings/meeting_2026-10-09_docsearch_weekly.md` | meetings | not_indexed | 0 |
| `data/meetings/meeting_2026-11-10_retro.md` | meetings | not_indexed | 0 |
| `data/projects/project_docsearch.md` | projects | indexed | 2 |
| `data/projects/project_recsys.md` | projects | not_indexed | 0 |
| `data/projects/search_quality_thoughts.md` | projects | indexed | 2 |
| `data/search/ir_lecture_inverted_index.md` | search | indexed | 2 |
| `data/search/vector_search_lecture.md` | search | indexed | 3 |
