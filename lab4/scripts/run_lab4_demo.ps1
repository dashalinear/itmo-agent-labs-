<#
Records all lab 4 scenarios with the real ITMO model, the real Temporal service and the lab 3 agents/harness.
Run INSIDE the ITMO network, Docker Desktop running (Elasticsearch from lab 1, Temporal dev server in Docker):

    powershell -ExecutionPolicy Bypass -File scripts\run_lab4_demo.ps1
    powershell -ExecutionPolicy Bypass -File scripts\run_lab4_demo.ps1 -SkipTests -Every 8m   # add -Push to commit and push

Scenarios (all automatic, the script only plays the user: it drops files into workspace\input):
  1  automatic ingestion: 10 files -> watcher -> IngestNewFiles; worker #1 is killed DURING the ingestion agent
     (HARNESS_CRASH_AT), worker #2 continues: Temporal retries the activity, the harness task is resumed
  2  duplicate event: the same batch is started again (Temporal: same workflow id) + the same content under
     another name (rejected by content hash, no second document)
  3  Schedule: run 1 -> report 1 at L0
  4  corpus growth: schedule paused, 2 files + 1 file on a new topic dropped and ingested automatically,
     schedule unpaused -> run 2 -> report 2 covers the new document/topic, report 1 kept
  5  failures: publish_report fails after writing the file (retry, no duplicate report); budget exhaustion
     (partial report); stage_batch always fails (bounded retries, file not lost), then the watcher restart
     picks the batch up again
  6  traces (Temporal histories + harness), results, L0 view, opening the latest report from L0

-Part extra (on the corpus of a full run, no reset):
  E1  worker killed DURING the topic-analysis agent of a scheduled run; the retry resumes the same harness task
  E2  overlap: the schedule is triggered while that run is unfinished -> skipped by the SKIP policy
  E3  a file is added while no watcher runs; the watcher started afterwards picks it up (rescan)
#>
param(
    [ValidateSet("all", "extra", "topic-crash", "growth", "ticks")][string]$Part = "all",
    [switch]$SkipPush,
    [switch]$Push,
    [switch]$SkipTests,
    [string]$Every = "8m",
    [string]$TemporalImage = "temporalio/temporal:1.10.0",
    [switch]$DryRun,                              # rehearsal without the LLM: test policies, namespace dryrun, own folder
    [string]$DryRoot = "",
    [string]$DryNamespace = "dryrun"
)

$ErrorActionPreference = "Continue"     # native tools (docker, python) write progress to stderr; failures are checked by exit codes
# git commit + push of the results only on request (-Push); without it the script just records them
if (-not $Push) { $SkipPush = $true }
$lab = Split-Path -Parent $PSScriptRoot
Set-Location $lab
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"                 # worker/watcher logs are written line by line (processes are stopped hard)
$py = Join-Path $lab "..\lab3\.venv\Scripts\python.exe"
$envFile = if (Test-Path (Join-Path $lab ".env")) { Join-Path $lab ".env" } else { Join-Path $lab "..\lab3\.env" }
$env:LAB4_HEARTBEAT_TIMEOUT = "30s"           # a killed worker is detected 30 s after its last heartbeat
$workerArgs = "-m topicflow.cli worker"
$results = Join-Path $lab "results"
$logs = Join-Path $lab "traces\processes"
if ($DryRun) {
    if (-not $DryRoot) { $DryRoot = Join-Path (Split-Path $lab -Qualifier) "tmp_lab4" }
    $env:TEMPORAL_NAMESPACE = $DryNamespace; $env:LAB4_ES_PREFIX = "itmo_lab4_dry"; $env:LAB4_ES_INDEX = "itmo_lab4_dry_excerpts"
    $env:LAB4_TASK_ROOT = Join-Path $DryRoot "workspace"
    $env:LAB4_TRACES_DIR = Join-Path $DryRoot "traces"; $env:LAB4_RESULTS_DIR = Join-Path $DryRoot "results"
    $results = $env:LAB4_RESULTS_DIR; $logs = Join-Path $env:LAB4_TRACES_DIR "processes"
    $workerArgs = "scripts\dry_run_worker.py"
    if (-not $PSBoundParameters.ContainsKey("Every")) { $Every = "1m" }
    $SkipPush = $true
    Write-Host "DRY RUN: fake LLM (test policies), namespace dryrun, folder $DryRoot" -ForegroundColor Yellow
}
New-Item -ItemType Directory -Force $results | Out-Null
New-Item -ItemType Directory -Force $logs | Out-Null

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function TF { & $py -m topicflow.cli @args | Out-Host; return $LASTEXITCODE }
function TF-Must { & $py -m topicflow.cli @args | Out-Host; if ($LASTEXITCODE -ne 0) { throw "topicflow $($args -join ' ') failed ($LASTEXITCODE)" } }
function Read-Env($name) {
    $line = Get-Content $envFile -Encoding UTF8 | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if (-not $line) { return "" }
    return $line.Substring($name.Length + 1).Trim()
}
function Wait-Port($port, $tries) {
    for ($i = 0; $i -lt $tries; $i++) {
        $c = New-Object Net.Sockets.TcpClient
        try { $c.Connect("localhost", $port); $c.Close(); return } catch { Start-Sleep -Seconds 2 }
    }
    throw "port $port did not open"
}
function Wait-Es($tries) {                     # the port opens before Elasticsearch can answer requests
    for ($i = 0; $i -lt $tries; $i++) {
        try {
            $h = Invoke-RestMethod -Uri "http://localhost:9200/_cluster/health" -TimeoutSec 5
            if ($h.status -in @("green", "yellow")) { Write-Host "Elasticsearch: $($h.status)"; return }
        } catch { }
        Start-Sleep -Seconds 3
    }
    throw "Elasticsearch did not become healthy"
}
function Assert-Crashed($p, $name) {             # the worker must die at the crash point, not for another reason
    if ($p.ExitCode -ne 137) {
        throw "$name exited with code $($p.ExitCode) instead of the simulated crash (137): see $(Join-Path $logs "$name.err.log")"
    }
}
function Start-Bg($name, $argline) {
    $p = Start-Process -FilePath $py -ArgumentList $argline -WorkingDirectory $lab -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $logs "$name.log") -RedirectStandardError (Join-Path $logs "$name.err.log")
    $null = $p.Handle                         # PowerShell 5.1 keeps ExitCode only if the handle was opened early
    $script:bg += $p
    Write-Host "started $name (pid $($p.Id)): python $argline"
    return $p
}
$script:bg = @()
function Stop-One($p) {                       # the venv python.exe is a launcher: stop its child process too
    Get-CimInstance Win32_Process -Filter "ParentProcessId=$($p.Id)" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
}
function Stop-Bg { foreach ($p in $script:bg) { Stop-One $p } }
trap { Write-Host "FAILED: $_" -ForegroundColor Red; Stop-Bg; exit 1 }
function Started-Id($out) { if ($out -match "started (\S+) run") { return $Matches[1] } throw "no workflow id in: $out" }

Step "0. Environment"
if (-not (Test-Path $py)) { throw "lab 3 venv not found: create it first (lab3 README), then pip install -r lab4\requirements.txt" }
& $py -c "import temporalio" 2>$null
if ($LASTEXITCODE -ne 0) { & $py -m pip install -r requirements.txt }
if (-not $DryRun) {
    $base = Read-Env "LLM_BASE_URL"; $key = Read-Env "LLM_API_KEY"
    try { Invoke-RestMethod -Uri "$base/models" -Headers @{ Authorization = "Bearer $key" } -TimeoutSec 10 | Out-Null }
    catch { throw "LLM server $base is not reachable: connect to the ITMO network and run again" }
    Write-Host "LLM server OK"
}

Step "1. Elasticsearch (lab 1 docker compose) and Temporal dev server (Docker)"
Push-Location ..\lab1; docker compose up -d; $rc = $LASTEXITCODE; Pop-Location
if ($rc -ne 0) { throw "docker compose up failed: is Docker Desktop running?" }
if (-not (docker ps -a --format "{{.Names}}" | Select-String -SimpleMatch "itmo-lab4-temporal")) {
    docker run -d --name itmo-lab4-temporal --user root -p 7233:7233 -p 8233:8233 -v itmo-lab4-temporal:/data $TemporalImage `
        server start-dev --ip 0.0.0.0 --db-filename /data/temporal.db | Out-Host
} else { docker start itmo-lab4-temporal | Out-Host }
Wait-Port 9200 60; Wait-Es 60; Wait-Port 7233 60
Start-Sleep -Seconds 3
if ($DryRun) { docker exec itmo-lab4-temporal temporal operator namespace create --namespace $DryNamespace 2>&1 | Out-Null }
Write-Host "Temporal UI: http://localhost:8233"

if (-not $SkipTests) {
    Step "2. Tests (deterministic stand-ins for the LLM; Temporal workflows on the real service)"
    & $py -m pytest -q | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }
}

if ($Part -eq "growth") {
    Step "W0. Corpus growth with a file on a NEW topic, reported by a Schedule tick (corpus of the main run, no reset)"
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "topicflow\.cli (worker|watch)|dry_run_worker" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    TF schedule delete | Out-Null
    TF-Must fault --clear
    $w7 = Start-Bg "worker7" $workerArgs
    $watch7 = Start-Bg "watcher7" "-m topicflow.cli watch"
    Start-Sleep -Seconds 20
    if ($w7.HasExited) { throw "worker7 ended at start: see $(Join-Path $logs 'worker7.err.log')" }

    Step "W1. The user drops a file on a new topic; the same detection event is repeated WHILE the workflow runs"
    TF-Must drop --set new_topic2
    $bid = $null
    for ($i = 0; $i -lt 60 -and -not $bid; $i++) {
        Start-Sleep -Seconds 2
        $bid = (& $py -c "import topicflow.config as c; from topicflow.registry import Registry; cfg=c.load(); b=[x for x in Registry(cfg.state_dir / 'lab4.db').batches() if x['status'] in ('started','running') and any('ui_accessibility' in f['input_path'] for f in x['files'])]; print(b[-1]['batch_id'] if b else '')" | Out-String).Trim()
    }
    if (-not $bid) { throw "the watcher did not start a workflow for the new file" }
    $replayOut = (& $py -m topicflow.cli replay --batch $bid | Out-String)
    $ns = if ($env:TEMPORAL_NAMESPACE) { $env:TEMPORAL_NAMESPACE } else { "default" }
    $listOut = (docker exec itmo-lab4-temporal temporal workflow list --namespace $ns --query "WorkflowId='ingest-$bid'" 2>&1 | Out-String)
    if ($replayOut -notmatch "already running") { Write-Host "WARNING: the workflow was not RUNNING at replay time (it finished first)" -ForegroundColor Yellow }
    "replay --batch $bid while the workflow is RUNNING:`r`n$replayOut`r`ntemporal workflow list --query WorkflowId='ingest-$bid':`r`n$listOut" |
        Out-File -Encoding utf8 (Join-Path $results "replay_while_running.txt")
    Get-Content (Join-Path $results "replay_while_running.txt")
    TF-Must wait --batches --timeout 1800

    Step "W2. Next report by the Schedule: the new document must appear as a NEW topic; earlier reports stay"
    $after = (Get-Date).ToUniversalTime().AddSeconds(-5).ToString("yyyy-MM-ddTHH:mm:ssZ")
    TF-Must schedule create --every 30m
    TF-Must wait --schedule-runs 1 --after $after --timeout 3600
    TF-Must schedule delete

    Step "W3. Traces and results"
    Stop-Bg
    TF-Must trace
    TF-Must export
    & $py -m topicflow.cli l0 | Out-File -Encoding utf8 (Join-Path $results "l0_view.json")
    & $py -m topicflow.cli open-report --latest | Out-File -Encoding utf8 (Join-Path $results "open_report_latest.txt")
    & $py -m topicflow.cli open-report --latest-complete | Out-File -Encoding utf8 (Join-Path $results "open_report_latest_complete.txt")
    & $py -m topicflow.cli status | Out-File -Encoding utf8 (Join-Path $results "status.txt")
    Get-Content (Join-Path $results "status.txt")
    if ($SkipPush) { Write-Host "`nDone (git skipped)."; exit 0 }
    Step "W4. Commit and push"
    git add traces results corpus
    git commit -m "Lab 4: new-topic file reported by a Schedule tick, replay while running (qwen3.8-27b)"
    if ($LASTEXITCODE -ne 0) { throw "git commit failed (nothing to commit?)" }
    git push
    if ($LASTEXITCODE -ne 0) { throw "git push failed" }
    Write-Host "`nDone."
    exit 0
}

if ($Part -eq "ticks") {
    # Real interval ticks of one Schedule (no immediate run): a file on a new topic arrives between tick 1 and
    # tick 2, the worker dies inside the topic agent of tick 2, and a file that arrives after the freeze of tick 2
    # appears only in tick 3. Corpus of the earlier runs, no reset.
    Step "K0. Workers and watcher"
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "topicflow\.cli (worker|watch)|dry_run_worker" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    TF schedule delete | Out-Null
    TF-Must fault --clear
    $w8 = Start-Bg "worker8" $workerArgs
    $watch8 = Start-Bg "watcher8" "-m topicflow.cli watch"
    Start-Sleep -Seconds 20
    if ($w8.HasExited) { throw "worker8 ended at start: see $(Join-Path $logs 'worker8.err.log')" }

    Step "K1. Schedule every $Every without the immediate run: run 1 is an interval tick"
    $after = (Get-Date).ToUniversalTime().AddSeconds(-5).ToString("yyyy-MM-ddTHH:mm:ssZ")
    TF-Must schedule create --every $Every --no-trigger
    TF-Must wait --schedule-runs 1 --after $after --timeout 3600
    & $py -m topicflow.cli schedule describe | Out-File -Encoding utf8 (Join-Path $results "schedule_ticks_after_tick1.json")

    Step "K2. A file on a new topic between tick 1 and tick 2 (ingested automatically by the watcher)"
    TF-Must drop --set new_topic3
    Start-Sleep -Seconds 10
    TF-Must wait --batches --timeout 1800

    Step "K3. Tick 2 on a worker that dies inside the topic agent; a file arrives after the freeze of tick 2"
    Stop-One $w8
    $env:HARNESS_CRASH_AT = "after_tool:6"
    $w9 = Start-Bg "worker9" $workerArgs
    Remove-Item Env:HARNESS_CRASH_AT
    Write-Host "waiting for tick 2 and for worker #9 to die inside its topic agent (after the 6th tool)..."
    $w9.WaitForExit(3600000) | Out-Null
    Assert-Crashed $w9 "worker9"
    TF-Must drop --set late2                  # the crash comes after freeze_corpus: tick 2 must not contain this file
    $w10 = Start-Bg "worker10" $workerArgs
    TF-Must wait --schedule-runs 2 --after $after --timeout 3600
    TF-Must wait --batches --timeout 1800

    Step "K4. Tick 3: the late file is a new document there"
    TF-Must wait --schedule-runs 3 --after $after --timeout 3600
    & $py -m topicflow.cli schedule describe | Out-File -Encoding utf8 (Join-Path $results "schedule_ticks_final.json")
    TF-Must schedule delete

    Step "K5. Traces and results"
    Stop-Bg
    TF-Must trace
    TF-Must export
    & $py -m topicflow.cli l0 | Out-File -Encoding utf8 (Join-Path $results "l0_view.json")
    & $py -m topicflow.cli open-report --latest | Out-File -Encoding utf8 (Join-Path $results "open_report_latest.txt")
    & $py -m topicflow.cli open-report --latest-complete | Out-File -Encoding utf8 (Join-Path $results "open_report_latest_complete.txt")
    & $py -m topicflow.cli status | Out-File -Encoding utf8 (Join-Path $results "status.txt")
    Get-Content (Join-Path $results "status.txt")
    if ($SkipPush) { Write-Host "`nDone (git skipped)."; exit 0 }
    Step "K6. Commit and push"
    git add traces results corpus
    git commit -m "Lab 4: three interval ticks of one Schedule, new topic between ticks, worker crash in a scheduled run, file after the freeze (qwen3.8-27b)"
    if ($LASTEXITCODE -ne 0) { throw "git commit failed (nothing to commit?)" }
    git push
    if ($LASTEXITCODE -ne 0) { throw "git push failed" }
    Write-Host "`nDone."
    exit 0
}

if ($Part -eq "topic-crash") {
    Step "T0. Worker killed DURING the topic-analysis agent (corpus of the main run, no reset)"
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "topicflow\.cli (worker|watch)|dry_run_worker" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    TF schedule delete | Out-Null
    TF-Must fault --clear
    $env:HARNESS_CRASH_AT = "after_tool:6"
    $w5 = Start-Bg "worker5" $workerArgs
    Remove-Item Env:HARNESS_CRASH_AT
    Start-Sleep -Seconds 20                                # worker loads the embedding model
    if ($w5.HasExited) { Assert-Crashed $w5 "worker5"; throw "worker5 ended before the topic agent started" }
    $wid = Started-Id ((& $py -m topicflow.cli topics-now) | Out-String)
    Write-Host "waiting for worker #5 to die inside the topic agent (simulated crash after the 6th tool)..."
    $w5.WaitForExit(1800000) | Out-Null
    Write-Host "worker #5 exit code: $($w5.ExitCode)"
    Assert-Crashed $w5 "worker5"
    $w6 = Start-Bg "worker6" $workerArgs
    TF wait --workflow-id $wid --timeout 3600 | Out-Null

    Step "T1. Traces and results"
    Stop-Bg
    TF-Must trace
    TF-Must export
    & $py -m topicflow.cli l0 | Out-File -Encoding utf8 (Join-Path $results "l0_view.json")
    & $py -m topicflow.cli open-report --latest | Out-File -Encoding utf8 (Join-Path $results "open_report_latest.txt")
    & $py -m topicflow.cli open-report --latest-complete | Out-File -Encoding utf8 (Join-Path $results "open_report_latest_complete.txt")
    & $py -m topicflow.cli status | Out-File -Encoding utf8 (Join-Path $results "status.txt")
    Get-Content (Join-Path $results "status.txt")
    if ($SkipPush) { Write-Host "`nDone (git skipped)."; exit 0 }
    Step "T2. Commit and push"
    git add traces results corpus
    git commit -m "Lab 4: worker crash inside the topic agent with qwen3.8-27b (resumed harness task)"
    if ($LASTEXITCODE -ne 0) { throw "git commit failed (nothing to commit?)" }
    git push
    if ($LASTEXITCODE -ne 0) { throw "git push failed" }
    Write-Host "`nDone."
    exit 0
}

if ($Part -eq "extra") {
    Step "E0. Extra scenarios on the corpus of the main run (no reset)"
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "topicflow\.cli (worker|watch)|dry_run_worker" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    TF schedule delete | Out-Null
    TF-Must fault --clear
    TF-Must status

    Step "E1. Worker #3 will be killed DURING the topic-analysis agent of a scheduled run"
    $env:HARNESS_CRASH_AT = "after_tool:6"
    $w3 = Start-Bg "worker3" $workerArgs
    Remove-Item Env:HARNESS_CRASH_AT
    Start-Sleep -Seconds 20                                # worker loads the embedding model
    if ($w3.HasExited) { Assert-Crashed $w3 "worker3"; throw "worker3 ended before the topic agent started" }
    $after = (Get-Date).ToUniversalTime().AddSeconds(-5).ToString("yyyy-MM-ddTHH:mm:ssZ")
    TF-Must schedule create --every 30m                   # first run immediately
    Write-Host "waiting for worker #3 to die inside the topic agent (simulated crash after the 6th tool)..."
    $w3.WaitForExit(1800000) | Out-Null
    Write-Host "worker #3 exit code: $($w3.ExitCode)"
    Assert-Crashed $w3 "worker3"

    Step "E2. Overlap: trigger the schedule while its run is unfinished -> skipped (policy SKIP)"
    TF-Must schedule trigger
    Start-Sleep -Seconds 3
    & $py -m topicflow.cli schedule describe | Out-File -Encoding utf8 (Join-Path $results "schedule_overlap.json")
    Get-Content (Join-Path $results "schedule_overlap.json")
    $w4 = Start-Bg "worker4" $workerArgs
    TF-Must wait --schedule-runs 1 --after $after --timeout 3600
    & $py -m topicflow.cli schedule describe | Out-File -Encoding utf8 (Join-Path $results "schedule_after_overlap.json")
    Get-Content (Join-Path $results "schedule_after_overlap.json")
    TF-Must schedule delete

    Step "E3. Rescan after the detector restart: a file added while no watcher runs"
    TF-Must drop --set late
    Start-Sleep -Seconds 5
    $watch3 = Start-Bg "watcher3" "-m topicflow.cli watch"
    Start-Sleep -Seconds 10
    TF-Must wait --batches --timeout 1800
    TF-Must status

    Step "E4. Traces and results"
    Stop-Bg
    TF-Must trace
    TF-Must export
    & $py -m topicflow.cli l0 | Out-File -Encoding utf8 (Join-Path $results "l0_view.json")
    & $py -m topicflow.cli open-report --latest | Out-File -Encoding utf8 (Join-Path $results "open_report_latest.txt")
    & $py -m topicflow.cli open-report --latest-complete | Out-File -Encoding utf8 (Join-Path $results "open_report_latest_complete.txt")
    & $py -m topicflow.cli status | Out-File -Encoding utf8 (Join-Path $results "status.txt")
    Get-Content (Join-Path $results "status.txt")
    if ($SkipPush) { Write-Host "`nDone (git skipped)."; exit 0 }
    Step "E5. Commit and push"
    git add traces results corpus
    git commit -m "Lab 4: extra scenarios with qwen3.8-27b (worker crash in the topic agent, overlap SKIP, rescan)"
    if ($LASTEXITCODE -ne 0) { throw "git commit failed (nothing to commit?)" }
    git push
    if ($LASTEXITCODE -ne 0) { throw "git push failed" }
    Write-Host "`nDone."
    exit 0
}

Step "3. Fresh workspace, indices, no schedule, no faults"
# workers/watchers left over from an interrupted earlier run of this script
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match "topicflow\.cli (worker|watch)|dry_run_worker" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "stopped stale process $($_.ProcessId)" }
TF schedule delete | Out-Null
TF-Must prepare --reset
TF-Must fault --clear

Step "4. Worker #1 (will be killed during the ingestion agent) and the watcher"
$env:HARNESS_CRASH_AT = "after_tool:10"
$w1 = Start-Bg "worker1" $workerArgs
Remove-Item Env:HARNESS_CRASH_AT
$watch = Start-Bg "watcher1" "-m topicflow.cli watch"
Start-Sleep -Seconds 20                                    # worker loads the embedding model

Step "5. Scenario 1: the user drops 10 files into input (no CLI indexing command)"
TF-Must drop --set initial
Write-Host "waiting for worker #1 to die inside the ingestion agent (simulated crash after the 10th tool)..."
$w1.WaitForExit(1800000) | Out-Null
Write-Host "worker #1 exit code: $($w1.ExitCode)"
Assert-Crashed $w1 "worker1"
$w2 = Start-Bg "worker2" $workerArgs
TF-Must wait --batches --timeout 3600
TF-Must status

Step "6. Scenario 2: duplicate events"
$first = (& $py -c "import topicflow.config as c; from topicflow.registry import Registry; cfg=c.load(); print(Registry(cfg.state_dir / 'lab4.db').batches()[0]['batch_id'])").Trim()
TF-Must replay --batch $first
TF-Must drop --set initial --repeat --only oncall.md
Start-Sleep -Seconds 10
TF-Must wait --batches --timeout 900
TF-Must status

Step "7. Scenario 3: Temporal Schedule every $Every (overlap SKIP), first run immediately"
TF-Must schedule create --every $Every
TF-Must wait --schedule-runs 1 --timeout 3600
TF-Must l0

Step "8. Scenario 4: corpus growth between two scheduled runs"
TF-Must schedule pause
TF-Must drop --set arrivals
TF-Must drop --set new_topic
Start-Sleep -Seconds 10
TF-Must wait --batches --timeout 3600
$after = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")   # the next report must be frozen after the ingestion
TF-Must schedule unpause
TF-Must wait --schedule-runs 1 --after $after --timeout 3600
TF-Must schedule describe
TF-Must schedule pause                                    # no more periodic runs during the failure scenarios

Step "9. Scenario 5a: publish_report fails after writing the file -> retry, one report"
TF-Must fault --activity publish_report --point after_write --times 1
$wid = Started-Id ((& $py -m topicflow.cli topics-now) | Out-String)
TF wait --workflow-id $wid --timeout 3600 | Out-Null

Step "10. Scenario 5b: budget exhaustion of the topic agent -> partial report"
$wid2 = Started-Id ((& $py -m topicflow.cli topics-now --set max_model_calls=4) | Out-String)
TF wait --workflow-id $wid2 --timeout 3600 | Out-Null

Step "11. Scenario 5c: stage_batch always fails -> bounded retries, file stays in input; watcher restart recovers"
TF-Must fault --activity stage_batch --times always
TF-Must drop --set initial --repeat --only bm25_basics.md
Start-Sleep -Seconds 60                                    # 5 attempts with backoff, then the workflow fails
TF-Must status
TF-Must fault --clear
Stop-One $watch
$watch2 = Start-Bg "watcher2" "-m topicflow.cli watch"
Start-Sleep -Seconds 10
TF-Must wait --batches --timeout 900
TF-Must status

Step "12. Stop the schedule, workers and watcher; traces and results"
TF-Must schedule describe
TF-Must schedule delete
Stop-Bg
TF-Must trace
TF-Must export
& $py -m topicflow.cli l0 | Out-File -Encoding utf8 (Join-Path $results "l0_view.json")
& $py -m topicflow.cli open-report --latest | Out-File -Encoding utf8 (Join-Path $results "open_report_latest.txt")
& $py -m topicflow.cli status | Out-File -Encoding utf8 (Join-Path $results "status.txt")
Get-Content (Join-Path $results "status.txt")

if ($SkipPush) { Write-Host "`nDone (git skipped)."; exit 0 }
Step "13. Commit and push"
git add traces results corpus
git commit -m "Lab 4: Temporal ingestion and scheduled topic reports with qwen3.8-27b (traces, reports, L0)"
if ($LASTEXITCODE -ne 0) { throw "git commit failed (nothing to commit?)" }
git push
if ($LASTEXITCODE -ne 0) { throw "git push failed" }
Write-Host "`nDone."
