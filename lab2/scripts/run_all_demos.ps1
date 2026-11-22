<#
Records all lab 2 traces with the real ITMO model and publishes the results.
Run INSIDE the ITMO network (the LLM server is not reachable outside), Docker Desktop running:

    powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1              # everything
    powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1 -Part web    # only web scenarios B, C
    powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1 -Part guard  # only the guardrail dialog G1
    powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1 -Part b2     # only the real web research B2
    ... -SkipPush -SkipTests

Scenarios (task ids):
  A1     long local analysis of data/search (520-page handbook): simulated crash after a durable checkpoint
         (HARNESS_CRASH_AT=after_tool:30), then resume in a NEW process until completed
  A_base the lab 1 agent (no harness) on task A (recorded 16.11 with the older request: no ES/MCP part, 40 calls)
  D1     tiny token budget -> paused with a partial result; user raises the limit; resume keeps the spent counters
  T1     LLM timeout 1 s -> finite retries -> paused
  E1     cancel from another process while the task is running
  B1     first real web attempt (kept as a record): general search engines unreachable from the ITMO network
  B2     real web research on a topic without URLs: multi-engine search (ddgs + public search APIs), >= 5 pages
  C1     web load test on the fixed search results (>= 50 URLs) with a small HTTP budget
  G1     guardrail dialog: task root escapes, junction, service area, injection page, big outputs, HTTP timeout/retries
#>
param(
    [ValidateSet("all", "web", "guard", "b2")][string]$Part = "all",
    [switch]$SkipPush,
    [switch]$Push,
    [switch]$SkipTests,
    [string]$EnvFile = ".env"
)

$ErrorActionPreference = "Stop"
# git commit + push of the results only on request (-Push); without it the script just records them
if (-not $Push) { $SkipPush = $true }
$lab = Split-Path -Parent $PSScriptRoot
Set-Location $lab
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$py = Join-Path $lab ".venv\Scripts\python.exe"
$EnvFile = Join-Path $lab $EnvFile
$env:HARNESS_ENV_FILE = $EnvFile      # every python step reads the same settings file

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Run-Py { & $py @args | Out-Host; if ($LASTEXITCODE -ne 0) { throw "python $($args -join ' ') failed with exit code $LASTEXITCODE" } }
function Harness { & $py -m harness.cli @args | Out-Host; return $LASTEXITCODE }
function Task-Status($id) { return (& $py -m harness.cli status --task-id $id --json | Out-String | ConvertFrom-Json) }
function Read-Env($name) {
    $line = Get-Content $EnvFile -Encoding UTF8 | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if (-not $line) { return "" }
    return $line.Substring($name.Length + 1).Trim()
}
function Wait-Url($url, $tries) {
    for ($i = 0; $i -lt $tries; $i++) {
        try { Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3 | Out-Null; return } catch { Start-Sleep -Seconds 2 }
    }
    throw "$url did not become ready"
}
function Resume-Until-Done($id, $max) {
    for ($i = 0; $i -lt $max; $i++) {
        $s = Task-Status $id
        if ($s.status -in @("completed", "cancelled")) { return }
        if ($s.reason -and ($s.reason -like "budget*" -or $s.reason -like "partial*")) { return }
        Harness resume --task-id $id | Out-Null
    }
}

Step "0. Python environment"
if (-not (Test-Path $py)) {
    python -m venv .venv
    & $py -m pip install -r requirements.txt
}
if (-not (Test-Path $EnvFile)) { throw "$EnvFile not found: copy .env.example to .env and set LLM_API_KEY" }

Step "1. ITMO LLM reachability and the real context window"
$base = Read-Env "LLM_BASE_URL"
$key = Read-Env "LLM_API_KEY"
try {
    $models = Invoke-RestMethod -Uri "$base/models" -Headers @{ Authorization = "Bearer $key" } -TimeoutSec 10
} catch {
    throw "LLM server $base is not reachable. Connect to the ITMO network (or VPN) and run again. Details: $($_.Exception.Message)"
}
$m = $models.data | Select-Object -First 1
Write-Host ("LLM server OK, model: " + $m.id)
# real window: GET /models fields -> POST /tokenize -> rejection message -> measurement from accept/reject
Write-Host "detecting the context window (first time may take a few minutes; cached in .cache\window.json)"
$wj = (& $py -m harness.cli window | Out-String | ConvertFrom-Json)
Write-Host ("context window: " + $wj.window + "  (source: " + $wj.source + ")")
if (-not $wj.window) { throw "The server did not reveal the context window: see the source line above; LLM_CONTEXT_WINDOW in .env is the last resort" }
$win = [int]$wj.window
$work = 28000
if ([int]$win -lt 32000) { $work = [int]([int]$win * 0.8) }
$fit = "working_context_tokens=$work"
Write-Host "working context limit for all tasks: $work tokens (real window $win)"

Step "2. Elasticsearch (lab 1 docker compose)"
Push-Location ..\lab1
docker compose up -d
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "docker compose up failed: is Docker Desktop running?" }
Pop-Location
Wait-Url "http://localhost:9200/_cluster/health" 60

Step "3. Fixture web server (fixed search results, HTTP cases)"
$fx = Start-Process -FilePath $py -ArgumentList "scripts\fixture_web.py", "--port", "8765" -PassThru -WindowStyle Hidden
try {
    Wait-Url "http://127.0.0.1:8765/p/checkpoint-1" 20

    if (-not $SkipTests) {
        Step "4. Offline tests"
        & $py -m pytest -q | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "tests failed" }
    }

    if ($Part -eq "all") {
        Step "5. Fresh task root and HTTP examples"
        Run-Py scripts\prepare_workspace.py --reset
        Run-Py scripts\http_examples.py

        Step "6. A1: long local analysis, simulated crash after a durable checkpoint"
        Harness new --spec demo\task_a_local.json --set $fit | Out-Null
        $env:HARNESS_CRASH_AT = "after_tool:30"
        $code = Harness run --task-id A1
        Remove-Item Env:HARNESS_CRASH_AT
        Write-Host "run exit code: $code (137 = process killed right after a tool, before the next checkpoint)"
        Harness status --task-id A1 | Out-Null

        Step "7. A1: resume in a NEW process (same task_id, no context re-entry)"
        Resume-Until-Done "A1" 3
        Harness status --task-id A1 | Out-Null
        Run-Py scripts\artifact_after_restart.py --task-id A1

        Step "8. A_base: the lab 1 agent without harness on task A"
        Harness baseline --spec demo\task_a_local.json --task-id A_base --max-model-calls 40 | Out-Null

        Step "9. D1: token budget exhausted -> partial; user raises the limit; resume keeps spent counters"
        Harness new --spec demo\task_d_budget.json --set $fit | Out-Null
        Harness run --task-id D1 | Out-Null
        Harness status --task-id D1 | Out-Null
        Harness limits --task-id D1 --set max_total_tokens=1500000 | Out-Null
        Harness resume --task-id D1 | Out-Null
        Harness status --task-id D1 | Out-Null

        Step "10. T1: LLM timeout 1 s -> finite retries -> paused"
        Harness new --spec demo\task_t_timeout.json --set $fit | Out-Null
        Harness run --task-id T1 | Out-Null

        Step "11. E1: cancel from another process while running"
        Harness new --spec demo\task_e_cancel.json --set $fit | Out-Null
        New-Item -ItemType Directory -Force results | Out-Null
        $p = Start-Process -FilePath $py -ArgumentList "-m", "harness.cli", "run", "--task-id", "E1" -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput "results\E1_stdout.txt" -RedirectStandardError "results\E1_stderr.txt"
        for ($i = 0; $i -lt 300; $i++) {
            Start-Sleep -Seconds 2
            $s = Task-Status "E1"
            if ($s.budget.model_calls.used -ge 5 -or $p.HasExited) { break }
        }
        Harness cancel --task-id E1 --reason "demo: cancel from another terminal" | Out-Null
        $p.WaitForExit(180000) | Out-Null
        Harness status --task-id E1 | Out-Null
    }

    if ($Part -in @("all", "web", "b2")) {
        if (-not (Test-Path workspace\catalog.json)) { Run-Py scripts\prepare_workspace.py }
        Step "12. B2: real web research (topic only, no URLs), multi-engine search"
        Harness new --spec demo\task_b2_web.json --set $fit | Out-Null
        Harness run --task-id B2 | Out-Null
        Resume-Until-Done "B2" 2
        Harness status --task-id B2 | Out-Null
    }
    if ($Part -in @("all", "web")) {

        Step "13. C1: web load test on fixed search results (>= 50 URLs), small HTTP budget"
        Harness new --spec demo\task_c_load.json --set $fit | Out-Null
        Harness run --task-id C1 | Out-Null
        Harness status --task-id C1 | Out-Null
    }

    if ($Part -in @("all", "guard")) {
        if (-not (Test-Path workspace\catalog.json)) { Run-Py scripts\prepare_workspace.py }
        Step "14. G1: guardrail dialog (junction outside the task root)"
        $outside = Join-Path $env:TEMP "itmo_lab2_outside"
        New-Item -ItemType Directory -Force $outside | Out-Null
        Set-Content -Path (Join-Path $outside "secret.txt") -Value "outside-the-task-root secret" -Encoding UTF8
        $link = Join-Path $lab "workspace\data\link_out"
        if (-not (Test-Path $link)) { New-Item -ItemType Junction -Path $link -Target $outside | Out-Null }
        try {
            Harness new --spec demo\task_g_guard.json --set $fit | Out-Null
            Harness chat --task-id G1 --script demo\dialog_guard_ru.txt | Out-Null
        } finally { cmd /c rmdir "$link" | Out-Null }
        if (-not (Test-Path (Join-Path $outside "secret.txt"))) { throw "outside file disappeared: guard failed" }
        if (Test-Path (Join-Path $lab "stolen.txt")) { throw "a file was written outside the task root" }
    }
} finally {
    Stop-Process -Id $fx.Id -Force -ErrorAction SilentlyContinue
}

Step "15. Evaluation tables"
Run-Py scripts\evaluate.py

Step "16. Secret scan"
$leaks = Get-ChildItem traces, results -Recurse -File | Select-String -SimpleMatch -Pattern $key -List
if ($leaks) { throw "The LLM token was found in: $($leaks.Path -join ', '). Nothing was committed." }
Write-Host "No token in traces/ and results/."

if ($SkipPush) { Write-Host "`nDone (git skipped)."; exit 0 }

Step "17. Commit and push"
git add traces results demo corpus fixtures
git commit -m "Lab 2: harness traces with qwen3.8-27b, results and evaluation ($Part)"
if ($LASTEXITCODE -ne 0) { throw "git commit failed (nothing to commit?)" }
git push
if ($LASTEXITCODE -ne 0) { throw "git push failed" }
Write-Host "`nDone. Traces: $lab\traces, results: $lab\results" -ForegroundColor Green
