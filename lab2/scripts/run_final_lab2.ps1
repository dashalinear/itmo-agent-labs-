<#
Re-records ONLY the lab 2 scenarios changed after the audit, with the real ITMO model, then commits
lab2/traces, lab2/results and the regenerated README blocks, and pushes.
Run INSIDE the ITMO network (the LLM server is not reachable outside), Docker Desktop running:

    powershell -ExecutionPolicy Bypass -File scripts\run_final_lab2.ps1                 # everything (about 30-40 min)
    powershell -ExecutionPolicy Bypass -File scripts\run_final_lab2.ps1 -SkipB3         # without the real web run B3
    powershell -ExecutionPolicy Bypass -File scripts\run_final_lab2.ps1 -Only A1,G2,P1,B3   # re-record only these task ids
    powershell -ExecutionPolicy Bypass -File scripts\run_final_lab2.ps1 -SkipPush       # record, do not commit
    powershell -ExecutionPolicy Bypass -File scripts\run_final_lab2.ps1 -DryRun -WorkDir D:\tmp\lab2_dry
        offline check of this script: scripted models (scripts/dryrun_models.py) instead of the LLM, all state,
        traces and results in -WorkDir, no git, README untouched

Scenarios (task ids):
  P1   CLI pause from a second process after the 1st evidence -> paused at a step boundary; resume in a new process
  A1   long local analysis; crash right after the 3rd saved evidence (after_tool:save_evidence:3) + resume in a
       NEW process; completion requires [C5] verbatim decisions (verbatim_quotes) and the three decisions indexed
       in Elasticsearch through the lab 1 MCP tools (indexed_in_es); artifact read after restart
  D1   token budget 120k -> paused + partial; the user raises the limit; resume keeps the counters (same [C5] check)
  DL1  task deadline 45 s -> guard=deadline, paused, partial result saved
  E1   cancel from another process AFTER 2-3 evidence are saved; the in-flight reservation is released
  I1   web research on the fixed search results with three crash points in three processes:
         after_download:2       (page downloaded, not stored: URL left 'running' -> re-queued)
         after_tool:web_fetch:1 (page stored, step checkpoint missing -> re-executed as a cache hit, no HTTP)
         after_tool:save_evidence:1 (evidence stored -> re-executed as already_saved)
       then resume until done; scripts/idempotency_report.py -> results/I1/idempotency.json
  C2   fixed search results with max_http_requests = 6 -> http_budget_exhausted, pending URLs kept
  G2   guard dialog 2: write/move by absolute path, move into/out of a junction, allowed write/move,
       four parallel read_pages in one step -> "step total would exceed"
  B3   real web research (topic only) with a second round: refined queries after reading, a link followed
       from a fetched page, at most 60 % of the read pages from one domain (skip with -SkipB3)
#>
param(
    [switch]$DryRun,
    [switch]$SkipPush,
    [switch]$Push,
    [switch]$SkipTests,
    [switch]$SkipB3,
    [string[]]$Only = @(),
    [string]$WorkDir = "",
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
$env:HARNESS_ENV_FILE = $EnvFile
$t0 = Get-Date

function Step($text) { Write-Host ("`n=== " + $text + "  [" + [int]((Get-Date) - $t0).TotalMinutes + " min] ===") -ForegroundColor Cyan }
function Run-Py { & $py @args | Out-Host; if ($LASTEXITCODE -ne 0) { throw "python $($args -join ' ') failed with exit code $LASTEXITCODE" } }
function Harness { & $py -m harness.cli @args | Out-Host; return $LASTEXITCODE }
function Task-Status($id) { return (& $py -m harness.cli status --task-id $id --json | Out-String | ConvertFrom-Json) }
function Read-Env($name) {
    if (-not (Test-Path $EnvFile)) { return "" }
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
        if ($s.reason -and ($s.reason -like "budget*" -or $s.reason -like "partial*" -or $s.reason -like "*deadline*")) { return }
        Harness resume --task-id $id | Out-Null
    }
}
# "-Only B3" or "-Only G2,I1" (powershell -File passes a comma list as one string)
$Only = @($Only | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
function Want($id) { return ((-not $Only) -or ($Only -contains $id)) }
function Run-With-Crash($id, $crash, $cmd) {
    $env:HARNESS_CRASH_AT = $crash
    try { $code = Harness $cmd --task-id $id } finally { Remove-Item Env:HARNESS_CRASH_AT -ErrorAction SilentlyContinue }
    Write-Host "$id $cmd with HARNESS_CRASH_AT=$crash -> exit code $code (137 = killed at the crash point)"
}

# ------------------------------------------------------------------ where everything goes
if ($DryRun) {
    if (-not $WorkDir) { $WorkDir = Join-Path $env:TEMP "lab2_final_dryrun" }
    if (Test-Path $WorkDir) { Remove-Item -Recurse -Force $WorkDir }
    New-Item -ItemType Directory -Force $WorkDir | Out-Null
    $env:TASK_ROOT = Join-Path $WorkDir "workspace"
    $env:RESULTS_DIR = Join-Path $WorkDir "results"
    $env:TRACES_DIR = Join-Path $WorkDir "traces"
    $env:HARNESS_SCRIPTED_MODEL = Join-Path $lab "scripts\dryrun_models.py"
    $env:LLM_CONTEXT_WINDOW = "262144"
    $env:TOKEN_COUNT_MODE = "heuristic"
    $outside = Join-Path $WorkDir "outside_area"
    $SkipB3 = $true
    $SkipPush = $true
    $A1Crash = "after_tool:save_evidence:3"
    $env:ES_INDEX = "itmo_lab2_dryrun_excerpts"   # dry run never writes into the recorded lab 2 index
    Write-Host "DRY RUN: scripted models, work dir $WorkDir (nothing is committed)" -ForegroundColor Yellow
} else {
    $outside = Join-Path $lab "outside_area"
    $A1Crash = "after_tool:save_evidence:3"      # crash right after the 3rd saved evidence: resume shows them restored
}
$root = if ($env:TASK_ROOT) { $env:TASK_ROOT } else { Join-Path $lab "workspace" }
$resultsDir = if ($env:RESULTS_DIR) { $env:RESULTS_DIR } else { Join-Path $lab "results" }
$tracesDir = if ($env:TRACES_DIR) { $env:TRACES_DIR } else { Join-Path $lab "traces" }
$fit = "working_context_tokens=28000"
$key = Read-Env "LLM_API_KEY"

Step "0. Python environment"
if (-not (Test-Path $py)) {
    python -m venv .venv
    & $py -m pip install -r requirements.txt
}

if (-not $DryRun) {
    if (-not (Test-Path $EnvFile)) { throw "$EnvFile not found: copy .env.example to .env and set LLM_API_KEY" }
    Step "1. ITMO LLM reachability and the real context window"
    $base = Read-Env "LLM_BASE_URL"
    try {
        $models = Invoke-RestMethod -Uri "$base/models" -Headers @{ Authorization = "Bearer $key" } -TimeoutSec 10
    } catch {
        throw "LLM server $base is not reachable. Connect to the ITMO network (or VPN) and run again. Details: $($_.Exception.Message)"
    }
    Write-Host ("LLM server OK, model: " + ($models.data | Select-Object -First 1).id)
    $wj = (& $py -m harness.cli window | Out-String | ConvertFrom-Json)
    Write-Host ("context window: " + $wj.window + "  (source: " + $wj.source + ")")
    if (-not $wj.window) { throw "The server did not reveal the context window; LLM_CONTEXT_WINDOW in .env is the last resort" }
    if ([int]$wj.window -lt 32000) { $fit = "working_context_tokens=" + [int]([int]$wj.window * 0.8) }
    Write-Host "working limit: $fit"

    Step "2. Elasticsearch (lab 1 docker compose)"
    Push-Location ..\lab1
    docker compose up -d
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw "docker compose up failed: is Docker Desktop running?" }
    Pop-Location
    Wait-Url "http://localhost:9200/_cluster/health" 60
}

Step "3. Fixture web server (fixed search results, HTTP cases)"
$fx = Start-Process -FilePath $py -ArgumentList "scripts\fixture_web.py", "--port", "8765" -PassThru -WindowStyle Hidden
try {
    Wait-Url "http://127.0.0.1:8765/p/checkpoint-1" 20

    if (-not $SkipTests -and -not $DryRun) {
        Step "4. Offline tests"
        & $py -m pytest -q | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "tests failed" }
    }

    Step "5. Fresh task root; old traces/results of the re-recorded tasks are replaced"
    Run-Py scripts\prepare_workspace.py --reset
    $ids = @("A1", "D1", "DL1", "E1", "P1", "I1", "C2", "G2")
    if (-not $SkipB3) { $ids += "B3"; $ids += "B4" }
    foreach ($extra in @("A_base", "T1", "C1", "G1")) { if ($Only -contains $extra) { $ids += $extra } }
    $ids = @($ids | Where-Object { Want $_ })
    Write-Host ("re-recorded task ids: " + ($ids -join ", "))
    foreach ($id in $ids) {
        Get-ChildItem $tracesDir -Filter "$($id)_*" -ErrorAction SilentlyContinue | Remove-Item -Force
        if (Test-Path (Join-Path $resultsDir $id)) { Remove-Item -Recurse -Force (Join-Path $resultsDir $id) }
    }
    if (Want "E1") { Remove-Item (Join-Path $resultsDir "E1_std*.txt") -ErrorAction SilentlyContinue }
    if (Want "P1") { Remove-Item (Join-Path $resultsDir "P1_std*.txt") -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Force $resultsDir | Out-Null

    if (Want "A1") {
        Step "6. A1: long analysis, crash after a durable checkpoint, resume in a NEW process, [C5] checked by code"
        Harness new --spec demo\task_a_local.json --set $fit | Out-Null
        Run-With-Crash "A1" $A1Crash "run"
        Resume-Until-Done "A1" 3
        Harness status --task-id A1 | Out-Null
        Run-Py scripts\artifact_after_restart.py --task-id A1
    }

    if (Want "D1") {
        Step "7. D1: token budget -> partial; user raises the limit; resume keeps the counters"
        Harness new --spec demo\task_d_budget.json --set $fit | Out-Null
        Harness run --task-id D1 | Out-Null
        Harness limits --task-id D1 --set max_total_tokens=1500000 | Out-Null
        Harness resume --task-id D1 | Out-Null
        Resume-Until-Done "D1" 2
        Harness status --task-id D1 | Out-Null
    }

    if (Want "DL1") {
        Step "8. DL1: task deadline 45 s -> paused with guard=deadline and a partial result"
        Harness new --spec demo\task_dl_deadline.json --set $fit | Out-Null
        Harness run --task-id DL1 | Out-Null
        Harness status --task-id DL1 | Out-Null
    }

    if (Want "E1") {
        Step "9. E1: cancel from another process after 2-3 evidence are saved"
        Harness new --spec demo\task_e_cancel.json --set $fit | Out-Null
        $p = Start-Process -FilePath $py -ArgumentList "-m", "harness.cli", "run", "--task-id", "E1" -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $resultsDir "E1_stdout.txt") -RedirectStandardError (Join-Path $resultsDir "E1_stderr.txt")
        # a second process watches the state DB and sends the cancel right after the 2nd evidence is saved
        & $py scripts\cancel_when.py --task-id E1 --min-evidence 2 --timeout 900 | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Host "E1: threshold not reached, cancelling now" -ForegroundColor Yellow
            Harness cancel --task-id E1 --reason "demo: cancel from another terminal" | Out-Null
        }
        $p.WaitForExit(240000) | Out-Null
        Harness status --task-id E1 | Out-Null
    }

    if (Want "I1") {
        Step "10. I1: crash windows on the fixed web corpus, resume in new processes, idempotency evidence"
        Harness new --spec demo\task_i_idem.json --set $fit | Out-Null
        Run-With-Crash "I1" "after_download:2" "run"
        Run-With-Crash "I1" "after_tool:web_fetch:1" "resume"
        Run-With-Crash "I1" "after_tool:save_evidence:1" "resume"
        Resume-Until-Done "I1" 3
        Harness status --task-id I1 | Out-Null
        Run-Py scripts\idempotency_report.py --task-id I1
    }

    if (Want "P1") {
        Step "9b. P1: CLI pause from a second process, status paused, resume in a NEW process"
        Harness new --spec demo\task_p_pause.json --set $fit | Out-Null
        $pp = Start-Process -FilePath $py -ArgumentList "-m", "harness.cli", "run", "--task-id", "P1" -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $resultsDir "P1_stdout.txt") -RedirectStandardError (Join-Path $resultsDir "P1_stderr.txt")
        & $py scripts\cancel_when.py --task-id P1 --min-evidence 1 --action wait --timeout 900 | Out-Host
        Harness pause --task-id P1 --reason "demo: pause from another terminal" | Out-Null
        $pp.WaitForExit(300000) | Out-Null
        Write-Host "P1 after pause:"
        Harness status --task-id P1 | Out-Null
        Harness resume --task-id P1 | Out-Null
        Harness status --task-id P1 | Out-Null
    }

    if (Want "C2") {
        Step "11. C2: fixed search results with max_http_requests = 6"
        Harness new --spec demo\task_c2_http.json --set $fit | Out-Null
        Harness run --task-id C2 | Out-Null
        Harness status --task-id C2 | Out-Null
    }

    if (Want "G2") {
        Step "12. G2: guard dialog 2 (absolute paths, junction moves, per-step output limit)"
        New-Item -ItemType Directory -Force $outside | Out-Null
        Set-Content -Path (Join-Path $outside "secret.txt") -Value "outside-the-task-root secret" -Encoding UTF8
        foreach ($f in "abs_write.txt", "moved_abs.md", "moved_junction.md", "written_via_junction.txt") { Remove-Item (Join-Path $outside $f) -ErrorAction SilentlyContinue }
        $link = Join-Path $root "data\link_out"
        if (-not (Test-Path $link)) { New-Item -ItemType Junction -Path $link -Target $outside | Out-Null }
        $outsideFwd = $outside.Replace("\", "/")
        $env:HARNESS_OUTSIDE_DIR = $outsideFwd
        $dialog = Join-Path $env:TEMP "itmo_lab2_dialog_guard2.txt"
        $lines = Get-Content demo\dialog_guard2_ru.txt -Encoding UTF8 | ForEach-Object { $_.Replace("{OUTSIDE}", $outsideFwd) }
        [System.IO.File]::WriteAllLines($dialog, [string[]]$lines, (New-Object System.Text.UTF8Encoding $false))
        try {
            Harness new --spec demo\task_g2_guard.json --set $fit | Out-Null
            Harness chat --task-id G2 --script $dialog | Out-Null
            Harness export --task-id G2 | Out-Null
        } finally { cmd /c rmdir "$link" | Out-Null; Remove-Item $dialog -ErrorAction SilentlyContinue }
        $bad = @()
        if (-not (Test-Path (Join-Path $outside "secret.txt"))) { $bad += "secret.txt left the outside folder" }
        foreach ($f in "abs_write.txt", "moved_abs.md", "moved_junction.md", "written_via_junction.txt") { if (Test-Path (Join-Path $outside $f)) { $bad += "$f written outside" } }
        if (-not (Test-Path (Join-Path $root "data\search\search_incident_reports.md"))) { $bad += "incident report was moved" }
        if (Test-Path (Join-Path $root "data\secret_copy.txt")) { $bad += "outside file copied into the root" }
        if ($bad) { throw ("guard failed: " + ($bad -join "; ")) }
        Write-Host "G2: no forbidden effect on disk (outside folder and documents intact)"
        Run-Py scripts\artifact_after_restart.py --task-id G2
    }

    # the scenarios first recorded on 16.11 with older code, re-recorded with the current one on request
    # (-Only A_base,T1,C1,G1); A_base now gets the same task A and the same 90 model calls as A1
    if ($Only -contains "A_base") {
        Step "12a. A_base: the lab 1 agent without harness on the same task A and the same call limit as A1"
        Harness baseline --spec demo\task_a_local.json --task-id A_base --max-model-calls 90 | Out-Null
    }
    if ($Only -contains "T1") {
        Step "12b. T1: LLM timeout 1 s -> finite retries -> paused"
        Harness new --spec demo\task_t_timeout.json --set $fit | Out-Null
        Harness run --task-id T1 | Out-Null
    }
    if ($Only -contains "C1") {
        Step "12c. C1: web load test on fixed search results (>= 50 URLs), small HTTP budget"
        Harness new --spec demo\task_c_load.json --set $fit | Out-Null
        Harness run --task-id C1 | Out-Null
        Harness status --task-id C1 | Out-Null
    }
    if ($Only -contains "G1") {
        Step "12d. G1: guardrail dialog (junction outside the task root)"
        $outside1 = Join-Path $env:TEMP "itmo_lab2_outside"
        New-Item -ItemType Directory -Force $outside1 | Out-Null
        Set-Content -Path (Join-Path $outside1 "secret.txt") -Value "outside-the-task-root secret" -Encoding UTF8
        $link1 = Join-Path $root "data\link_out"
        if (-not (Test-Path $link1)) { New-Item -ItemType Junction -Path $link1 -Target $outside1 | Out-Null }
        try {
            Harness new --spec demo\task_g_guard.json --set $fit | Out-Null
            Harness chat --task-id G1 --script demo\dialog_guard_ru.txt | Out-Null
        } finally { cmd /c rmdir "$link1" | Out-Null }
        if (-not (Test-Path (Join-Path $outside1 "secret.txt"))) { throw "outside file disappeared: guard failed" }
        if (Test-Path (Join-Path $lab "stolen.txt")) { throw "a file was written outside the task root" }
    }

    if (-not $SkipB3 -and (Want "B3")) {
        Step "13. B3: real web research with a second round (topic only, no URLs)"
        Harness new --spec demo\task_b3_web.json --set $fit | Out-Null
        Harness run --task-id B3 | Out-Null
        Resume-Until-Done "B3" 2
        Harness status --task-id B3 | Out-Null
    }
    if (-not $SkipB3 -and (Want "B4")) {
        # B4 = the same topic under the assignment's own requirements (second round of refined queries, stop when
        # the review is done). B3 also demands a followed link and <= 60 % pages per domain, which the ITMO
        # network does not allow (most non-arXiv sites time out there), so B3 can only end partial.
        Step "13b. B4: real web research, second round, completion by the review criteria"
        Harness new --spec demo\task_b4_web.json --set $fit | Out-Null
        Harness run --task-id B4 | Out-Null
        Resume-Until-Done "B4" 2
        Harness status --task-id B4 | Out-Null
    }
} finally {
    Stop-Process -Id $fx.Id -Force -ErrorAction SilentlyContinue
}

Step "14. Evaluation tables and final checks"
# check-mark symbols copied from web pages are kept as plain words in the committed files (logged per task)
foreach ($id in $ids) { Run-Py scripts\plain_text_marks.py --task-id $id }
Run-Py scripts\evaluate.py
if ($DryRun) { Run-Py scripts\final_checks.py } else { Run-Py scripts\final_checks.py --readme }

if ($DryRun) { Write-Host "`nDry run done in $WorkDir ($([int]((Get-Date) - $t0).TotalMinutes) min)." -ForegroundColor Green; exit 0 }

Step "15. Secret scan"
if ($key) {
    $leaks = Get-ChildItem traces, results -Recurse -File | Select-String -SimpleMatch -Pattern $key -List
    if ($leaks) { throw "The LLM token was found in: $($leaks.Path -join ', '). Nothing was committed." }
}
Write-Host "No token in traces/ and results/."

if ($SkipPush) { Write-Host "`nDone (git skipped)."; exit 0 }

Step "16. Commit and push (only lab2/traces, lab2/results, regenerated README blocks)"
git add -A -- traces results README.md
git commit -m "Lab 2: re-recorded $($ids -join ', ') with qwen3.8-27b after the audit"
if ($LASTEXITCODE -ne 0) { throw "git commit failed (nothing to commit?)" }
git push
if ($LASTEXITCODE -ne 0) { throw "git push failed" }
Write-Host "`nDone in $([int]((Get-Date) - $t0).TotalMinutes) min. Check results\final_checks.md" -ForegroundColor Green
