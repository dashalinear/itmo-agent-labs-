# Проверки сценариев (scripts/final_checks.py)

| задача | ожидаемое свойство | фактически | итог |
|---|---|---|---|
| A1 | статус completed только по критериям | completed (Обзор по data/search собран: 5 разделов (approaches, limitations, disputed, deci) | OK |
| A1 | [C5] решения АС-01..03 дословно (проверка complete_task) | ok | OK |
| A1 | отказы complete_task до выполнения критериев (информативно) | 0 | OK |
| A1 | сбой после checkpoint и resume в новом процессе | crash=True, resume=True, запусков 2 | OK |
| A1 | сбой после сохранённых evidence; после resume они восстановлены, дублей нет | evidence на момент сбоя 5, повторённые save_evidence после resume: ['saved', 'already_saved', 'already_saved', 'already_saved', 'already_saved', 'already_saved']; в конце 17, дублей 0 | OK |
| A1 | MCP Elasticsearch лабы 1: upsert_excerpts записал выдержки, search_excerpts их находит | upsert ok: 1/1, поиск с хитами: 3/3, критерий indexed_in_es: True | OK |
| A1 | >= 2 компактификаций в одном запуске без реплик пользователя | 3 | OK |
| A1 | нет вызова сверх рабочего лимита | макс. оценка+резерв 20567 / 28000; сверх лимита: 0 | OK |
| A1 | факт за пределами выданного фрагмента найден в артефакте в новом процессе | 'АС-01': смещение 5961 > выдано 1901, через read_artifact | OK |
| D1 | статус completed только по критериям | completed (Обзор собран: 5 разделов (approaches, limitations, disputed, decisions, summary)) | OK |
| D1 | [C5] решения АС-01..03 дословно (проверка complete_task) | ok | OK |
| D1 | отказы complete_task до выполнения критериев (информативно) | 0 | OK |
| D1 | первый запуск: paused по бюджету + частичный отчёт | paused | OK |
| D1 | лимит поднят пользователем (limits_changed) | 1 | OK |
| D1 | после resume счётчики не обнулены | токены до resume: 111715 | OK |
| E1 | статус cancelled (не completed) | cancelled | OK |
| E1 | отмена после сохранения 2-3 evidence | 2 | OK |
| E1 | резерв токенов освобождён после отмены | reserved=0, interrupted_call_tokens=12283 | OK |
| E1 | частичный отчёт сохранён | true | OK |
| P1 | CLI pause из другого процесса: задача paused на границе шага | paused (pause requested by the user (demo: pause from another termin) | OK |
| P1 | resume в новом процессе продолжил ту же задачу, расход не обнулён | запусков 2, вызовов модели до resume 11, всего 24; статус paused | OK |
| DL1 | paused по дедлайну задачи (не completed) | paused (budget exhausted: task deadline reached (task_deadline_s: needed 47.8,) | OK |
| DL1 | trace: harness:guardrail guard=deadline | 1 | OK |
| DL1 | частичный отчёт сохранён (evidence: сколько успело сохраниться до дедлайна) | evidence 0, отчёт: True | OK |
| DL1 | после дедлайна новые действия не выполняются (инструмент на границе) | save_evidence | OK |
| C2 | HTTP-лимит достигнут в агентном прогоне | 6/6 | OK |
| C2 | trace: harness:guardrail guard=http_budget (http_budget_exhausted) | 1 | OK |
| C2 | необработанные URL сохранены в очереди | pending 8, found 5 | OK |
| C2 | задача не помечена completed | paused | OK |
| I1 | сбои процесса и resume в новых процессах | сбоев 3, resume 3 | OK |
| I1 | URL, оставшиеся running, возвращены в очередь | 1 | OK |
| I1 | повторный шаг: web_fetch = cache hit | true | OK |
| I1 | страница, прочитанная из хранилища после сбоя, повторно не скачана (HTTP-попытки URL не выросли) | true | OK |
| I1 | повторный шаг: save_evidence = already_saved | true | OK |
| I1 | нет дублей evidence и URL | True, True | OK |
| I1 | повтор тех же ключей в новом процессе: already_saved / cache hit без HTTP | True, True | OK |
| I1 | итоговый статус (информативно) | completed | OK |
| G2 | write по абсолютному пути наружу -> outside_workspace | ["outside_workspace"] | OK |
| G2 | move по абсолютному пути наружу -> outside_workspace | ["outside_workspace"] | OK |
| G2 | write в junction (data/link_out -> вне root) -> outside_workspace | ["outside_workspace"] | OK |
| G2 | move в junction (data/link_out -> вне root) -> outside_workspace | ["outside_workspace"] | OK |
| G2 | move из junction -> outside_workspace | ["outside_workspace"] | OK |
| G2 | разрешённые write и move внутри root работают | ["ok", "ok"] | OK |
| G2 | лимит суммарного вывода шага: step total would exceed | 2 | OK |
| G2 | сумма результатов одного шага не больше max_step_result_chars | максимум 11962 из 12000 | OK |
| B3 | не помечен completed без выполненных критериев | paused (budget exhausted before the call (tokens: needed 12770, rema) | OK |
| B3 | второй раунд: новые запросы после чтения страниц | 7 запросов после чтения | OK |
| B3 | доп. критерий (сверх задания): прочитана страница по ссылке с прочитанной страницы (found_on) | 0; link_followed событий: 0 | НЕТ |
| B3 | доп. критерий (сверх задания): не больше 60 % прочитанных страниц с одного домена | arxiv.org: 9/14 = 64% | НЕТ |
| B3 | домены прочитанных страниц (информативно) | {"arxiv.org": 9, "docs.langchain.com": 1, "mortalapps.com": 1, "fast.io": 1, "www.ibm.com": 1, "en.wikipedia.org": 1} | OK |

Итого: 48 OK, 2 НЕТ (scripts/final_checks.py по results/ и traces/); из них 2 — дополнительные критерии сверх задания, по обязательным требованиям НЕТ: 0.
