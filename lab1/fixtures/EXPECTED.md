# Тестовые данные и ожидаемые проверки

## Исходный набор `fixtures/input/` (12 файлов, UTF-8, каждый < 2 КиБ)

| Файл | Ожидаемая тема | Особенность |
|---|---|---|
| ir_lecture_inverted_index.md | courses | индекс, BM25 (для поиска «индексы», «BM25») |
| mlops_lecture_monitoring.md | courses | |
| agents_seminar_react.txt | courses | TXT |
| courses/notes.md | courses | одноимённый с meetings/notes.md |
| meetings/notes.md | meetings | одноимённый; содержит строку с prompt injection, она должна остаться данными |
| meeting_2026-10-02_kickoff.md | meetings | |
| project_docsearch.md | projects | |
| project_recsys.md | projects | |
| howto_elasticsearch_docker.md | howto | |
| howto_git_branches.txt | howto | |
| search_quality_thoughts.md | courses или projects | неоднозначный («заметка про поиск»): нужен уточняющий вопрос или обоснование |
| empty_todo.txt | `_unsorted` | пустой файл: остаётся в каталоге с пояснением, не индексируется |

Колонка «Ожидаемая тема» задаёт тему, а не обязательное имя папки. Системный промпт имён папок не задаёт, но в
`skills/organize_files/SKILL.md` они приведены как примеры (`courses`, `projects`, `meetings`, `howto`), и в прогоне
22.11.2026 модель взяла именно их, плюс `_unsorted` для пустого файла. Одноимённые `notes.md` легли в разные папки
под своими именами.

## Файлы для добавления во время сессии `fixtures/late/`
- meeting_2026-11-10_retro.md: в meetings
- vector_search_lecture.md: в courses или search (папку `search` пользователь просит создать в ходе 9 основного диалога)

`scripts/run_all_demos.ps1` копирует их в `workspace/input/` из отдельного процесса PowerShell, пока
CLI стоит на паузе `@wait_for_files`.

## Ожидаемое итоговое состояние после цепочки `run_all_demos.ps1`
(сессии main, limit, guard, errors, mcp_down, retry, см. README, раздел 9)

| Документ | Где после цепочки | Почему |
|---|---|---|
| search_quality_thoughts.md | `data/projects/`, category `projects`, `indexed` | main: в ходе 1 в projects, в ходе 3 перенос в courses; errors: перенос в projects при остановленном ES (`pending_sync`); retry: путь и категория в индексе исправлены |
| ir_lecture_inverted_index.md | `data/search/`, category `search`, `indexed` | ход 9 main: перенос проиндексированного файла и `update_document_path` |
| agents_seminar_react.txt | `data/courses/`, `indexed` | errors: upsert упал (`error`); retry: проиндексирован |
| howto_git_branches.txt | `data/guides/`, category `guides`, `indexed` | mcp_down: индексация не удалась (`mcp_unavailable`, статус `error`), файл перенесён в новую папку guides; retry: проиндексирован |
| project_recsys.md | `data/projects/`, `not_indexed` | в цепочке не переносится и не индексируется |
| `data/howto/_about.md` | `data/howto/`, вне каталога | создаётся в guard успешным `write`; это не документ из `fixtures`, поэтому в каталог не попадает, а `catalog_view` показывает его в `reconcile.unregistered_data_files` |
| остальные файлы | по темам из таблицы выше | |

Ни один документ не должен остаться в статусе `pending_sync` или `error`, а у каждой выдержки в индексе
`current_path` и `category` должны совпадать с каталогом (`results/RUN_SUMMARY.md`, строка «расходятся с каталогом: 0»).
Проиндексированы должны быть документы, которые индексирует сам сценарий: в main не меньше трёх документов
про поиск и новая лекция из `fixtures/late/`, в retry семинар ReAct и инструкция по git. Остальные документы
остаются `not_indexed`, пустой `empty_todo.txt` не индексируется никогда.

### Что получилось в прогоне 22.11.2026 (`results/catalog.json`)

14 документов: 8 `indexed`, 6 `not_indexed`, `pending_sync` и `error` нет. В индексе 19 выдержек, расхождений
пути и категории с каталогом нет, `scripts/verify.py` прошёл все 5 проверок.

| Документ (`current_path`) | Статус | Выдержек |
|---|---|---|
| `data/courses/agents_seminar_react.txt` | `indexed` | 4 |
| `data/courses/notes.md` | `indexed` | 2 |
| `data/guides/howto_git_branches.txt` | `indexed` | 2 |
| `data/howto/howto_elasticsearch_docker.md` | `indexed` | 2 |
| `data/projects/project_docsearch.md` | `indexed` | 2 |
| `data/projects/search_quality_thoughts.md` | `indexed` | 2 |
| `data/search/ir_lecture_inverted_index.md` | `indexed` | 2 |
| `data/search/vector_search_lecture.md` | `indexed` | 3 |
| `data/_unsorted/empty_todo.txt` | `not_indexed` | 0 |
| `data/courses/mlops_lecture_monitoring.md` | `not_indexed` | 0 |
| `data/meetings/meeting_2026-10-02_kickoff.md` | `not_indexed` | 0 |
| `data/meetings/meeting_2026-11-10_retro.md` | `not_indexed` | 0 |
| `data/meetings/notes.md` | `not_indexed` | 0 |
| `data/projects/project_recsys.md` | `not_indexed` | 0 |

Это совпадает с ожиданиями выше. Инструкция по git после mcp_down была в статусе `error` с `index_error` про
недоступный MCP-сервер (`results/catalog_after_mcp_down.json`), и retry её проиндексировал. `data/howto/_about.md`
в каталог не попал, `catalog_view` в retry показывает его в `reconcile.unregistered_data_files`.

## Ожидаемые проверки (`python scripts/verify.py`)
1. Каждый файл есть ровно один раз (по sha256) в data/ или input/: ничего не потеряно и не скопировано.
2. В data/ не меньше 3 тематических папок.
3. Каталог: уникальные document_id, current_path существует, hash совпадает, category равна папке.
4. Elasticsearch (через MCP): не меньше 3 документов с выдержками; текст выдержек совпадает со строками файла;
   current_path и category совпадают с каталогом (расхождение допустимо только при записанном `pending_sync`);
   нет дублей диапазонов; число выдержек равно числу в каталоге.

Что проверяется вручную по трассе (сводка в `results/RUN_SUMMARY.md`): повтор индексации даёт `created=0`; после
переноса поиск возвращает новый путь; ошибки (n=0, `../`, несуществующий файл, недоступный ES) приходят модели
наблюдениями; `organize_files` и `index_excerpts` загружаются через `load_skill`, каждый под свой запрос;
зацикленный ход останавливается лимитом (`turn_end.stopped_by`).
