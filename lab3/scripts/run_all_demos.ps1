<#
Records all lab 3 traces with the real ITMO model and publishes the results.
Run INSIDE the ITMO network, Docker Desktop running:

    powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1                  # everything
    powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1 -Part dialog     # only the main dialog M1
    powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1 -Part exhaustive # only partial+resume EXP1
    powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1 -Part rerun -MainTask M2 -ExpTask EXP2
                                                        # dialog + exhaustive again with new task ids, corpus kept
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
    [ValidateSet("all", "dialog", "exhaustive", "rerun")][string]$Part = "all",
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

if ($Part -in @("all", "exhaustive", "rerun")) {
    Step "12. $($ExpTask): exhaustive from the CLI with a tiny budget (partial), then resume"
    Nav subagent --kind exhaustive --scope storage --task-id $ExpTask --set max_model_calls=6 --request-file demo/request_exhaustive_ru.txt | Out-Null
    Nav coverage --task-id $ExpTask | Out-Null
    Nav resume --task-id $ExpTask --set max_model_calls=120 | Out-Null
    Nav coverage --task-id $ExpTask | Out-Null
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
