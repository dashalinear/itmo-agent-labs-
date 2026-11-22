# История разработки лабы 3

README описывает текущее состояние системы и финальные прогоны. Здесь собраны прошлые прогоны (M1–M9, EXP1–EXP5), что в них сломалось и что я после этого поменяла. Все прогоны шли с `qwen3.8-27b` в сети ИТМО, окно 262 144 токена, рабочий лимит 28 000.

Трассы и результаты M7, M8, M9, EXP4 и EXP5 скрипт `final2` удалил из репозитория, когда запускал следующую попытку (он берёт первый свободный id и убирает прошлые попытки). Для них ниже только короткое описание. Таблицы по остальным задачам есть в `results/evaluation.md`.

## Прогоны по порядку

| Дата | Задачи | Что показали | Что пошло не так |
|---|---|---|---|
| 16–17.11 | ING1 | Загрузка 53 файлов в 9 папок, L1 для 52 документов, 242 вызова модели, 4 запуска из-за трёх отказов сервера ИТМО | Пауза пришлась на начало, когда переносов ещё не было |
| 16–17.11 | ING2, ING3 | Новые файлы из CLI (3 документа, 7 фрагментов); повтор без дублей | Повтор ING3 проверен только на пустой папке |
| 17.11 | M1 | Ходы 1–9 диалога | Проверка цитат вырезала «N: » внутри текста; модель около 150 раз повторила один grep и съела весь бюджет задачи (200 вызовов) |
| 17.11 | EXP1 | Partial по бюджету и resume, 47/47, 36/36 кодов | После полного покрытия 30 раз вызвал `submit_result({})` и остался `paused` |
| 17.11 | M2, M2-deep1, M2-exhaustive1 | Шесть сочетаний поиска в диалоге, deep с 9 evidence, обход 47/47 и 36/36 | Deep остановлен лимитом 30 вызовов на ход; основной агент назвал несуществующие пути, ложно сказал, что субагент завершён, и выдал нечитаемый ответ; запрос к deep уже содержал ответ |
| 17.11 | EXP2 | Partial и resume, 47/47 | 35/36: L-ST-30 прочитан, но не записан |
| 17.11 | M3, M3-deep1 | Делегирование | Deep 38 раз читал разделы и ни разу не процитировал (0 evidence); основной агент сам начал grep по `storage`, не увидел `next_cursor` в артефакте и подставлял выдуманный курсор |
| 17.11 | M4, M4-deep1, M4-exhaustive1 | Неблокирующий deep и exhaustive одновременно, deep 17 evidence, обход 47/47 | Deep израсходовал 40 вызовов до `submit_result`; обход 35/36 (L-ST-30) |
| 19.11 | ING4, ING5, probe, M5, EXP3 | Сбой посреди загрузки и resume; повтор без дублей; шесть сочетаний; EXP3 36/36 | M5-deep1 перебирал `section_id` s01…s19 (65 ошибок), 2 evidence; M5-exhaustive1 35/36; `resume_cursor` реестра в EXP3 дал `invalid_cursor` |
| 19.11 | M6, M6-deep1 | Deep завершён через `submit_result`, 17 evidence | Список открытых вопросов пустой |
| 20.11, 16:50 | M7, EXP4 | Первая попытка `final2` | `submit_result` у M7-deep1 обрезался на max_tokens=2048; M7-exhaustive1 прочитал 47/47 и закончил ход без `submit_result` |
| 20.11, 19:05 | M7 (повтор) | Вторая попытка | Диалог продолжился поверх старого M7, агент путал субагентов; оба субагента остановил `no_progress` (6 одинаковых grep) |
| 20.11 | M8, M8-deep1, M8-exhaustive1, EXP4 | Оба субагента завершились через `submit_result` | `area_next` засчитывал порцию без решения по фрагментам: M8-exhaustive1 35/36, EXP4 33/36; M8-exhaustive1 написал неверные числа по документам; основной агент сократил `fragment_id` до 8 символов и назвал выдуманный `evidence_id` |
| 20.11 | M9, M9-deep1, M9-exhaustive1, EXP5 | Deep и exhaustive параллельно, оба `completed` за один запуск; 24 evidence у deep; обход 47/47, 42 упоминания; EXP5 16/47 → 47/47 | См. ниже |

Подробнее про M9 и EXP5, потому что исправления после них есть в текущем коде:
- M9-deep1 после 10 компактификаций написал, что ограничения Redis и сроки хранения «не прочитаны», хотя цитировал их сам; статусы шагов плана не обновлял; в тексте сокращал `fragment_id` до 8 символов.
- Основной агент M9 на ходе 7 заменил открытые вопросы субагента своими и не сказал об этом. На ходе 5 «раз в 90 дней» взял из summary L1 без цитаты L2.
- M9-exhaustive1 и EXP5 нашли 35/36: строка L-ST-30 («Трассы перед публикацией проверяются на утечку секретов автоматически») стоит в одном фрагменте с записанными L-ST-28 и L-ST-29, и вердикт по фрагменту не заставлял решать про неё отдельно.
- EXP5 после resume продолжил курсором из восстановленной истории, а не `resume_cursor` реестра. Сверочный `area_grep` он дочитал до 30 из 49 совпадений.

## Проблемы и исправления

| Что сломалось (прогон) | Что я поменяла | Тест |
|---|---|---|
| Проверка цитат вырезала «N: » внутри строки (M1) | номер строки снимается только в начале строки цитаты | — |
| Повтор одного и того же вызова до конца бюджета (M1) | no-progress guard в harness: на 3-й одинаковый вызов с тем же результатом ошибка с подсказкой, на 6-й ход останавливается; лимит 30 вызовов модели на ход диалога | `test_repeated_identical_calls_are_stopped` (лаба 2) |
| `submit_result({})` после полного обхода (EXP1) | пустые findings собираются кодом из evidence | `test_submit_result_without_findings_is_composed_from_evidence` |
| Deep перечитывал разделы без цитат (M3-deep1) | harness помечает повторное чтение и после 5 чтений без цитат просит процитировать | `test_deep_coaching_marks_repeated_reads_and_asks_to_cite` |
| Страница grep не помещалась в ответ, выдуманный курсор (M3, коммит `108bc4d`) | страница grep всегда в лимите одного ответа; чужой курсор отклоняется | `test_grep_page_fits_single_result_limit_and_invented_cursor_is_rejected` |
| Основной агент сам делал полный обход (M3) | правило в промпте: просьба просмотреть всё уходит exhaustive-субагенту | — (промпт) |
| Deep не доходил до итога (M2-deep1, M4-deep1) | лимит deep 60 вызовов; при остатке не больше 6 вызовов в результатах tools приходит `harness_budget`; если бюджет кончился, итог собирает код (`compose_partial`, явный partial) | `test_deep_tools_warn_when_few_model_calls_are_left`, `test_deep_stopped_by_budget_returns_an_explicit_partial_result` |
| Выдуманные пути, пустой и нечитаемый ответ (M2) | `answer_issue` проверяет ответ, ошибочный ответ один раз возвращается агенту (`chat-recheck`) | `test_answer_check_flags_invented_paths_and_unreadable_answers` |
| Повтор загрузки проверен только на пустой папке (ING3) | CLI до агента убирает файлы с известным содержимым в `rejected/` | `test_repeated_ingestion_of_known_content_is_skipped_before_the_agent` |
| Перебор несуществующих разделов (M5-deep1) | `unknown_section` возвращает список настоящих разделов; после 4 ошибок подряд подсказка сменить подход | `test_unknown_section_lists_real_sections_and_error_streak_is_pointed_out` |
| `resume_cursor` реестра не принимался `area_next` (EXP3) | курсор реестра имеет тот же формат, что `next_cursor` | `test_area_next_is_deterministic_per_cursor` |
| `unread_lines` обрезался до 20 диапазонов | соседние диапазоны склеиваются | `test_area_next_is_deterministic_per_cursor` |
| Пустые открытые вопросы deep (M6-deep1) | `open_questions_required` | `test_non_blocking_deep_subagent_and_chained_budget` |
| `submit_result` обрезался на 2048 токенах (M7-deep1) | резерв ответа субагентов 6144; `open_questions` первым полем; `empty_arguments` на пустой вызов | `test_deep_submit_with_cut_off_arguments_gets_a_clear_error` |
| Субагент закончил ход без `submit_result` или его остановил `no_progress` (M7) | одно дополнительное сообщение в той же задаче, вторая остановка окончательная | `test_sub_agent_that_stops_without_submit_gets_one_follow_up`, `test_sub_agent_stopped_by_no_progress_gets_one_follow_up` |
| Повтор `final2` продолжил старый диалог (M7) | `final2` берёт первый свободный id и удаляет прошлые попытки; `evaluate.py` пропускает задачи без трасс | — (скрипт) |
| Порция засчитывалась без решения по фрагментам (M8, EXP4, коммит `7f4dc8d`) | `verdict_required`: у каждого фрагмента прошлой порции запись или id в `no_mention` | `test_area_next_is_deterministic_per_cursor` |
| Неверные числа упоминаний по документам (M8-exhaustive1) | числа по документам дописывает код по evidence | — |
| Сокращённые `fragment_id` и выдуманный `evidence_id` в ответе (M6, M8) | `answer_issue` проверяет оба; `submit_result` отклоняет неизвестные `evidence_id` | `test_answer_check_rejects_evidence_ids_that_do_not_exist`, `test_answer_check_flags_invented_paths_and_unreadable_answers` |
| Мало ссылок на evidence в findings deep (M8-deep1) | `evidence_refs_required`, нужен хотя бы один поиск по L2 | `test_non_blocking_deep_subagent_and_chained_budget` |
| «Не прочитан» про процитированный документ, незакрытый план, короткие id в тексте субагента (M8-deep1, M9-deep1) | `contradicts_evidence`, `plan_open`, `short_ids` в `submit_result` | `test_submit_rules_short_ids_unfinished_passes_contradictions_and_open_plan` |
| Строка с требованием во фрагменте, где уже есть упоминание (M9, EXP5, L-ST-30) | `check_lines` и `line_verdict_required`: у каждой строки с формулировкой требования своё решение | `test_area_next_is_deterministic_per_cursor` |
| Сверочный grep не дочитан (EXP5) | `pass_incomplete` в `submit_result` exhaustive | `test_submit_rules_short_ids_unfinished_passes_contradictions_and_open_plan` |
| Resume по курсору из истории (EXP5) | `resume` exhaustive передаёт агенту `resume_cursor` реестра первым сообщением | `test_exhaustive_partial_then_resume_from_the_same_boundary` (политика продолжает по `resume_cursor`) |
| Открытые вопросы субагента заменены своими (M9) | правило в промпте: открытые вопросы субагента дословно, свои отдельно | — (промпт) |
| Цифра из summary L1 без цитаты (M9) | правило в промпте: каждое число с цитатой L2 | — (промпт) |
| Скрипт сам делал commit и push | commit и push только с ключом `-Push` | — (скрипт) |

После всех исправлений тестов 30. В финальном прогоне M10/EXP6 (README, раздел 9) модель всё равно продолжила EXP6 курсором из истории, а не `resume_cursor`. Обход при этом ничего не потерял, потому что подтверждение порции требует вердикта по каждому её фрагменту.
