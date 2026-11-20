<#
Records all lab 3 traces with the real ITMO model and publishes the results.
Run INSIDE the ITMO network, Docker Desktop running:

    powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1                  # everything
    powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1 -Part dialog     # only the main dialog M1
    powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1 -Part exhaustive # only partial+resume EXP1
    powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1 -Part rerun -MainTask M2 -ExpTask EXP2
                                                        # dialog + exhaustive again with new task ids, corpus kept
    powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1 -Part delegation -MainTask M3 -SkipTests
    ... -SkipPush -SkipTests

Steps (task ids):
  ING1  ingestion AGENT over workspace\input (53 files): simulated crash after the 80th tool, resume in a new process
  ING2  user adds 3 new files to input\new_files; CLI ingestion of that folder only
  ING3  re-run on the unchanged corpus: nothing re-indexed, no duplicates
  probe six search combinations without LLM (control queries from corpus\manifest.json)
  M1    main agent dialog: L0->L1->L2, the six searches, non-blocking deep + exhaustive sub-agents
  EXP1  exhaustive sub-agent from the CLI with a tiny budget (partial), then resume with a larger one
#>
param(
    [ValidateSet("all", "dialog", "exhaustive", "rerun", "delegation", "final", "deep", "final2")][string]$Part = "all",
    [switch]$SkipPush,
    [switch]$SkipTests,
    [string]$EnvFile = ".env",
    [string]$MainTask = "M1",
    [string]$ExpTask = "EXP1"
)

$ErrorActionPreference = "Stop"
$lab = Split-Path -Parent $PSScriptRoot
Set-Location $lab
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$py = Join-Path $lab ".venv\Scripts\python.exe"
$EnvFile = Join-Path $lab $EnvFile
$env:HARNESS_ENV_FILE = $EnvFile

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Run-Py { & $py @args | Out-Host; if ($LASTEXITCODE -ne 0) { throw "python $($args -join ' ') failed with exit code $LASTEXITCODE" } }
function Nav { & $py -m navsearch.cli @args | Out-Host; return $LASTEXITCODE }
function Task-Status($id) { return (& $py -m navsearch.cli status --task-id $id --json | Out-String | ConvertFrom-Json) }
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
        Nav resume --task-id $id | Out-Null
    }
}

Step "0. Python environment"
if (-not (Test-Path $py)) {
    python -m venv .venv
    & $py -m pip install -r requirements.txt
}
if (-not (Test-Path $EnvFile)) { throw "$EnvFile not found: copy .env.example to .env and set LLM_API_KEY" }

Step "1. ITMO LLM reachability"
$base = Read-Env "LLM_BASE_URL"
$key = Read-Env "LLM_API_KEY"
try {
    $models = Invoke-RestMethod -Uri "$base/models" -Headers @{ Authorization = "Bearer $key" } -TimeoutSec 10
} catch {
    throw "LLM server $base is not reachable. Connect to the ITMO network (or VPN) and run again. Details: $($_.Exception.Message)"
}
Write-Host ("LLM server OK, model: " + ($models.data | Select-Object -First 1).id + " (window: measured by the lab 2 harness, cached)")

Step "2. Elasticsearch (lab 1 docker compose)"
Push-Location ..\lab1
docker compose up -d
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "docker compose up failed: is Docker Desktop running?" }
Pop-Location
Wait-Url "http://localhost:9200/_cluster/health" 60

Step "3. Embedding model (downloaded once into .cache\fastembed)"
Run-Py -c "import navsearch; from navsearch.embed import Embedder; print('embedding dims:', len(Embedder().embed(['test'])[0]))"

if (-not $SkipTests) {
    Step "4. Offline tests"
    & $py -m pytest -q | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }
}

if ($Part -eq "all") {
    Step "5. Fresh task root and empty indices"
    Run-Py -m navsearch.cli prepare --reset

    Step "6. ING1: ingestion agent over input (simulated crash after the 80th tool call)"
    $env:HARNESS_CRASH_AT = "after_tool:80"
    $code = Nav ingest --folder input --task-id ING1
    Remove-Item Env:HARNESS_CRASH_AT
    Write-Host "ingest exit code: $code (137 = killed after a tool, before the next checkpoint)"
    Nav status --task-id ING1 | Out-Null

    Step "7. ING1: resume in a NEW process until the corpus is complete"
    Resume-Until-Done "ING1" 4
    Nav status --task-id ING1 | Out-Null
    Nav validate | Out-Null

    Step "8. ING2: the user adds new files; CLI ingestion of input\new_files"
    Run-Py -m navsearch.cli add-new-files
    Nav ingest --folder input/new_files --task-id ING2 | Out-Null
    Resume-Until-Done "ING2" 2

    Step "9. ING3: re-run on the unchanged corpus (no duplicates expected)"
    Nav ingest --folder input --task-id ING3 | Out-Null
    Nav validate | Out-Null

    Step "10. Six search combinations without LLM (control queries)"
    Nav probe | Out-Null
}

if ($Part -in @("all", "dialog", "rerun")) {
    Step "11. $($MainTask): main agent dialog with non-blocking deep and exhaustive sub-agents"
    Nav chat --task-id $MainTask --script demo\dialog_main_ru.txt | Out-Null
}

if ($Part -in @("all", "delegation")) {
    $dt = "M3"
    if ($Part -eq "delegation") { $dt = $MainTask }
    Step "11b. $($dt): non-blocking delegation dialog (main answers while sub-agents work)"
    Nav chat --task-id $dt --script demo\dialog_delegation_ru.txt | Out-Null
}

if ($Part -in @("all", "exhaustive", "rerun")) {
    Step "12. $($ExpTask): exhaustive from the CLI with a tiny budget (partial), then resume"
    Nav subagent --kind exhaustive --scope storage --task-id $ExpTask --set max_model_calls=6 --request-file demo/request_exhaustive_ru.txt | Out-Null
    Nav coverage --task-id $ExpTask | Out-Null
    Nav resume --task-id $ExpTask --set max_model_calls=120 | Out-Null
    Nav coverage --task-id $ExpTask | Out-Null
}

if ($Part -eq "final") {
    Step "F1. ING4: new files, crash in the MIDDLE of ingestion (after the 8th tool), resume in a NEW process"
    Run-Py -m navsearch.cli add-new-files --batch new_batch2 --to input/new_files2
    $env:HARNESS_CRASH_AT = "after_tool:8"
    $code = Nav ingest --folder input/new_files2 --task-id ING4
    Remove-Item Env:HARNESS_CRASH_AT
    Write-Host "ingest exit code: $code (137 = killed right after a tool, before the next checkpoint)"
    if ($code -ne 137) { throw "ING4 did not stop at the crash point (exit code $code)" }
    Nav status --task-id ING4 | Out-Null
    Resume-Until-Done "ING4" 3
    Nav status --task-id ING4 | Out-Null
    Nav validate | Out-Null

    Step "F2. ING5: the same files of the first new batch once more -> no new documents, ES counts unchanged"
    $countCmd = "import navsearch; from navsearch.index import CorpusIndex; i=CorpusIndex(); i.refresh(); print('L1', i.count('L1'), 'L2', i.count('L2'))"
    $before = (& $py -c $countCmd | Out-String).Trim()
    Run-Py -m navsearch.cli add-new-files --batch new_batch --to input/new_files_repeat
    Nav ingest --folder input/new_files_repeat --task-id ING5 | Out-Null
    $after = (& $py -c $countCmd | Out-String).Trim()
    "Elasticsearch before ING5: $before`r`nElasticsearch after ING5:  $after" | Out-File -Encoding utf8 results\ING5_no_duplicates.txt
    Get-Content results\ING5_no_duplicates.txt
    if ($before -ne $after) { Write-Host "WARNING: counts changed" -ForegroundColor Yellow }

    Step "F3. Six search combinations without LLM (control queries)"
    Nav probe | Out-Null

    Step "F4. M5: main agent dialog in plain language (deep first, then the six searches, exhaustive)"
    Nav chat --task-id M5 --script demo\dialog_final_ru.txt | Out-Null

    Step "F5. EXP3: exhaustive with a tiny budget (partial) - the remainder is saved - then resume"
    Nav subagent --kind exhaustive --scope storage --task-id EXP3 --set max_model_calls=6 --request-file demo/request_exhaustive_ru.txt | Out-Null
    New-Item -ItemType Directory -Force results\EXP3 | Out-Null
    & $py -m navsearch.cli coverage --task-id EXP3 | Out-File -Encoding utf8 results\EXP3\coverage_partial.json
    Get-Content results\EXP3\coverage_partial.json | Select-Object -First 12
    Nav resume --task-id EXP3 --set max_model_calls=120 | Out-Null
    & $py -m navsearch.cli coverage --task-id EXP3 | Out-File -Encoding utf8 results\EXP3\coverage_final.json
}

if ($Part -eq "deep") {
    Step "D1. M6: deep research delegated in natural language; the main agent answers meanwhile, then uses the result"
    Nav chat --task-id M6 --script demo\dialog_deep_ru.txt | Out-Null
}

if ($Part -eq "final2") {
    # A repeated run must start a NEW dialog task: on 20.11 the second run continued the old M7 and the main
    # agent mixed up the sub-agents of both runs. So the first free id from M8 on is taken, and the traces and
    # results of the earlier final2 attempts (M7, M8, ... with their sub-agents) are removed from the repository.
    $f2 = $null
    for ($i = 8; $i -lt 100 -and -not $f2; $i++) {
        & $py -m navsearch.cli status --task-id "M$i" | Out-Null
        if ($LASTEXITCODE -ne 0) { $f2 = "M$i" }
    }
    $old = '^M([7-9]|[1-9][0-9])([_-]|$)'
    Get-ChildItem traces -File | Where-Object { $_.Name -match $old } | Remove-Item -Force
    Get-ChildItem results -Directory | Where-Object { $_.Name -match $old } | Remove-Item -Recurse -Force
    Step "G1. ${f2}: deep and exhaustive in parallel, the main agent answers meanwhile and uses both results"
    Nav chat --task-id $f2 --script demo\dialog_final2_ru.txt | Out-Null

    # the same for the partial -> resume demo: a new id from EXP5 on, earlier final2 attempts (EXP4, ...) removed
    $exp = $null
    for ($i = 5; $i -lt 100 -and -not $exp; $i++) {
        & $py -m navsearch.cli status --task-id "EXP$i" | Out-Null
        if ($LASTEXITCODE -ne 0) { $exp = "EXP$i" }
    }
    $oldExp = '^EXP([4-9]|[1-9][0-9])([_-]|$)'
    Get-ChildItem traces -File | Where-Object { $_.Name -match $oldExp } | Remove-Item -Force
    Get-ChildItem results -Directory | Where-Object { $_.Name -match $oldExp } | Remove-Item -Recurse -Force
    Step "G2. ${exp}: exhaustive with a tiny budget (partial), remainder saved, resume in a new process"
    Nav subagent --kind exhaustive --scope storage --task-id $exp --set max_model_calls=8 --request-file demo/request_exhaustive_ru.txt | Out-Null
    New-Item -ItemType Directory -Force "results\$exp" | Out-Null
    & $py -m navsearch.cli coverage --task-id $exp | Out-File -Encoding utf8 "results\$exp\coverage_partial.json"
    Nav resume --task-id $exp --set max_model_calls=120 | Out-Null
    & $py -m navsearch.cli coverage --task-id $exp | Out-File -Encoding utf8 "results\$exp\coverage_final.json"
}

Step "13. Export and technical results"
Nav export | Out-Null
Run-Py scripts\evaluate.py

Step "14. Secret scan"
$leaks = Get-ChildItem traces, results -Recurse -File | Select-String -SimpleMatch -Pattern $key -List
if ($leaks) { throw "The LLM token was found in: $($leaks.Path -join ', '). Nothing was committed." }
Write-Host "No token in traces/ and results/."

if ($SkipPush) { Write-Host "`nDone (git skipped)."; exit 0 }

Step "15. Commit and push"
git add traces results demo corpus
git commit -m "Lab 3: traces with qwen3.8-27b, L0/L1/L2 corpus, coverage and technical results ($Part)"
if ($LASTEXITCODE -ne 0) { throw "git commit failed (nothing to commit?)" }
git push
if ($LASTEXITCODE -ne 0) { throw "git push failed" }
Write-Host "`nDone. Traces: $lab\traces, results: $lab\results" -ForegroundColor Green
