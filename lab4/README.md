# Лабораторная 4. Temporal: автоматическая индексация и обзор тем корпуса

Две задачи автоматизированы через Temporal поверх лаб 1–3:
1. **Новые файлы → индексация.** Watcher замечает готовый файл в `workspace/input` и запускает workflow `IngestNewFiles`. Тот вызывает агента-загрузчика лаб 1/3 в harness лабы 2, который строит L0/L1/L2 в Elasticsearch.
2. **Периодический обзор тем.** Temporal Schedule запускает workflow `TopicReport`. Он фиксирует список документов по L0, агент тематического анализа выделяет темы с подтверждениями из L2, а отчёт публикуется отдельным документом на L0.

Новых ReAct-циклов, своего планировщика и очереди задач нет. Агенты — те же `create_agent` и middleware harness, что в лабе 3.

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

Workflow только координирует шаги (`topicflow/workflows.py` импортирует лишь `temporalio`). LLM, Elasticsearch/MCP и файловые операции выполняются в activities (`topicflow/activities.py`). В историю Temporal попадают идентификаторы и небольшие метаданные; тексты, отчёты, история агента и evidence лежат в workspace, `state.db`/`checkpoints.db` harness и артефактах.

Изменения в предыдущих лабах:
- **лаба 3, `levels.py`:** L0 получил блок `reports` (история отчётов и указатель на последний) и пометку `service: topic_report` у документов `data/_reports`; `corpus_overview` показывает этот блок;
- **лаба 1, `pathguard.py`:** исправлена гонка. При параллельных `move` в новую папку `Path.resolve()` на Windows возвращает путь с префиксом `\\?\`, и проверка ошибочно считала его «вне workspace». Покрыто тестами в `lab1/tests/test_tools_fs.py`.

## 2. Установка и запуск

Python-окружение — venv лабы 3 плюс `temporalio==1.36.1` (`requirements.txt`, полный список версий — `requirements.lock.txt`). Токен LLM берётся из `lab4/.env`, а если его нет — из `lab3/.env` (оба в `.gitignore`).

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

История Temporal хранится в volume `itmo-lab4-temporal`, поэтому переживает перезапуск контейнера. UI: http://localhost:8233.

- `--user root` нужен, потому что процесс образа не может создать файл SQLite в только что созданном volume.
- Из Git Bash команду запускают с `MSYS_NO_PATHCONV=1`, иначе путь `/data` превращается в путь Windows. В PowerShell это не нужно.

Команды (из `lab4`, `$py = ..\lab3\.venv\Scripts\python.exe`):

| Команда | Что делает |
|---|---|
| `$py -m topicflow.cli prepare [--reset]` | workspace, индексы `itmo_lab4_l1/l2`, реестр |
| `$py -m topicflow.cli worker` | Worker: оба workflow и все activities |
| `$py -m topicflow.cli watch` | наблюдение за `workspace/input` и запуск `IngestNewFiles` |
| `$py -m topicflow.cli schedule create --every 10m` | создать Schedule (первый запуск сразу; `--no-trigger` — без него) |
| `$py -m topicflow.cli schedule describe \| pause \| unpause \| trigger \| delete` | статус, пауза, внеочередной запуск, остановка Schedule |
| `$py -m topicflow.cli status` | реестр: батчи, файлы, отчёты (Workflow ID, run ID, task_id) |
| `$py -m topicflow.cli l0` / `open-report --latest` | L0 с блоком отчётов / открыть последний отчёт с L0 |
| `$py -m topicflow.cli topics-now [--set max_model_calls=4]` | разовый запуск `TopicReport` вне расписания |
| `$py -m topicflow.cli fault --activity A --point P --times N\|always` / `--clear` | инъекция сбоя в activity |
| `$py -m topicflow.cli trace` / `export` | истории Temporal → `traces/temporal`; результаты → `results/` |

Пользователь только кладёт файлы в `workspace/input`; команда `drop --set initial|arrivals|new_topic` делает это за него в демо. Полный сценарий со всеми проверками — `scripts\run_lab4_demo.ps1` (в сети ИТМО).

**Репетиция без LLM:** `scripts\run_lab4_demo.ps1 -DryRun -SkipTests` проходит тот же сценарий с настоящими Temporal, Schedule, workflows, activities, harness и Elasticsearch. Отличия:
- вместо qwen работают детерминированные политики из `tests/fake_llm.py` (worker `scripts/dry_run_worker.py`);
- отдельный namespace Temporal (`-DryNamespace`, по умолчанию `dryrun`);
- папка `-DryRoot` (по умолчанию `<диск>:\tmp_lab4`) и индексы `itmo_lab4_dry_*`;
- интервал Schedule 1 мин;
- коммита нет.

Это проверка обвязки, а не демонстрация модели.

## 3. Задача 1: новые файлы → индексация

**Обнаружение** (`topicflow/watcher.py`). Temporal не следит за файловой системой, поэтому её раз в 2 с сканирует отдельный процесс.
- Файл, который ещё пишется, не читается. Имена `*.part`, `*.tmp`, `*.crdownload`, `~*`, `.*` пропускаются; демо пишет `<name>.part` и переименовывает по окончании. Остальные файлы должны 3 с не менять размер и mtime.
- Готовые незарегистрированные файлы одного прохода образуют батч. Его id — хеш списка «путь | размер | mtime», то же событие всегда даёт тот же id.
- Батч и его файлы сначала записываются в реестр (`workspace/.harness/lab4.db`), затем запускается workflow `ingest-<batch_id>` с `id_conflict_policy=USE_EXISTING` и `id_reuse_policy=ALLOW_DUPLICATE_FAILED_ONLY`. Повторный старт того же батча не создаёт второго выполнения, а завершённый Temporal отклоняет.
- При старте watcher перезапускает батчи, чей workflow не работает и не завершён успешно, затем заново сканирует `input`. Папки `input/_*` зарезервированы.

**Workflow `IngestNewFiles`.**

| Шаг (activity) | Что делает | Идемпотентность |
|---|---|---|
| `record_batch` | Workflow ID и run ID в реестр | перезапись тех же полей |
| `stage_batch` | атомарный `os.replace` файлов батча в `input/_batches/<batch_id>/`; тот же контент, что уже есть в `data`, уходит в `rejected/<batch_id>/` со статусом `duplicate` | повтор видит уже перенесённые файлы; исходник не теряется |
| `run_ingest_agent` | агент-загрузчик лаб 1/3 на папке батча, задача harness `ING-<batch_id>` | при повторе продолжается та же задача с checkpoint и накопленным бюджетом |
| `verify_batch` | код проверяет: каждый файл в `data`, есть L1, актуальный L2, в ES 1 запись L1 и столько же L2, сколько фрагментов | только чтение и запись статуса |

В реестре у каждого файла сохраняются исходный путь, хеш, батч, статус (`detected → staged → indexed / duplicate / failed / missing`), `document_id` и текущий путь. Если индексация не завершена, workflow падает с `IngestIncomplete`, а файлы остаются в `input/_batches/<batch_id>`.

## 4. Задача 2: периодический обзор тем

**Schedule** `lab4-topics`: интервал из `--every` или `LAB4_TOPICS_EVERY` (для демо 8 мин), `overlap=SKIP` — период пропускается, пока идёт предыдущий запуск, `catchup_window=1m`.

**Идентичность запуска.** Schedule даёт каждому периоду свой Workflow ID: `lab4-topics-run-<время по расписанию>`. Это run key. Из него выводятся манифест `.harness/lab4/runs/<slug>/manifest.json`, задача harness `TOP-<slug>`, путь отчёта `data/_reports/topics_<slug>.md` и запись в истории отчётов. Повтор activity или публикации после сбоя пишет тот же файл в тот же документ, второго отчёта за запуск не появляется.

**Workflow `TopicReport`.**
1. `freeze_corpus` — фиксирует список документов по L0 один раз за запуск. Служебные отчёты (`data/_reports`), пустые файлы и документы без L1 исключаются с причиной. Файлы, проиндексированные позже, войдут в следующий отчёт.
2. `run_topic_agent` — агент тем (`topicflow/topics.py`): тот же `create_agent` и middleware harness, свой промпт и инструменты:
   - `frozen_documents` — постраничный зафиксированный список;
   - `examine_document` — L1 документа, отмечает его просмотренным;
   - `read_section` / `read_fragment` / поиск лабы 3 и `cite_fragment` — подтверждения из L2;
   - `topic_upsert` / `topic_merge` — карта тем, хранится в `topics.json` и переживает перезапуск;
   - `mark_unreadable`, `topics_status`;
   - `submit_report` — завершение только при полном покрытии: каждый документ просмотрен или отмечен с причиной, отнесён к теме, у каждой темы есть цитата из L2. Иначе `stop_partial`.
   Агент работает частями: страницы списка, отдельные L1, компактификация harness. Весь корпус в один prompt не загружается.
3. `publish_report` — Markdown рендерится только из сохранённого состояния, без текущего времени, поэтому повтор даёт те же байты. Затем атомарная запись, регистрация в каталоге по пути (тот же `document_id`), L1/L2 со стабильными id, запись в `.harness/corpus/reports.json` → блок `reports` в L0 (`history` + `latest`).

**Содержание отчёта:**
- статус ПОЛНЫЙ или ЧАСТИЧНЫЙ с причинами;
- Workflow ID, run ID, задача harness и её расход, ссылка на предыдущий отчёт;
- состав и покрытие: просмотрено L1 X из N, новые документы с прошлого отчёта;
- темы с описаниями, `document_id` и путями, цитаты из L2 со строками и `fragment_id`; новая тема помечается;
- непроверенные документы и ошибки с причинами; таблица зафиксированного списка.

**Открытие с L0:** `open-report --latest` идёт по цепочке L0 `reports.latest.document_id` → L1 отчёта → текст. Основной агент лабы 3 видит тот же блок в `corpus_overview`.

## 5. Повторы, таймауты, перезапуск Worker и harness

| Activity | start-to-close | попыток | heartbeat | не повторяется |
|---|---|---|---|---|
| `record_batch`, `stage_batch`, `verify_batch`, `freeze_corpus`, `publish_report` | 30 с – 5 мин | 5 (1 с, ×2, до 20 с) | — | `UnknownBatch` |
| `run_ingest_agent`, `run_topic_agent` | 2 ч | 3 (5 с, ×2, до 1 мин) | 90 с (в демо 30 с) | — |

- **Агентная activity.** Каждые 10 с она шлёт heartbeat с расходом задачи. Если Worker убит, Temporal по heartbeat timeout отдаёт попытку живому Worker. Новая попытка открывает ту же задачу harness (`ING-…` / `TOP-…`) и продолжает её с последнего checkpoint и с накопленным бюджетом. В событиях задачи остаётся `temporal_attempt` с номером попытки и `resumed=true`.
- **Исчерпание бюджета** и собственный `stop_partial` агента — окончательный результат: повтор не добавит бюджета. Отчёт публикуется как ЧАСТИЧНЫЙ. Прочие остановки (сервер LLM недоступен, сбой) поднимают повторяемую ошибку, ограниченную 3 попытками.
- **Если агентная activity окончательно упала,** workflow всё равно проверяет или публикует то, что есть, и не помечает результат полным.
- **Сбои для демо** задаются файлом `faults.json`, без перезапуска Worker. `publish_report --point after_write` падает между записью файла и регистрацией на L0: следующая попытка завершает тот же отчёт.

Ограничения лабы 2 сохраняются для обоих агентов: task root, рабочий лимит контекста 28 000 при окне 262 144, компактификация, лимит вывода tools с артефактами, бюджет токенов, вызовов и tools, таймауты.

## 6. Тестовые данные

`corpus/`:
- `initial/` — 10 документов, темы: хранение состояния, поиск, эксплуатация, протокол совещания;
- `arrivals/` — 2 файла, добавляются во время работы;
- `new_topic/inference_energy.md` — новая тема (энергопотребление инференса) для второго периода.

Индекс воспроизводится тем же путём: `prepare --reset`, затем файлы в `input`, всё остальное делают Temporal и агенты.

## 7. Тесты

`..\lab3\.venv\Scripts\python.exe -m pytest -q` — 6 тестов. Нужен Elasticsearch, последний тест — ещё и Temporal на `localhost:7233` (иначе пропускается). Вместо LLM — детерминированные политики `tests/fake_llm.py`. Они работают по 5 документов за шаг, как требует лимит вывода шага, и продолжают работу после падения worker. Сводку они писать не умеют, поэтому для них поднят рабочий лимит контекста (`limit_overrides`); сама компактификация проверена в лабе 2.

| Тест | Что проверяет |
|---|---|
| `test_watcher_ignores_partial_files_and_waits_for_stability` | `.part` не берётся, ожидание стабильности, повторное событие не регистрирует батч второй раз |
| `test_ingestion_batch_is_idempotent_and_duplicates_are_not_indexed` | батч → `data` + L1/L2/ES; повтор каждого шага ничего не дублирует; тот же контент под другим именем → `rejected/`, без второго документа |
| `test_two_reports_on_l0_and_the_second_covers_the_new_topic` | два отчёта на L0, `latest` и история; повтор публикации — тот же документ; второй отчёт видит новый документ и тему, отчёты в анализ не входят |
| `test_budget_exhaustion_gives_a_partial_report_and_retry_does_not_duplicate` | бюджет → ЧАСТИЧНЫЙ отчёт с причинами; сбой после записи файла → повтор без дубля |
| `test_agent_task_is_resumed_not_restarted` | повтор агентной activity продолжает ту же задачу harness, счётчики не сбрасываются |
| `test_workflows_on_real_temporal_service` | оба workflow на настоящем Temporal; `publish_report` повторён Temporal (attempt 2), отчёт один |

## 8. Результаты прогона

_Заполняется после прогона `scripts\run_lab4_demo.ps1` в сети ИТМО: Workflow ID, run ID, Activity attempts, task_id harness, отчёты (`results/reports/`), L0 (`results/l0.json`), трассы (`traces/temporal/summary.md`, `traces/*.jsonl`)._

## 9. Ограничения

- Обработка замены и удаления уже проиндексированных файлов не реализована (по заданию). Файл с новым содержимым под старым именем станет новым документом.
- Один Worker, по одной activity на очередь. Параллельная загрузка нескольких батчей не требуется и упрощает согласованность каталога.
- Watcher — периодический опрос, без событий ОС: задержка обнаружения — интервал опроса плюс окно стабильности (около 5 с).
- Temporal dev server (`start-dev`) с SQLite в volume: для лабораторного стенда, не для продакшена.
