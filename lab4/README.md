# Лабораторная 4. Temporal: автоматическая индексация и обзор тем корпуса

В этой лабе я автоматизировала через Temporal две задачи поверх лаб 1–3:
1. **Новые файлы → индексация.** Watcher замечает готовый файл в `workspace/input` и запускает workflow `IngestNewFiles`. Тот вызывает агента-загрузчика лаб 1/3 в harness лабы 2, и агент строит L0/L1/L2 в Elasticsearch.
2. **Периодический обзор тем.** Temporal Schedule запускает workflow `TopicReport`. Workflow фиксирует список документов по L0, агент тематического анализа выделяет темы с подтверждениями из L2, а отчёт публикуется отдельным документом на L0.

Новых ReAct-циклов, своего планировщика и очереди задач я не писала. Агенты те же, что в лабе 3: `create_agent` и middleware harness.

## 1. Архитектура

```
 пользователь кладёт файл           Temporal Service (Docker, temporalio/temporal:1.10.0, :7233, UI :8233)
 workspace/input/x.md  ──►  watcher ──start_workflow──►  IngestNewFiles(batch)        TopicReport(run)  ◄── Schedule lab4-topics
   (.part → rename,          (scan,                         │ record_batch               │ freeze_corpus        (every N, overlap SKIP)
    проверка стабильности)    registry)                     │ stage_batch                │ run_topic_agent ──► агент тем (harness)
                                                            │ run_ingest_agent ──► агент-загрузчик лаб 1/3 (harness)
                                                            │ verify_batch               │ publish_report ──► data/_reports/topics_<run>.md + L0
                                                    Worker (один процесс, очереди lab4-ingest и lab4-topics)
```

| Что | Где | Из какой лабы |
|---|---|---|
| Организатор файлов, каталог, `move`, MCP Elasticsearch, skills | `lab1/organizer`, `lab1/mcp_es` | 1 |
| Harness: бюджет, компактификация, артефакты, checkpoints, resume | `lab2/harness` (`TaskRunner`) | 2 |
| L0/L1/L2, агент-загрузчик, поиск, `cite_fragment` | `lab3/navsearch` | 3 |
| Workflows, activities, watcher, Schedule, агент тем, публикация отчёта | `lab4/topicflow` | 4 |

Workflow у меня только координирует шаги: `topicflow/workflows.py` импортирует лишь `temporalio`. LLM, Elasticsearch/MCP и файловые операции выполняются в activities (`topicflow/activities.py`). В историю Temporal попадают идентификаторы и небольшие метаданные. Тексты, отчёты, история агента и evidence лежат в workspace, в `state.db`/`checkpoints.db` harness и в артефактах.

В предыдущих лабах я поменяла две вещи:
- лаба 3, `levels.py`: в L0 есть блок `reports` (история отчётов и указатели на последний и последний полный), а документы из `data/_reports` получают пометку `service: topic_report`. `corpus_overview` показывает этот блок;
- лаба 1, `pathguard.py`: при параллельных `move` в новую папку `Path.resolve()` на Windows возвращает путь с префиксом `\\?\`, и проверка считала его «вне workspace». Я это исправила, тесты в `lab1/tests/test_tools_fs.py`.

## 2. Установка и запуск

Python-окружение я взяла из лабы 3 (её venv) и добавила `temporalio==1.36.1` (`requirements.txt`, полный список версий в `requirements.lock.txt`). Токен LLM берётся из `lab4/.env`, а если его нет, то из `lab3/.env`. Оба файла в `.gitignore`.

```powershell
cd itmo-agent-labs\lab4
..\lab3\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Сервисы:

```powershell
cd ..\lab1; docker compose up -d; cd ..\lab4                       # Elasticsearch (лаба 1)
docker run -d --name itmo-lab4-temporal --user root -p 7233:7233 -p 8233:8233 -v itmo-lab4-temporal:/data `
  temporalio/temporal:1.10.0 server start-dev --ip 0.0.0.0 --db-filename /data/temporal.db   # Temporal Service + UI
```

История Temporal хранится в volume `itmo-lab4-temporal` и переживает перезапуск контейнера. UI: http://localhost:8233.

Две детали про запуск контейнера:
- `--user root` нужен, потому что процесс образа не может создать файл SQLite в только что созданном volume.
- Из Git Bash команду надо запускать с `MSYS_NO_PATHCONV=1`, иначе путь `/data` превращается в путь Windows. В PowerShell это не нужно.

Команды (из `lab4`, `$py = ..\lab3\.venv\Scripts\python.exe`):

| Команда | Что делает |
|---|---|
| `& $py -m topicflow.cli prepare [--reset]` | workspace, индексы `itmo_lab4_l1/l2`, реестр |
| `& $py -m topicflow.cli worker` | Worker: оба workflow и все activities |
| `& $py -m topicflow.cli watch` | наблюдение за `workspace/input` и запуск `IngestNewFiles` |
| `& $py -m topicflow.cli schedule create --every 10m [--no-trigger]` | создать Schedule; без `--no-trigger` первый запуск сразу, с ним первый запуск будет тиком интервала |
| `& $py -m topicflow.cli schedule describe \| pause \| unpause \| trigger \| delete` | статус, пауза, внеочередной запуск, остановка Schedule |
| `& $py -m topicflow.cli status` | реестр: батчи, файлы, отчёты (Workflow ID, run ID, task_id) |
| `& $py -m topicflow.cli l0` / `open-report --latest` | L0 с блоком отчётов / открыть последний отчёт с L0 |
| `& $py -m topicflow.cli topics-now [--set max_model_calls=4]` | разовый запуск `TopicReport` вне расписания |
| `& $py -m topicflow.cli fault --activity A --point P --times N\|always` / `--clear` | инъекция сбоя в activity |
| `& $py -m topicflow.cli replay --batch <batch_id>` | повторить событие обнаружения для батча (Temporal не запустит второе выполнение) |
| `& $py -m topicflow.cli wait --batches \| --schedule-runs N [--after T] \| --workflow-id W` | дождаться загрузки / запусков Schedule / workflow |
| `& $py -m topicflow.cli trace` / `export` | истории Temporal → `traces/temporal` и сводка `summary.md`; результаты → `results/` |

Пользователь только кладёт файлы в `workspace/input`. В демо это за него делает команда `drop --set initial|arrivals|new_topic|late|new_topic2|new_topic3|late2`. Все сценарии со всеми проверками лежат в `scripts\run_lab4_demo.ps1` (запускается в сети ИТМО). Скрипт записывает `traces`, `results` и `corpus`, а `git commit` и `git push` делает только с ключом `-Push`:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_lab4_demo.ps1 -Every 8m -Push              # основной сценарий
powershell -ExecutionPolicy Bypass -File scripts\run_lab4_demo.ps1 -Part extra -SkipTests -Push        # E2, E3
powershell -ExecutionPolicy Bypass -File scripts\run_lab4_demo.ps1 -Part topic-crash -SkipTests -Push  # E1
powershell -ExecutionPolicy Bypass -File scripts\run_lab4_demo.ps1 -Part growth -SkipTests -Push       # новая тема, повтор во время RUNNING
powershell -ExecutionPolicy Bypass -File scripts\run_lab4_demo.ps1 -Part ticks -Every 5m -SkipTests -Push  # три тика одного Schedule
```

Части `extra`, `topic-crash`, `growth` и `ticks` работают поверх корпуса предыдущих прогонов, без `--reset`.

**Репетиция без LLM.** Команда `scripts\run_lab4_demo.ps1 -DryRun -SkipTests` проходит тот же сценарий с настоящими Temporal, Schedule, workflows, activities, harness и Elasticsearch. Отличия такие:
- вместо qwen работают детерминированные политики из `tests/fake_llm.py` (worker `scripts/dry_run_worker.py`);
- отдельный namespace Temporal (`-DryNamespace`, по умолчанию `dryrun`);
- папка `-DryRoot` (по умолчанию `<диск>:\tmp_lab4`) и индексы `itmo_lab4_dry_*`;
- интервал Schedule 1 мин;
- коммита нет даже с `-Push`.

Это проверка обвязки, а не демонстрация модели. Так сценарий можно отладить без доступа к qwen.

## 3. Задача 1: новые файлы → индексация

**Обнаружение** (`topicflow/watcher.py`). Temporal не следит за файловой системой, поэтому её раз в 2 с сканирует отдельный процесс.
- Файл, который ещё пишется, не читается. Имена `*.part`, `*.tmp`, `*.crdownload`, `~*`, `.*` пропускаются. Демо пишет `<name>.part` и переименовывает файл, когда запись закончена. Остальные файлы должны 3 с не менять размер и mtime.
- Готовые незарегистрированные файлы одного прохода образуют батч. Его id считается как хеш списка «путь | размер | mtime», поэтому одно и то же событие всегда даёт тот же id.
- Сначала батч и его файлы записываются в реестр (`workspace/.harness/lab4.db`), потом запускается workflow `ingest-<batch_id>` с `id_conflict_policy=USE_EXISTING` и `id_reuse_policy=ALLOW_DUPLICATE_FAILED_ONLY`. Если тот же батч стартует повторно, второго выполнения не появляется, а повтор уже завершённого батча Temporal отклоняет.
- При старте watcher перезапускает батчи, у которых workflow не работает и не завершён успешно: один новый запуск на каждый старт watcher и не больше трёх за всё время (`MAX_BATCH_RESTARTS = 3`, счётчик хранится в реестре и переживает перезапуски watcher). Дальше батч ждёт меня, а watcher пишет об этом в лог. Потом watcher заново сканирует `input`. Папки `input/_*` зарезервированы.
- Если Temporal был недоступен в момент обнаружения, батч остаётся в реестре со статусом `detected`. Watcher при этом не падает и раз в ~30 с снова пытается запустить его workflow.

**Workflow `IngestNewFiles`.**

| Шаг (activity) | Что делает | Идемпотентность |
|---|---|---|
| `record_batch` | Workflow ID и run ID в реестр | перезапись тех же полей |
| `stage_batch` | атомарный `os.replace` файлов батча в `input/_batches/<batch_id>/`; тот же контент, который уже есть в `data` или уже взят другим батчем, уходит в `rejected/<batch_id>/` со статусом `duplicate` | повтор видит уже перенесённые файлы; файл, взятый прошлым запуском этого батча (в том числе уже перенесённый агентом в `data`), остаётся в батче, поэтому перезапуск снова вызывает агента и проверку; исходник не теряется |
| `run_ingest_agent` | агент-загрузчик лаб 1/3 на папке батча, задача harness `ING-<batch_id>` | при повторе продолжается та же задача с checkpoint и накопленным бюджетом |
| `verify_batch` | код проверяет, что каждый файл в `data`, есть L1, актуальный L2, в ES 1 запись L1 и столько же L2, сколько фрагментов | только чтение и запись статуса |

В реестре у каждого файла хранятся исходный путь, хеш, батч, статус (`detected → staged → indexed / duplicate / failed / missing`), `document_id` и текущий путь. Если индексация не завершена, workflow падает с `IngestIncomplete`, а файлы остаются в `input/_batches/<batch_id>`.

## 4. Задача 2: периодический обзор тем

**Schedule** `lab4-topics`. Интервал берётся из `--every` или `LAB4_TOPICS_EVERY`, `catchup_window=1m`. Стоит `overlap=SKIP`: пока идёт предыдущий запуск, очередной период пропускается. `schedule create` записывает в `.harness/lab4/schedule_meta.json` время создания, интервал и то, был ли немедленный запуск.

**Идентичность запуска.** Каждому периоду Schedule даёт свой Workflow ID: `lab4-topics-run-<время по расписанию>`. Его я и использую как run key. Из него выводятся манифест `.harness/lab4/runs/<slug>/manifest.json`, задача harness `TOP-<slug>`, путь отчёта `data/_reports/topics_<slug>.md` и запись в истории отчётов. Если после сбоя повторяется activity или публикация, она пишет тот же файл в тот же документ, и второго отчёта за запуск не появляется.

**Workflow `TopicReport`.**
1. `freeze_corpus` фиксирует список документов по L0 один раз за запуск. Служебные отчёты (`data/_reports`), пустые файлы и документы без L1 исключаются, причина записывается. Файлы, проиндексированные позже, войдут в следующий отчёт. Документы, которых не было в списке предыдущего отчёта, получают пометку `new_since_previous_report`.
2. `run_topic_agent` запускает агента тем (`topicflow/topics.py`). Это тот же `create_agent` с middleware harness, но со своим промптом и инструментами:
   - `frozen_documents`: зафиксированный список постранично, с пометкой новых документов. Промпт требует, чтобы для каждого нового документа агент явно решил, новая это тема или расширение существующей;
   - `examine_document`: L1 документа, документ отмечается просмотренным;
   - `read_section` / `read_fragment` / поиск лабы 3 и `cite_fragment`: подтверждения из L2;
   - `topic_upsert` / `topic_merge`: карта тем запуска, она хранится в `topics.json` и переживает перезапуск;
   - `mark_unreadable`, `topics_status`;
   - `submit_report`: завершить можно только при полном покрытии, то есть каждый документ просмотрен или отмечен с причиной и отнесён к теме, а у каждой темы есть цитата из L2. Иначе остаётся `stop_partial`.
   Агент работает частями: страницы списка, отдельные L1, компактификация harness. Весь корпус в один prompt не загружается. Карту тем каждый запуск строит заново, поэтому группировка от отчёта к отчёту может меняться.
3. `publish_report` рендерит Markdown только из сохранённого состояния, без текущего времени, поэтому повтор даёт те же байты. Дальше идут атомарная запись, регистрация в каталоге по пути (тот же `document_id`), L1/L2 со стабильными id и запись в `.harness/corpus/reports.json`. Из неё строится блок `reports` в L0: `history`, `latest` и `latest_complete` (последний полный отчёт).

Что есть в отчёте:
- статус ПОЛНЫЙ или ЧАСТИЧНЫЙ с причинами;
- Workflow ID, run ID, задача harness и её расход, ссылка на предыдущий отчёт;
- «вид запуска» и «период» (`_schedule_kind` в `topicflow/reports.py` по `schedule_meta.json`): «тик интервала Schedule (каждые N мин)», «немедленный запуск при создании Schedule» или «ручной запуск topics-now»; период идёт от прошлого запуска Schedule до текущего;
- состав и покрытие: просмотрено L1 X из N, новые документы с прошлого отчёта и таблица «новый документ → тема → новая тема или расширение»;
- темы с описаниями, `document_id` и путями, цитаты из L2 со строками и `fragment_id`; новые темы и темы, расширенные новыми документами, помечены;
- непроверенные документы и ошибки с причинами, таблица зафиксированного списка.

**Открытие с L0.** `open-report --latest` (или `--latest-complete`) идёт по цепочке L0 `reports.latest.document_id` → L1 отчёта → текст. Основной агент лабы 3 видит тот же блок в `corpus_overview`.

## 5. Повторы, таймауты, перезапуск Worker и harness

| Activity | start-to-close | попыток | heartbeat | не повторяется |
|---|---|---|---|---|
| `record_batch`, `stage_batch`, `verify_batch`, `freeze_corpus`, `publish_report` | 30 с – 5 мин | 5 (1 с, ×2, до 20 с) | — | `UnknownBatch` |
| `run_ingest_agent`, `run_topic_agent` | 1 ч | 3 (5 с, ×2, до 1 мин) | 90 с (в демо 30 с) | — |

Execution timeout у обоих workflow 4 ч: три попытки агентной activity с backoff и публикация отчёта в него помещаются.

Агентная activity каждые 10 с шлёт heartbeat с расходом задачи. Если Worker убит, Temporal по heartbeat timeout отдаёт попытку живому Worker. Новая попытка открывает ту же задачу harness (`ING-…` / `TOP-…`) и продолжает её с последнего checkpoint, бюджет при этом накопленный, а не новый. В событиях задачи остаётся `temporal_attempt` с номером попытки и `resumed=true`.

Исчерпание бюджета и собственный `stop_partial` агента я считаю окончательным результатом: повтор бюджета не добавит. Отчёт в этом случае публикуется как ЧАСТИЧНЫЙ. Остальные остановки (сервер LLM недоступен, guard harness, сбой) поднимают повторяемую ошибку, и попыток не больше 3. Если агентная activity всё-таки окончательно упала, workflow всё равно проверяет или публикует то, что есть, и не помечает результат полным.

Сбои для демо задаются файлом `faults.json`, Worker для этого перезапускать не нужно. Например, `publish_report --point after_write` падает между записью файла и регистрацией на L0, и следующая попытка завершает тот же отчёт. Падение worker внутри агента задаётся переменной `HARNESS_CRASH_AT` (например, `after_tool:6`): процесс выходит с кодом 137, а скрипт проверяет, что он вышел именно там.

Ограничения лабы 2 сохраняются для обоих агентов: task root, рабочий лимит контекста 28 000 при окне 262 144, компактификация, лимит вывода tools с артефактами, бюджет токенов, вызовов и tools, таймауты.

## 6. Тестовые данные

`corpus/`:
- `initial/` — 10 документов, темы: хранение состояния, поиск, эксплуатация, протокол совещания;
- `arrivals/` — 2 файла, добавляются во время работы;
- `new_topic/inference_energy.md` — новая тема (энергопотребление инференса);
- `late/latency_budget.md` — файл для сценария E3 (кладётся, когда watcher не работает);
- `new_topic2/ui_accessibility.md` — совсем новая тема (доступность интерфейса) для `-Part growth`;
- `new_topic3/onboarding_engineers.md` — онбординг инженеров, кладётся между тиками 1 и 2 в `-Part ticks`;
- `late2/query_rewrite.md` — переписывание запросов, кладётся после фиксации списка тика 2.

Как воспроизвести индекс и основной сценарий вручную (из `lab4`, `$py = ..\lab3\.venv\Scripts\python.exe`):

```powershell
& $py -m topicflow.cli prepare --reset          # пустой workspace и индексы itmo_lab4_l1 / itmo_lab4_l2
& $py -m topicflow.cli worker                   # окно 1
& $py -m topicflow.cli watch                    # окно 2
& $py -m topicflow.cli drop --set initial       # окно 3: 10 файлов -> автоматическая загрузка
& $py -m topicflow.cli wait --batches
& $py -m topicflow.cli schedule create --every 8m
& $py -m topicflow.cli drop --set arrivals; & $py -m topicflow.cli drop --set new_topic
```

## 7. Тесты

`..\lab3\.venv\Scripts\python.exe -m pytest -q` из `lab4` запускает 11 тестов (последний запуск: 11 passed). Им нужен Elasticsearch, а `test_workflows_on_real_temporal_service` ещё и Temporal на `localhost:7233` (без него тест пропускается). Вместо LLM работают детерминированные политики `tests/fake_llm.py`. Они берут по 5 документов за шаг, как требует лимит вывода шага, и продолжают работу после падения worker. Писать сводку они не умеют, поэтому для них я подняла рабочий лимит контекста (`limit_overrides`). Саму компактификацию я проверяла в лабе 2.

| Тест | Что проверяет |
|---|---|
| `test_watcher_ignores_partial_files_and_waits_for_stability` | `.part` не берётся, есть ожидание стабильности, повторное событие не регистрирует батч второй раз |
| `test_restarted_incomplete_batch_runs_the_agent_again_instead_of_claiming_success` | агент перенёс файл в `data`, но не проиндексировал; перезапущенный батч снова вызывает агента и доводит L1/L2, а не объявляет успех |
| `test_same_content_in_two_batches_staged_before_indexing_is_one_document` | одинаковый контент в двух батчах до индексации → второй в `rejected/`, документ один |
| `test_watcher_survives_temporal_outage_and_rescans_after_restart` | Temporal недоступен в момент обнаружения → батч зарегистрирован и запускается позже; новый процесс watcher подбирает файл, добавленный, пока он был выключен, и не стартует повторно уже запущенный батч |
| `test_failed_batch_is_restarted_a_bounded_number_of_times_over_watcher_starts` | батч, который падает снова и снова, перезапускается при старте watcher, но всего не больше `MAX_BATCH_RESTARTS` = 3 раз, сколько бы раз watcher ни запускали |
| `test_ingestion_batch_is_idempotent_and_duplicates_are_not_indexed` | батч → `data` + L1/L2/ES; повтор каждого шага ничего не дублирует; тот же контент под другим именем → `rejected/`, второго документа нет |
| `test_two_reports_on_l0_and_the_second_covers_the_new_topic` | два отчёта на L0, `latest` и история; повтор публикации даёт тот же документ; второй отчёт видит новый документ и тему, отчёты в анализ не входят |
| `test_budget_exhaustion_gives_a_partial_report_and_retry_does_not_duplicate` | бюджет → ЧАСТИЧНЫЙ отчёт с причинами; сбой после записи файла → повтор без дубля |
| `test_agent_task_is_resumed_not_restarted` | повтор агентной activity продолжает ту же задачу harness, счётчики не сбрасываются |
| `test_workflows_on_real_temporal_service` | оба workflow на настоящем Temporal; повтор события, пока workflow ещё в RUNNING, возвращает то же выполнение (`USE_EXISTING`, в Temporal одно выполнение); `publish_report` повторён Temporal (attempt 2), отчёт один |
| `test_report_tells_an_interval_tick_from_the_immediate_run` | строка «вид запуска» по `schedule_meta.json`: немедленный запуск при создании, тик интервала «каждые 5 мин», ручной запуск |

## 8. Результаты прогонов

Все прогоны шли в сети ИТМО: модель `qwen3.8-27b`, Temporal dev server 1.10.0 в Docker, namespace `default`, heartbeat timeout в демо 30 с. Основной прогон 17.11.2026 (Schedule каждые 8 мин) занял около 10 минут, дальше я дописывала отдельные части сценария поверх того же корпуса. Что менялось в коде между прогонами и какие проблемы были по дороге, я записала в [docs/history.md](docs/history.md).

Где лежат результаты:
- истории Temporal — `traces/temporal/*.json`; сводка попыток по всем сохранённым историям — `traces/temporal/summary.md`;
- трассы агентов harness — `traces/ING-*`, `traces/TOP-*`; задачи harness — `results/<task_id>/`;
- логи процессов — `traces/processes/*.log`, трассировки ошибок worker — `traces/processes/*.err.log`;
- отчёты — `results/reports/`; L0 с блоком отчётов — `results/l0.json`, `results/reports.json`; реестр — `results/registry.json`, `results/status.txt`;
- проверка поиска по автоматически загруженным файлам (grep, full-text и vector в Elasticsearch) — `results/search_check.txt`;
- зафиксированные списки корпуса и карты тем по каждому запуску — `results/runs/<run>/`.

В историях 17–18.11 стоят прежние таймауты (`startToCloseTimeout=7200s`, `workflowExecutionTimeout` 21600 s у загрузки и 10800 s у отчёта), с 20.11 у обоих workflow 14400 s. Запуски тестов (`test-*`) в выгрузку `traces/temporal` не входят.

| Прогон | Сценарий | Workflow ID / run ID | Attempts | Результат |
|---|---|---|---|---|
| 17.11 | Загрузка 10 файлов, worker #1 убит после 10-го tool | `ingest-4d4e776d7c` / `01a1eb3f-27b8-7aff-bf3f-459bd0405a73` | `run_ingest_agent`: **2** (`Heartbeat timeout`) | `ING-4d4e776d7c`: 2 запуска, 13 вызовов модели, 80 tools; попытка 2 стартовала через 38 с и продолжила задачу (`resumed=true`, `runs_before=1`). 10/10 файлов в `data`, L1, L2, ES |
| 17.11 | Повтор события после завершения | тот же | — | Temporal отклонил, второго выполнения нет (`results/replay_event.txt`) |
| 17.11 | Тот же контент под другим именем | `ingest-5c534583b2` / `01a1eb41-b371-741c-8fb6-c8d77fcdc7ed` | все по 1 | `oncall_copy.md` → `rejected/`, статус `duplicate`, второго документа нет |
| 17.11 | Schedule, немедленный запуск при создании | `lab4-topics-run-2026-11-17T20:45:20Z` / `01a1eb41-ceae-7b36-b526-11d7d8b12c0b` | все по 1 | **ПОЛНЫЙ**, 10/10 документов, 4 темы, 10 цитат L2 |
| 17.11 | Рост корпуса: 2 файла + новая тема | `ingest-578a8801b8` / `01a1eb42-fd50-794f-a9c0-553f1e4e11a4` | все по 1 | 3/3 файла проиндексированы, пока Schedule стоял на паузе |
| 17.11 | Schedule, тик интервала 20:48:00 | `lab4-topics-run-2026-11-17T20:48:00Z` / `01a1eb44-3c62-7bb7-b73f-f80cafc476ed` | все по 1 | **ПОЛНЫЙ**, 13/13, 4 темы, 13 цитат; 3 новых документа названы, отчёт 1 в анализ не вошёл |
| 17.11 | `publish_report` падает после записи файла | `lab4-topics-manual-2026-11-17T20:49:46Z` / `01a1eb45-dd8c-7017-b610-64d54801bc28` | `publish_report`: **2** | тот же файл и документ, в истории L0 одна запись за запуск |
| 17.11 | Исчерпание бюджета (4 вызова) | `lab4-topics-manual-2026-11-17T20:51:11Z` / `01a1eb47-2688-72d9-95c2-6549164afc5c` | все по 1 | **ЧАСТИЧНЫЙ**: просмотрено 5 из 13, причины в отчёте |
| 17.11 | `stage_batch` падает всегда | `ingest-6341c09670` / `01a1eb47-679e-7bba-8063-6a8d88ffa901` | `stage_batch`: **5**, FAILED | файл остался в `input`; после `fault --clear` и перезапуска watcher тот же Workflow ID, run `01a1eb48-4b63-7e17-938f-d8d582116708` — completed, дубль `bm25_basics.md` → `rejected/` |
| 18.11 extra | E2. Перекрытие запусков | `lab4-topics-run-2026-11-18T11:59:36Z` / `01a1ee86-d5d8-763c-8487-cb9a69444dde` | все по 1 | после `schedule trigger` `skipped_overlap=1` (`results/schedule_overlap.json`), после завершения `skipped_overlap=2`, тик 12:00:00 тоже пропущен (`results/schedule_after_overlap.json`). **ПОЛНЫЙ**, 13/13, 3 темы |
| 18.11 extra | E3. Файл при остановленном watcher | `ingest-6b93f9adf0` / `01a1ee88-7d1a-734f-b414-da9cbd1e13d0` | все по 1 | watcher нашёл `latency_budget.md` при первом сканировании (`traces/processes/watcher3.log`), файл в `data/ops/` |
| 18.11 topic-crash | E1. Worker #5 убит после 6-го tool агента тем | `lab4-topics-manual-2026-11-18T13:20:31.699Z` / `01a1eed0-ebff-7971-aac5-c070e2841a09` | `run_topic_agent`: **2** (`Heartbeat timeout`) | попытка 2 на worker #6 через 36 с продолжила `TOP-manual-20261118T132031699Z` (`resume_kind=continue_interrupted_step`). **ПОЛНЫЙ**, 14/14, 5 тем |
| 20.11 growth | Файл на новую тему | `ingest-1362a3c0e5` / `01a1f963-c315-7816-a1c0-ca81a6e793c8` | все по 1 | `ui_accessibility.md` → `data/ops/`, workflow за 37 с (`traces/processes/watcher7.log`) |
| 20.11 growth | Повтор события, пока workflow в RUNNING | тот же | — | возвращено то же выполнение, в `temporal workflow list` одна строка `Running` (`results/replay_while_running.txt`) |
| 20.11 growth | Schedule, немедленный запуск при создании | `lab4-topics-run-2026-11-20T14:37:46Z` / `01a1f964-5cd9-7bff-bf37-9bef906c65bb` | `run_topic_agent`: **2** (guard `no_progress`) | **ПОЛНЫЙ**, 15/15, 4 темы; новый документ стал отдельной темой T4 «Доступность интерфейса» |
| 22.11 ticks | Тик 1, 18:05 | `lab4-topics-run-2026-11-22T18:05:00Z` / `01a2046e-cd61-7075-968f-8298d9cd2e9f` | все по 1 | **ПОЛНЫЙ**, 15/15, 6 тем, 19 цитат; новых документов 0 |
| 22.11 ticks | Файл между тиками 1 и 2 | `ingest-14c8e725b7` / `01a20472-7538-73e2-90e6-57b3be9c0a0a` | все по 1 | `onboarding_engineers.md` → `data/ops/` |
| 22.11 ticks | Тик 2, 18:10, worker #9 убит внутри агента тем | `lab4-topics-run-2026-11-22T18:10:00Z` / `01a20473-6104-745e-8dda-18dff0d19c38` | `run_topic_agent`: **2** (`Heartbeat timeout`) | **ПОЛНЫЙ**, 16/16, 4 темы, 17 цитат |
| 22.11 ticks | Файл после фиксации списка тика 2 | `ingest-2619a135a3` / `01a20473-9042-7a89-ab68-34eac54d2c56` | все по 1 | `query_rewrite.md` → `data/search/` |
| 22.11 ticks | Тик 3, 18:15 | `lab4-topics-run-2026-11-22T18:15:00Z` / `01a20477-f510-79ed-a796-8544af68e91d` | все по 1 | **ПОЛНЫЙ**, 17/17, 4 темы, 17 цитат |

**Что решили агенты.** Загрузчик сам разложил файлы по папкам `storage`, `search`, `ops`, `meetings`. Агент тем подписывает темы по-русски, у каждой темы есть цитаты из L2 со строками и `fragment_id`. Число тем меняется от запуска к запуску (3–6), потому что карту тем каждый запуск строит заново. С новыми документами модель решала по-разному. Энергопотребление инференса 17.11 попало в расширенную тему эксплуатации, а 18.11 стало отдельной темой T5. Доступность интерфейса 20.11 сразу стала отдельной темой T4 с пометкой «новая тема».

**Сбой по guard 20.11.** Первую попытку `run_topic_agent` 20.11 я не планировала ронять. Модель шесть раз подряд вызвала `read_fragment` с одними и теми же аргументами, сработал guard `no_progress` из harness лабы 2, задача ушла в `paused`, а activity завершилась ошибкой (`lastFailure`: «no progress: read_fragment returned the same result 6 times», трассировка в `traces/processes/worker7.err.log`). Temporal повторил activity, вторая попытка продолжила ту же задачу `TOP-20261120T143746Z` и довела отчёт до полного.

### Три тика одного Schedule (`-Part ticks`, 22.11.2026)

Здесь Schedule создан с `--no-trigger --every 5m`, поэтому все три запуска (18:05, 18:10, 18:15 UTC) — настоящие тики интервала, а не немедленный запуск. В `results/schedule_ticks_after_tick1.json` после первого тика `num_actions=1`, в `results/schedule_ticks_final.json` после третьего `num_actions=3`, `skipped_overlap=0`. В каждом отчёте (`results/reports/topics_20261122T180500Z.md`, `topics_20261122T181000Z.md`, `topics_20261122T181500Z.md`) есть строка «вид запуска: тик интервала Schedule (каждые 5 мин)» и строка «период»: у тика 1 от запуска Schedule 20.11 14:37:46 до 18:05:00, у тиков 2 и 3 по 5 минут.

Как шёл прогон (логи `traces/processes/worker8.log`, `worker9.log`, `worker10.log`, `watcher8.log`, истории `traces/temporal/lab4-topics-run-2026-11-22T18-0*` и `T18-1*`, трассы `traces/TOP-20261122T18*`):
1. Тик 1 выполнил worker #8 (identity `2540@…` в истории, pid 2540 в `worker8.log`). В корпусе 15 документов, новых с отчёта 20.11 нет.
2. Между тиками я положила `onboarding_engineers.md`. Watcher #8 сам запустил `ingest-14c8e725b7`, файл проиндексирован до тика 2.
3. Тик 2 взял worker #9, запущенный с `HARNESS_CRASH_AT=after_tool:6`. `freeze_corpus` зафиксировал 16 документов, потом worker #9 упал внутри агента тем (`harness:simulated_crash` после 6-го tool в трассе `a11`). Через 36 с после старта тика Temporal отдал вторую попытку worker #10 (identity `35836@…`, `lastFailure`: «activity Heartbeat timeout»). Она продолжила ту же задачу `TOP-20261122T181000Z`: `temporal_attempt` attempt 2, `resumed=true`, `status_before=running`, `runs_before=1`, в трассе `a22` `resume_kind=continue_interrupted_step`. Отчёт опубликован с первой попытки `publish_report`.
4. После падения worker #9, то есть уже после фиксации списка тика 2, я положила `query_rewrite.md` (`ingest-2619a135a3`). В отчёте тика 2 его нет.
5. Тик 3: 17 документов, `query_rewrite.md` указан как новый документ с прошлого отчёта.

Что получилось с новыми документами. `onboarding_engineers.md` в тике 2 правильно отмечен как новый документ, но отдельной темой модель его не сделала: в таблице «Новые документы и их темы» стоит «расширяет тему», документ попал в T3 «Процессы, люди и решения: совещания, онбординг, дежурства, доступность». По смыслу онбординг можно было выделить отдельно, это решение модели о группировке, покрытие при этом полное. `query_rewrite.md` в тике 3 тоже «расширяет тему» и попал в T1 про поиск, здесь это ожидаемо.

**Открытие с L0.** После последнего прогона `reports.history` содержит 10 отчётов, `reports.latest` и `reports.latest_complete` указывают на отчёт тика 3 (`results/open_report_latest.txt`, `results/open_report_latest_complete.txt`): `reports.latest` → `document_id` → L1 отчёта → текст.

## 9. Ограничения

- Замену и удаление уже проиндексированных файлов я не обрабатываю, по заданию это не требуется. Файл с новым содержимым под старым именем станет новым документом.
- Worker один, по одной activity на очередь. Параллельная загрузка нескольких батчей не требуется, а без неё проще держать каталог согласованным.
- Watcher опрашивает папку периодически, событий ОС он не использует. Задержка обнаружения равна интервалу опроса плюс окно стабильности, около 5 с.
- Temporal dev server (`start-dev`) с SQLite в volume годится для лабораторного стенда, но не для продакшена. Закрытые выполнения он со временем удаляет, поэтому сводку я строю по сохранённым историям, а не по серверу.
- Карта тем строится заново в каждом запуске, поэтому названия и число тем между отчётами не совпадают. Решение «новая тема или расширение» принимает модель, и оно не всегда совпадает с моим (онбординг в тике 2).
- Поиск лабы 3, доступный агенту тем, не скрывает служебные отчёты: они проиндексированы, чтобы их можно было открыть с L0. Но подтвердить тему цитатой из отчёта нельзя (`topic_upsert` принимает только цитаты из зафиксированных документов), а сами отчёты в зафиксированный список не входят.

## 10. Выводы

Больше всего мне помогло то, что я разделила обязанности: workflow только координирует шаги и хранит идентификаторы, а состояние агента, бюджет и checkpoints остаются в harness лабы 2. Поэтому после падения worker новая попытка activity продолжала ту же задачу, а не начинала её заново. Это видно три раза: в загрузке 17.11, в ручном запуске 18.11 и в тике 2 по расписанию 22.11. Идемпотентность пришлось продумывать для каждого шага отдельно: id батча как хеш списка «путь | размер | mtime», `USE_EXISTING` при старте, run key из Workflow ID Schedule, рендер отчёта без текущего времени. Ни повтор события, ни повтор публикации дублей не дали. Фиксация списка в начале запуска тоже работает так, как я задумывала: файл, пришедший после неё, попадает только в следующий тик. Слабее всего у меня решение о новой теме: его принимает модель, и энергопотребление 17.11 и онбординг 22.11 она отнесла к существующим темам, хотя я ждала отдельных. Неожиданнее всего был прогон 20.11: guard `no_progress` и повтор activity в Temporal сами довели отчёт до полного.
