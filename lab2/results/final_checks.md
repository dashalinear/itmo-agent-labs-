# Проверки сценариев (scripts/final_checks.py)

| задача | ожидаемое свойство | фактически | итог |
|---|---|---|---|
| A1 | статус completed только по критериям | completed (Обзор собран: 5 секций (approaches, limitations, disputed, decisions, summary), ) | OK |
| A1 | [C5] решения АС-01..03 дословно (проверка complete_task) | None | НЕТ |
| A1 | отказы complete_task до выполнения критериев (информативно) | 0 | OK |
| A1 | сбой после checkpoint и resume в новом процессе | crash=True, resume=True, запусков 2 | OK |
| A1 | >= 2 компактификаций в одном запуске без реплик пользователя | 4 | OK |
| A1 | нет вызова сверх рабочего лимита | макс. оценка+резерв 20682 / 28000; сверх лимита: 0 | OK |
| A1 | факт за пределами выданного фрагмента найден в артефакте в новом процессе | '"line": 1311': смещение 10104 > выдано 1696, через search_artifact | OK |
| D1 | статус completed только по критериям | completed (Обзор завершён: 5 разделов (approaches, limitations, disputed, decisions, summar) | OK |
| D1 | [C5] решения АС-01..03 дословно (проверка complete_task) | None | НЕТ |
| D1 | отказы complete_task до выполнения критериев (информативно) | 1 | OK |
| D1 | первый запуск: paused по бюджету + частичный отчёт | paused | OK |
| D1 | лимит поднят пользователем (limits_changed) | 1 | OK |
| D1 | после resume счётчики не обнулены | токены до resume: 114055 | OK |
| E1 | статус cancelled (не completed) | cancelled | OK |
| E1 | отмена после сохранения 2-3 evidence | 0 | НЕТ |
| E1 | резерв токенов освобождён после отмены | reserved=20782, interrupted_call_tokens=0 | НЕТ |
| E1 | частичный отчёт сохранён | true | OK |
| DL1 | есть результаты | нет | НЕТ |
| C2 | есть результаты | нет | НЕТ |
| I1 | есть results/I1/idempotency.json | нет | НЕТ |
| G2 | есть трассы G2 | нет | НЕТ |

Итого: 13 OK, 8 НЕТ (scripts/final_checks.py по results/ и traces/).
