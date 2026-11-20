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

Предыдущие лабы пришлось немного поправить:
- лаба 3, `levels.py`: в L0 появился блок `reports` (история отчётов и указатель на последний), а документы из `data/_reports` получают пометку `service: topic_report`. `corpus_overview` показывает этот блок;
- лаба 1, `pathguard.py`: я исправила гонку. При параллельных `move` в новую папку `Path.resolve()` на Windows возвращает путь с префиксом `\\?\`, и проверка ошибочно считала его «вне workspace». Исправление покрыто тестами в `lab1/tests/test_tools_fs.py`.

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
| `& $py -m topicflow.cli schedule create --every 10m` | создать Schedule (первый запуск сразу; `--no-trigger` — без него) |
| `& $py -m topicflow.cli schedule describe \| pause \| unpause \| trigger \| delete` | статус, пауза, внеочередной запуск, остановка Schedule |
| `& $py -m topicflow.cli status` | реестр: батчи, файлы, отчёты (Workflow ID, run ID, task_id) |
| `& $py -m topicflow.cli l0` / `open-report --latest` | L0 с блоком отчётов / открыть последний отчёт с L0 |
| `& $py -m topicflow.cli topics-now [--set max_model_calls=4]` | разовый запуск `TopicReport` вне расписания |
| `& $py -m topicflow.cli fault --activity A --point P --times N\|always` / `--clear` | инъекция сбоя в activity |
| `& $py -m topicflow.cli replay --batch <batch_id>` | повторить событие обнаружения для батча (Temporal не запустит второе выполнение) |
| `& $py -m topicflow.cli wait --batches \| --schedule-runs N [--after T] \| --workflow-id W` | дождаться загрузки / запусков Schedule / workflow |
| `& $py -m topicflow.cli trace` / `export` | истории Temporal → `traces/temporal`; результаты → `results/` |

Пользователь только кладёт файлы в `workspace/input`. В демо это за него делает команда `drop --set initial|arrivals|new_topic`. Полный сценарий со всеми проверками лежит в `scripts\run_lab4_demo.ps1` (запускается в сети ИТМО). В конце скрипт сам делает `git commit` и `git push` своих `traces`, `results` и `corpus`. С ключом `-SkipPush` он только записывает результаты, без коммита.

**Репетиция без LLM.** Команда `scripts\run_lab4_demo.ps1 -DryRun -SkipTests` проходит тот же сценарий с настоящими Temporal, Schedule, workflows, activities, harness и Elasticsearch. Отличия такие:
- вместо qwen работают детерминированные политики из `tests/fake_llm.py` (worker `scripts/dry_run_worker.py`);
- отдельный namespace Temporal (`-DryNamespace`, по умолчанию `dryrun`);
- папка `-DryRoot` (по умолчанию `<диск>:\tmp_lab4`) и индексы `itmo_lab4_dry_*`;
- интервал Schedule 1 мин;
- коммита нет.

Это проверка обвязки, а не демонстрация модели. Так сценарий можно отладить без доступа к qwen.

## 3. Задача 1: новые файлы → индексация

**Обнаружение** (`topicflow/watcher.py`). Temporal не следит за файловой системой, поэтому её раз в 2 с сканирует отдельный процесс.
- Файл, который ещё пишется, не читается. Имена `*.part`, `*.tmp`, `*.crdownload`, `~*`, `.*` пропускаются. Демо пишет `<name>.part` и переименовывает файл, когда запись закончена. Остальные файлы должны 3 с не менять размер и mtime.
- Готовые незарегистрированные файлы одного прохода образуют батч. Его id считается как хеш списка «путь | размер | mtime», поэтому одно и то же событие всегда даёт тот же id.
- Сначала батч и его файлы записываются в реестр (`workspace/.harness/lab4.db`), потом запускается workflow `ingest-<batch_id>` с `id_conflict_policy=USE_EXISTING` и `id_reuse_policy=ALLOW_DUPLICATE_FAILED_ONLY`. Если тот же батч стартует повторно, второго выполнения не появляется, а повтор уже завершённого батча Temporal отклоняет.
- При старте watcher перезапускает батчи, у которых workflow не работает и не завершён успешно. На каждый старт watcher это один новый запуск, а всего упавший батч перезапускается не больше трёх раз (`MAX_BATCH_RESTARTS`, счётчик хранится в реестре и переживает перезапуски watcher). Дальше батч ждёт меня, а watcher пишет об этом в лог. Тест `test_failed_batch_is_restarted_a_bounded_number_of_times_over_watcher_starts`. Потом watcher заново сканирует `input`. Папки `input/_*` зарезервированы.
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

**Schedule** `lab4-topics`. Интервал берётся из `--every` или `LAB4_TOPICS_EVERY` (в демо 8 мин), `catchup_window=1m`. Стоит `overlap=SKIP`: пока идёт предыдущий запуск, очередной период пропускается.

**Идентичность запуска.** Каждому периоду Schedule даёт свой Workflow ID: `lab4-topics-run-<время по расписанию>`. Его я и использую как run key. Из него выводятся манифест `.harness/lab4/runs/<slug>/manifest.json`, задача harness `TOP-<slug>`, путь отчёта `data/_reports/topics_<slug>.md` и запись в истории отчётов. Если после сбоя повторяется activity или публикация, она пишет тот же файл в тот же документ, и второго отчёта за запуск не появляется.

**Workflow `TopicReport`.**
1. `freeze_corpus` фиксирует список документов по L0 один раз за запуск. Служебные отчёты (`data/_reports`), пустые файлы и документы без L1 исключаются, причина записывается. Файлы, проиндексированные позже, войдут в следующий отчёт. Документы, которых не было в списке предыдущего отчёта, получают пометку `new_since_previous_report`.
2. `run_topic_agent` запускает агента тем (`topicflow/topics.py`). Это тот же `create_agent` с middleware harness, но со своим промптом и инструментами:
   - `frozen_documents`: зафиксированный список постранично, с пометкой новых документов. Промпт требует, чтобы для каждого нового документа агент явно решил, новая это тема или расширение существующей;
   - `examine_document`: L1 документа, документ отмечается просмотренным;
   - `read_section` / `read_fragment` / поиск лабы 3 и `cite_fragment`: подтверждения из L2;
   - `topic_upsert` / `topic_merge`: карта тем, она хранится в `topics.json` и переживает перезапуск;
   - `mark_unreadable`, `topics_status`;
   - `submit_report`: завершить можно только при полном покрытии, то есть каждый документ просмотрен или отмечен с причиной и отнесён к теме, а у каждой темы есть цитата из L2. Иначе остаётся `stop_partial`.
   Агент работает частями: страницы списка, отдельные L1, компактификация harness. Весь корпус в один prompt не загружается.
3. `publish_report` рендерит Markdown только из сохранённого состояния, без текущего времени, поэтому повтор даёт те же байты. Дальше идут атомарная запись, регистрация в каталоге по пути (тот же `document_id`), L1/L2 со стабильными id и запись в `.harness/corpus/reports.json`. Из неё строится блок `reports` в L0: `history`, `latest` и `latest_complete` (последний полный отчёт).

Что есть в отчёте:
- статус ПОЛНЫЙ или ЧАСТИЧНЫЙ с причинами;
- Workflow ID, run ID, задача harness и её расход, ссылка на предыдущий отчёт;
- состав и покрытие: просмотрено L1 X из N, новые документы с прошлого отчёта и таблица «новый документ → тема → новая тема или расширение»;
- темы с описаниями, `document_id` и путями, цитаты из L2 со строками и `fragment_id`; новые темы и темы, расширенные новыми документами, помечены;
- непроверенные документы и ошибки с причинами, таблица зафиксированного списка.

**Открытие с L0.** `open-report --latest` (или `--latest-complete`) идёт по цепочке L0 `reports.latest.document_id` → L1 отчёта → текст. Основной агент лабы 3 видит тот же блок в `corpus_overview`.

## 5. Повторы, таймауты, перезапуск Worker и harness

| Activity | start-to-close | попыток | heartbeat | не повторяется |
|---|---|---|---|---|
| `record_batch`, `stage_batch`, `verify_batch`, `freeze_corpus`, `publish_report` | 30 с – 5 мин | 5 (1 с, ×2, до 20 с) | — | `UnknownBatch` |
| `run_ingest_agent`, `run_topic_agent` | 1 ч | 3 (5 с, ×2, до 1 мин) | 90 с (в демо 30 с) | — |

В записанных прогонах 17–18.11 start-to-close у агентных activity был 2 ч (`startToCloseTimeout=7200s` в историях). После прогонов я сократила его до 1 ч, а execution timeout workflow сделала одинаковым, 4 ч (в историях 17–18.11 он ещё 6 ч у загрузки и 3 ч у отчёта: `workflowExecutionTimeout` 21600 s и 10800 s, в прогоне 20.11 уже 14400 s у обоих). Теперь 3 попытки с backoff и публикация отчёта помещаются в лимит workflow (коммит 6f2d2ba).

Агентная activity каждые 10 с шлёт heartbeat с расходом задачи. Если Worker убит, Temporal по heartbeat timeout отдаёт попытку живому Worker. Новая попытка открывает ту же задачу harness (`ING-…` / `TOP-…`) и продолжает её с последнего checkpoint, бюджет при этом накопленный, а не новый. В событиях задачи остаётся `temporal_attempt` с номером попытки и `resumed=true`.

Исчерпание бюджета и собственный `stop_partial` агента я считаю окончательным результатом: повтор бюджета не добавит. Отчёт в этом случае публикуется как ЧАСТИЧНЫЙ. Остальные остановки (сервер LLM недоступен, сбой) поднимают повторяемую ошибку, и попыток не больше 3. Если агентная activity всё-таки окончательно упала, workflow всё равно проверяет или публикует то, что есть, и не помечает результат полным.

Сбои для демо задаются файлом `faults.json`, Worker для этого перезапускать не нужно. Например, `publish_report --point after_write` падает между записью файла и регистрацией на L0, и следующая попытка завершает тот же отчёт.

Ограничения лабы 2 сохраняются для обоих агентов: task root, рабочий лимит контекста 28 000 при окне 262 144, компактификация, лимит вывода tools с артефактами, бюджет токенов, вызовов и tools, таймауты.

## 6. Тестовые данные

`corpus/`:
- `initial/` — 10 документов, темы: хранение состояния, поиск, эксплуатация, протокол совещания;
- `arrivals/` — 2 файла, добавляются во время работы;
- `new_topic/inference_energy.md` — новая тема (энергопотребление инференса) для второго периода;
- `late/latency_budget.md` — файл для сценария E3 (кладётся, когда watcher не работает);
- `new_topic2/ui_accessibility.md` — ещё одна совсем новая тема (доступность интерфейса) для части `-Part growth`.

Как воспроизвести индекс и все сценарии (из `lab4`, `$py = ..\lab3\.venv\Scripts\python.exe`):

```powershell
& $py -m topicflow.cli prepare --reset          # пустой workspace и индексы itmo_lab4_l1 / itmo_lab4_l2
& $py -m topicflow.cli worker                   # окно 1
& $py -m topicflow.cli watch                    # окно 2
& $py -m topicflow.cli drop --set initial       # окно 3: 10 файлов -> автоматическая загрузка
& $py -m topicflow.cli wait --batches
& $py -m topicflow.cli schedule create --every 8m
& $py -m topicflow.cli drop --set arrivals; & $py -m topicflow.cli drop --set new_topic
```

Всё это вместе со сценариями сбоев выполняет `scripts\run_lab4_demo.ps1`.

## 7. Тесты

`..\lab3\.venv\Scripts\python.exe -m pytest -q` запускает 10 тестов. Им нужен Elasticsearch, а последнему ещё и Temporal на `localhost:7233` (без него тест пропускается). Вместо LLM работают детерминированные политики `tests/fake_llm.py`. Они берут по 5 документов за шаг, как требует лимит вывода шага, и продолжают работу после падения worker. Писать сводку они не умеют, поэтому для них я подняла рабочий лимит контекста (`limit_overrides`). Саму компактификацию я проверяла в лабе 2.

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

## 8. Результаты прогона

Основной прогон `scripts\run_lab4_demo.ps1` я сделала 17.11.2026 в сети ИТМО: модель `qwen3.8-27b`, Temporal dev server 1.10.0 в Docker, namespace `default`, Schedule каждые 8 мин. Всё заняло около 10 минут.

Где лежат результаты:
- истории Temporal — `traces/temporal/*.json`, сводка попыток — `traces/temporal/summary.md`;
- трассы агентов harness — `traces/ING-*`, `traces/TOP-*`;
- отчёты — `results/reports/`; L0 с блоком отчётов — `results/l0.json`, `results/reports.json`;
- реестр — `results/registry.json`, `results/status.txt`; задачи harness — `results/<task_id>/`;
- проверка поиска по автоматически загруженным файлам (grep, full-text и vector в Elasticsearch) — `results/search_check.txt`;
- зафиксированные списки корпуса и карты тем по каждому запуску — `results/runs/<run>/`.

| Сценарий | Workflow ID / run ID | Activity attempts | Задача harness | Результат |
|---|---|---|---|---|
| Автоматическая загрузка 10 файлов + падение worker | `ingest-4d4e776d7c` / `01a1eb3f-27b8-7aff-bf3f-459bd0405a73` | `run_ingest_agent`: **2** (первая — `Heartbeat timeout`) | `ING-4d4e776d7c`: 2 запуска, 13 вызовов модели, 80 tools, 3 компактификации | Worker #1 убит после 10-го tool (`HARNESS_CRASH_AT`, код 137). Через heartbeat timeout (30 с; попытка 2 стартовала через 38 с после первой) Temporal отдал попытку живому worker, и она продолжила ту же задачу: `temporal_attempt` attempt 2, `resumed=true`, `runs_before=1`. 10/10 файлов в `data`, L1, L2 и ES, `verify_batch` — completed |
| Повтор события | тот же `ingest-4d4e776d7c` | — | — | `replay` отклонён Temporal: workflow с этим id уже завершён, второго выполнения нет. Вывод повтора и `temporal workflow list` по этому id — в `results/replay_event.txt`. Повтор, пока workflow выполняется, возвращает то же выполнение (`USE_EXISTING`), это проверено тестом на настоящем Temporal |
| Тот же контент под другим именем | `ingest-5c534583b2` / `01a1eb41-b371-741c-8fb6-c8d77fcdc7ed` | `stage_batch`: 1 | — | `oncall_copy.md` → `rejected/`, статус `duplicate` → `data/ops/oncall.md`; второго документа нет |
| Schedule, запуск 1 (немедленный запуск при создании Schedule, `trigger_immediately`; в истории есть `TemporalScheduledById=lab4-topics`) | `lab4-topics-run-2026-11-17T20:45:20Z` / `01a1eb41-ceae-7b36-b526-11d7d8b12c0b` | все по 1 | `TOP-20261117T204520Z`: 15 вызовов, 39 tools | **ПОЛНЫЙ**, 10/10 документов, 4 темы, 10 цитат L2 |
| Рост корпуса: 2 файла + новая тема | `ingest-578a8801b8` / `01a1eb42-fd50-794f-a9c0-553f1e4e11a4` | все по 1 | `ING-578a8801b8`: 12 вызовов, 27 tools | 3/3 файла проиндексированы автоматически, пока Schedule стоял на паузе |
| Schedule, запуск 2 (очередной тик интервала 20:48:00) | `lab4-topics-run-2026-11-17T20:48:00Z` / `01a1eb44-3c62-7bb7-b73f-f80cafc476ed` | все по 1 | `TOP-20261117T204800Z`: 22 вызова, 58 tools, 2 компактификации | **ПОЛНЫЙ**, 13/13 документов, 4 темы, 13 цитат. В отчёте названы 3 новых документа; отчёт 1 сохранён и в анализ не вошёл (исключён 1 служебный документ) |
| Сбой `publish_report` после записи файла | `lab4-topics-manual-2026-11-17T20:49:46Z` / `01a1eb45-dd8c-7017-b610-64d54801bc28` | `publish_report`: **2** (первая — `injected fault … after_write`) | `TOP-manual-20261117T204946Z` | Повтор дописал тот же файл и тот же документ; в истории L0 одна запись за этот запуск |
| Исчерпание бюджета | `lab4-topics-manual-2026-11-17T20:51:11Z` / `01a1eb47-2688-72d9-95c2-6549164afc5c` | все по 1 (бюджет окончателен, повтора нет) | `TOP-manual-20261117T205111Z`: лимит 4 вызова | **ЧАСТИЧНЫЙ**: просмотрено 5 из 13, причины перечислены в отчёте |
| Сбой `stage_batch` при каждой попытке | `ingest-6341c09670` / `01a1eb47-679e-7bba-8063-6a8d88ffa901` | `stage_batch`: **5**, затем workflow FAILED | — | Повторов ровно 5, файл остался в `input`. После `fault --clear` и перезапуска watcher тот же Workflow ID запущен заново: run `01a1eb48-4b63-7e17-938f-d8d582116708` — completed, это дубль `bm25_basics.md` → `rejected/` |

**Что решили агенты.** Загрузчик сам выбрал папки `storage`, `search`, `ops`, `meetings`. Агент тем выделил 4 темы и подписал их по-русски: эксплуатация, хранение состояния, поиск, решения совещания M-05. У каждой темы есть цитаты из L2 со строками и `fragment_id`.

С новой темой вышло не так, как я рассчитывала. Документ про энергопотребление инференса загрузчик положил в `ops`. Во втором отчёте он учтён: назван среди новых документов и процитирован («0,9 Вт·ч на запрос», «2,4 кВт»). Но отдельной темой qwen его не выделила, а расширила тему эксплуатации до «… дежурства и энергия». Поэтому пометки «новая тема» нет: она ставится только теме, у которой все документы новые. Это решение модели о группировке, покрытие при этом полное.

**Открытие с L0.** Вывод `open-report --latest` я сохранила в `results/open_report_latest.txt`: `reports.latest` → `document_id` → L1 отчёта → текст. На момент основного прогона последним был частичный отчёт из сценария с бюджетом, и он помечен `partial`. Для такого случая есть `reports.latest_complete` и `open-report --latest-complete`. После дополнительных прогонов в `reports.history` 6 отчётов, последний из них полный (см. ниже).

Запуски тестов (`test-*`) в выгрузку `traces/temporal` не входят: тесты работают в том же namespace, и `trace` их пропускает.

С логами процессов основного прогона 17.11 я промахнулась: `traces/processes/watcher1.log`, `watcher2.log` и `worker2.log` оказались пустыми. Вывод Python буферизовался, а процессы останавливаются принудительно. Пустые файлы я убрала. Автоматический старт и повтор события подтверждаются историями Temporal (`ingest-4d4e776d7c`, identity worker в событиях `ActivityTaskStarted`), реестром (`results/status.txt`) и `results/replay_event.txt`. Начиная с прогонов 18.11 логи пишутся без буферизации (`watcher3.log` и др.).

**Исправлено после основного прогона.** Эти ошибки я нашла, когда проверяла код после прогона. На результаты таблицы выше они не влияют, исправления покрыты тестами.
- Перезапуск недоиндексированного батча. Если агент уже перенёс файл в `data`, но не построил L1/L2, перезапущенный батч считал файл пропавшим и объявлял себя завершённым. Теперь файл остаётся в батче, агент запускается снова и продолжает задачу, а `verify_batch` проверяет файл. Тест `test_restarted_incomplete_batch_runs_the_agent_again_instead_of_claiming_success`.
- Одинаковый файл в двух батчах. Если оба батча забирали его до индексации, документ мог появиться дважды. Теперь второй уходит в `rejected/`. Тест `test_same_content_in_two_batches_staged_before_indexing_is_one_document`.
- Watcher при недоступном Temporal. Watcher не падает и раз в ~30 с запускает батчи, которые ещё не стартовали. Тест `test_watcher_survives_temporal_outage_and_rescans_after_restart`.
- Каталог. Публикация отчёта пишет каталог под той же блокировкой, что и загрузчик.
- Ручные запуски. Ключ `topics-now` уникален до миллисекунды.
- L0 получил указатель `latest_complete`.
- Новые документы. Агент тем видит пометку новых документов, а в отчёте есть таблица «новый документ → тема».
- Логи процессов пишутся без буферизации.

### Дополнительные сценарии (`-Part extra`)

Три сценария из раздела 05 в основном прогоне я показала не полностью:
- worker падал во время агента-загрузчика, а не во время агента тем;
- перекрытия запусков Schedule не возникло;
- файл, добавленный при остановленном watcher, не проверялся.

Я вынесла их в отдельную часть сценария, которая работает поверх корпуса основного прогона:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_lab4_demo.ps1 -Part extra -SkipTests
```

- E1. Worker убивается после 6-го tool агента тем во время запуска по расписанию. Попытка 2 на новом worker должна продолжить ту же задачу `TOP-…`.
- E2. Пока этот запуск не завершён, выполняется `schedule trigger`. Политика SKIP пропускает внеочередной запуск, это видно по `skipped_overlap` в `results/schedule_overlap.json`.
- E3. Файл `corpus/late/latency_budget.md` кладётся в `input`, когда ни один watcher не работает. Watcher, запущенный после этого, подбирает его при первом сканировании.

**Прогон `-Part extra` 18.11.2026** (qwen3.8-27b, корпус основного прогона):

| Сценарий | Workflow ID / run ID | Результат |
|---|---|---|
| E2. Перекрытие запусков | `lab4-topics-run-2026-11-18T11:59:36Z` / `01a1ee86-d5d8-763c-8487-cb9a69444dde` | Пока этот запуск шёл, Schedule пропустил два запуска. Сразу после `schedule trigger`: `num_actions=1`, `skipped_overlap=1`, запуск в `running` (`results/schedule_overlap.json`). После завершения `skipped_overlap=2`: очередной тик в 12:00:00 тоже пропущен (`results/schedule_after_overlap.json`). Сам запуск дал **ПОЛНЫЙ** отчёт: 13/13 документов, 3 темы (`TOP-20261118T115936Z`: 15 вызовов модели, 47 tools, 2 компактификации) |
| E3. Пересканирование после перезапуска детектора | `ingest-6b93f9adf0` / `01a1ee88-7d1a-734f-b414-da9cbd1e13d0` | `latency_budget.md` положен в `input`, когда watcher не работал. Watcher, запущенный после этого, при первом сканировании нашёл файл (`traces/processes/watcher3.log`) и запустил workflow. Файл проиндексирован: `data/ops/latency_budget.md`, все activities с первой попытки |
| E1. Падение worker во время агента тем | — | **В этом прогоне не показан.** Worker #3 упал сразу при старте, до того как взял задачу: Elasticsearch после `docker compose up` уже открыл порт, но ещё не отвечал (`ConnectionError`, выдержка из лога — `results/worker3_startup_error.txt`). Запуск выполнил worker #4 с первой попытки. Скрипт тогда этого не заметил |

После этого прогона я поправила скрипт и worker:
- скрипт ждёт готовности Elasticsearch по `_cluster/health`;
- скрипт проверяет, что worker вышел именно в точке сбоя (код 137), а иначе останавливается с ошибкой;
- worker при старте повторяет подключение к Elasticsearch.

Сценарий E1 я вынесла в отдельную часть `-Part topic-crash`. Агент тем там запускается вручную через `topics-now`; в репетиции попытка 2 продолжила ту же задачу `TOP-…`.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_lab4_demo.ps1 -Part topic-crash -SkipTests
```

**Прогон `-Part topic-crash` 18.11.2026** (qwen3.8-27b):

| Сценарий | Workflow ID / run ID | Activity attempts | Задача harness | Результат |
|---|---|---|---|---|
| E1. Падение worker во время агента тем | `lab4-topics-manual-2026-11-18T13:20:31.699Z` / `01a1eed0-ebff-7971-aac5-c070e2841a09` | `run_topic_agent`: **2** (первая — `Heartbeat timeout`) | `TOP-manual-20261118T132031699Z`: 2 запуска, 16 вызовов модели, 55 tools, 2 компактификации | Worker #5 убит после 6-го tool агента тем (`simulated_crash after_tool:6`, код 137). Попытка 2 на worker #6 через 36 с продолжила ту же задачу: `temporal_attempt` attempt 2, `resumed=true`, `runs_before=1`, `status_before=running`; в трассе `a22` записано `resume_kind=continue_interrupted_step`. Отчёт **ПОЛНЫЙ**: 14/14 документов, 5 тем, 14 цитат L2 |

В этом отчёте новый документ `latency_budget.md` попал в таблицу «Новые документы и их темы» с пометкой «расширяет тему», а тема T4 помечена «расширена новыми документами: 1». Энергопотребление инференса qwen на этот раз выделила отдельной темой T5. После этого прогона `reports.latest` и `reports.latest_complete` указывают на этот полный отчёт (`results/open_report_latest.txt`, `results/open_report_latest_complete.txt`).

**Рост корпуса с новой темой по Schedule и повтор события во время работы (`-Part growth`).**

Эту часть я добавила, потому что двух вещей в прогонах не хватало. В основном прогоне новый документ попал в расширенную тему, а отдельной темой он стал только в ручном запуске `topics-now`. Повтор события, пока workflow выполняется, был показан только тестом.

Что делает часть:
- кладёт файл на совсем новую тему (`corpus/new_topic2/ui_accessibility.md`, требования доступности интерфейса, ACC-2026-01);
- сразу повторяет событие обнаружения, пока workflow ещё в RUNNING. Вывод `replay` и `temporal workflow list` сохраняется в `results/replay_while_running.txt`: там одно выполнение и тот же run ID;
- после загрузки создаёт Schedule (`--every 30m`) и ждёт первый его запуск. Это немедленное действие Schedule при создании (`trigger_immediately`), а не тик интервала: интервал в 30 мин я взяла, чтобы второй запуск не начался до конца скрипта. Настоящий тик интервала есть в основном прогоне 17.11 (запуск 20:48:00 при `--every 8m`).

Ещё я добавила в отчёт строку «время запуска по расписанию». В отчёте этого прогона она подписана «(тик Schedule)», что для немедленного действия неточно; после прогона подпись стала «(запуск Schedule)».

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_lab4_demo.ps1 -Part growth -SkipTests
```

**Прогон `-Part growth` 20.11.2026** (qwen3.8-27b, корпус после предыдущих прогонов):

| Сценарий | Workflow ID / run ID | Activity attempts | Задача harness | Результат |
|---|---|---|---|---|
| Файл на новую тему | `ingest-1362a3c0e5` / `01a1f963-c315-7816-a1c0-ca81a6e793c8` | все по 1 | `ING-1362a3c0e5`: 1 запуск | Watcher сам увидел `ui_accessibility.md` и запустил workflow (`traces/processes/watcher7.log`). Загрузчик положил файл в `data/ops/ui_accessibility.md`, L1, L2 и ES построены, workflow завершился за 37 с |
| Повтор события, пока workflow в RUNNING | тот же `ingest-1362a3c0e5` | — | — | `replay` вернул то же выполнение `01a1f963-…` (политика `USE_EXISTING`), нового не создано. `temporal workflow list` по этому id в тот момент показывает одну строку со статусом `Running` (`results/replay_while_running.txt`) |
| Отчёт по Schedule | `lab4-topics-run-2026-11-20T14:37:46Z` / `01a1f964-5cd9-7bff-bf37-9bef906c65bb` | `run_topic_agent`: **2** | `TOP-20261120T143746Z`: 2 запуска, 30 вызовов модели, 94 tools, 400 774 токена | Запуск сделал сам Schedule (`TemporalScheduledById=lab4-topics`, первое действие при создании). Отчёт **ПОЛНЫЙ**: 15/15 документов, 4 темы, 15 цитат L2. Новый документ стал **отдельной темой T4** «Доступность интерфейса» с пометкой «новая тема» и есть в таблице «Новые документы и их темы». Прошлые отчёты на месте, в анализ не вошли (исключено 6 служебных) |

Падение первой попытки `run_topic_agent` в этом прогоне я не планировала. Модель шесть раз подряд вызвала `read_fragment` с одними и теми же аргументами и каждый раз получила один и тот же ответ. Сработал guard `no_progress` из harness лабы 2: задача ушла в `paused`, activity завершилась ошибкой (`lastFailure` в истории Temporal: «no progress: read_fragment returned the same result 6 times»). Temporal повторил activity, вторая попытка продолжила ту же задачу `TOP-…` (трасса `a22`) и довела отчёт до конца. Получился ещё один живой пример того, как guard в harness и повтор в Temporal работают вместе, причём без моего вмешательства.

После прогона `reports.latest` и `reports.latest_complete` указывают на этот отчёт (`results/open_report_latest.txt`, `results/open_report_latest_complete.txt`).

## 9. Ограничения

- Замену и удаление уже проиндексированных файлов я не обрабатываю, по заданию это не требуется. Файл с новым содержимым под старым именем станет новым документом.
- Worker один, по одной activity на очередь. Параллельная загрузка нескольких батчей не требуется, а без неё проще держать каталог согласованным.
- Watcher опрашивает папку периодически, событий ОС он не использует. Задержка обнаружения равна интервалу опроса плюс окно стабильности, около 5 с.
- Temporal dev server (`start-dev`) с SQLite в volume годится для лабораторного стенда, но не для продакшена.
- Поиск лабы 3, доступный агенту тем, не скрывает служебные отчёты: они проиндексированы, чтобы их можно было открыть с L0. Но подтвердить тему цитатой из отчёта нельзя (`topic_upsert` принимает только цитаты из зафиксированных документов), а сами отчёты в зафиксированный список не входят.

## 10. Выводы

Больше всего мне помогло то, что я разделила обязанности: workflow только координирует шаги и хранит идентификаторы, а состояние агента, бюджет и checkpoints остаются в harness лабы 2. Благодаря этому после падения worker новая попытка activity продолжала ту же задачу, а не начинала её заново. Это видно и в загрузке 17.11, и в прогоне `-Part topic-crash` 18.11. Идемпотентность пришлось продумывать для каждого шага отдельно: id батча как хеш списка «путь | размер | mtime», `USE_EXISTING` при старте, run key из Workflow ID Schedule, рендер отчёта без текущего времени. В итоге ни повтор события, ни повтор публикации дублей не дали. С первого раза получилось не всё: E1 сорвался из-за того, что Elasticsearch ещё не отвечал, логи 17.11 оказались пустыми, а qwen в основном прогоне не выделила новую тему отдельно. Первые две проблемы я закрыла правками скрипта и логирования, а отдельную тему модель выделила уже в следующих прогонах. Неожиданнее всего был прогон 20.11: guard `no_progress` и повтор activity в Temporal сами довели отчёт до полного.
