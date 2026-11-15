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

## Файлы для добавления во время сессии `fixtures/late/`
- meeting_2026-11-10_retro.md → meetings
- vector_search_lecture.md → courses

## Ожидаемые проверки (`python scripts/verify.py`)
1. Каждый файл присутствует ровно один раз (по sha256) в data/ или input/ — ничего не потеряно и не скопировано.
2. ≥ 3 тематических папок в data/.
3. Каталог: уникальные document_id, current_path существует, hash совпадает, category = папка.
4. Elasticsearch (через MCP): ≥ 3 документов с выдержками; текст выдержек совпадает со строками файла; current_path совпадает с каталогом; нет дублей диапазонов; число выдержек = каталогу.

Ручные проверки по трассе: повтор индексации даёт `created=0`; после переноса поиск возвращает новый путь;
ошибки (n=0, `../`, несуществующий файл, недоступный MCP) — контролируемые наблюдения; `organize_files` и
`index_excerpts` загружаются выборочно (load_skill), каждый — под свой запрос.
