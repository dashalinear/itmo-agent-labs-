# Тестовые данные и ожидаемые проверки

## Исходный набор `fixtures/input/` (12 файлов, UTF-8, каждый < 2 КиБ)

| Файл | Ожидаемая тема | Особенность |
|---|---|---|
| ir_lecture_inverted_index.md | courses | индекс, BM25 (для поиска «индексы», «BM25») |
| mlops_lecture_monitoring.md | courses | |
| agents_seminar_react.txt | courses | TXT |
| courses/notes.md | courses | **одноимённый** с meetings/notes.md |
| meetings/notes.md | meetings | **одноимённый**; содержит prompt-injection строку (должна остаться данными) |
| meeting_2026-10-02_kickoff.md | meetings | |
| project_docsearch.md | projects | |
| project_recsys.md | projects | |
| howto_elasticsearch_docker.md | howto | |
| howto_git_branches.txt | howto | |
| search_quality_thoughts.md | courses **или** projects | **неоднозначный** («заметка про поиск»): нужен уточняющий вопрос или обоснование |
| empty_todo.txt | `_unsorted` | **пустой** файл: остаётся в каталоге с пояснением, не индексируется |

Имена папок выбирает модель по содержимому; подсказка в системном промпте — `courses`, `projects`, `meetings`, `howto`.

## Файлы для добавления во время сессии `fixtures/late/`
- meeting_2026-11-10_retro.md → meetings
- vector_search_lecture.md → courses или search (папку `search` пользователь создаёт в ходе 9 основного диалога)

`scripts/run_all_demos.ps1` копирует их в `workspace/input/` из **отдельного процесса** PowerShell, пока
CLI стоит на паузе `@wait_for_files` (сам CLI ничего не копирует).

## Ожидаемое итоговое состояние после цепочки `run_all_demos.ps1`
(main → limit → guard → errors → retry, см. README, раздел 9)

| Документ | Где после цепочки | Почему |
|---|---|---|
| search_quality_thoughts.md | `data/projects/`, category `projects`, `indexed` | ход 3 main: перенос в courses; сессия errors: перенос в projects при остановленном ES (`pending_sync`); сессия retry: путь и категория в индексе исправлены |
| ir_lecture_inverted_index.md | `data/search/`, category `search`, `indexed` | ход 9 main: перенос проиндексированного файла и `update_document_path` |
| agents_seminar_react.txt | `data/courses/`, `indexed` | сессия errors: upsert упал (`error`); retry: проиндексирован |
| project_recsys.md | `data/projects/` | в цепочке не переносится |
| остальные файлы | по темам из таблицы выше | |

Ни один документ не должен остаться в статусе `pending_sync` или `error`; у каждой выдержки в индексе
`current_path` и `category` совпадают с каталогом (`results/RUN_SUMMARY.md`, строка «расходятся с каталогом: 0»).

## Ожидаемые проверки (`python scripts/verify.py`)
1. Каждый файл присутствует ровно один раз (по sha256) в data/ или input/ — ничего не потеряно и не скопировано.
2. ≥ 3 тематических папок в data/.
3. Каталог: уникальные document_id, current_path существует, hash совпадает, category = папка.
4. Elasticsearch (через MCP): ≥ 3 документов с выдержками; текст выдержек совпадает со строками файла;
   current_path **и category** совпадают с каталогом (расхождение допустимо только при записанном `pending_sync`);
   нет дублей диапазонов; число выдержек = каталогу.

Ручные проверки по трассе (сводка — `results/RUN_SUMMARY.md`): повтор индексации даёт `created=0`; после
переноса поиск возвращает новый путь; ошибки (n=0, `../`, несуществующий файл, недоступный ES) — контролируемые
наблюдения; `organize_files` и `index_excerpts` загружаются выборочно (load_skill), каждый — под свой запрос;
зацикленный ход остановлен лимитом (`turn_end.stopped_by`).
