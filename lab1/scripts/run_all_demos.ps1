<#
Records every lab 1 demo trace with the real ITMO model and publishes the results.
Run from any folder, INSIDE the ITMO network (the LLM server is not reachable outside):

    powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1
    powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1 -SkipPush   # no git commit/push

Steps: LLM check -> Elasticsearch up -> fresh workspace/index -> main dialog -> verify/export
-> limit dialog -> errors dialog with ES stopped -> retry dialog with ES started -> final verify/export
-> secret scan -> git commit + push of traces/ and results/.
#>
param([switch]$SkipPush)

$ErrorActionPreference = "Stop"
$lab = Split-Path -Parent $PSScriptRoot
Set-Location $lab
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$py = Join-Path $lab ".venv\Scripts\python.exe"

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Run-Py { & $py @args; if ($LASTEXITCODE -ne 0) { throw "python $($args -join ' ') failed with exit code $LASTEXITCODE" } }
function Read-Env($name) {
    $line = Get-Content .env -Encoding UTF8 | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if (-not $line) { throw "$name is missing in .env" }
    return $line.Substring($name.Length + 1).Trim()
}
function Wait-ES {
    for ($i = 0; $i -lt 60; $i++) {
        try { Invoke-RestMethod -Uri "http://localhost:9200/_cluster/health" -TimeoutSec 3 | Out-Null; return } catch { Start-Sleep -Seconds 3 }
    }
    throw "Elasticsearch did not become ready on http://localhost:9200"
}

Step "0. Python environment"
if (-not (Test-Path $py)) {
    python -m venv .venv
    & $py -m pip install -r requirements.txt
}
if (-not (Test-Path .env)) { throw ".env not found: copy .env.example to .env and set LLM_API_KEY" }

Step "1. ITMO LLM reachability"
$base = Read-Env "LLM_BASE_URL"
$key = Read-Env "LLM_API_KEY"
try {
    $models = Invoke-RestMethod -Uri "$base/models" -Headers @{ Authorization = "Bearer $key" } -TimeoutSec 10
    Write-Host ("LLM server OK, models: " + (($models.data | ForEach-Object { $_.id }) -join ", "))
} catch {
    throw "LLM server $base is not reachable. Connect to the ITMO network (or VPN) and run again. Details: $($_.Exception.Message)"
}

Step "2. Elasticsearch"
docker compose up -d
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed: is Docker Desktop running?" }
Wait-ES

Step "3. Fresh workspace and empty index"
Run-Py scripts\prepare_workspace.py --reset-index

Step "4. Main dialog (organize, browse, move, index, search, re-index, late files, tool errors)"
Run-Py -m organizer.cli --script demo\dialog_ru.txt

Step "5. Verify and export after the main dialog"
& $py scripts\verify.py
Copy-Item results\verify_report.json results\verify_report_main.json -Force
Run-Py scripts\export_index.py

Step "6. Execution-limit dialog (AGENT_MODEL_CALLS_PER_TURN=3)"
$env:AGENT_MODEL_CALLS_PER_TURN = "3"
try { Run-Py -m organizer.cli --script demo\dialog_limit_ru.txt } finally { Remove-Item Env:AGENT_MODEL_CALLS_PER_TURN }

Step "7. Errors dialog with Elasticsearch STOPPED"
docker compose stop
try { Run-Py -m organizer.cli --script demo\dialog_errors_ru.txt } finally { docker compose start; Wait-ES }

Step "8. Retry dialog with Elasticsearch running again"
Run-Py -m organizer.cli --script demo\dialog_retry_ru.txt

Step "9. Final verify and export"
& $py scripts\verify.py
$verifyExit = $LASTEXITCODE
Run-Py scripts\export_index.py
Run-Py scripts\export_graph.py | Out-Null

Step "10. Secret scan"
$leaks = Get-ChildItem traces, results -Recurse -File | Select-String -SimpleMatch -Pattern $key -List
if ($leaks) { throw "The LLM token was found in: $($leaks.Path -join ', '). Nothing was committed." }
Write-Host "No token in traces/ and results/."

if ($SkipPush) { Write-Host "`nDone (git skipped). verify exit code: $verifyExit"; exit 0 }

Step "11. Commit and push"
git add traces results docs demo
git commit -m "Lab 1: demo traces with qwen3.8-27b, verify report, index export and workspace snapshot"
if ($LASTEXITCODE -ne 0) { throw "git commit failed (nothing to commit?)" }
git push
if ($LASTEXITCODE -ne 0) { throw "git push failed" }
Write-Host "`nDone. verify exit code: $verifyExit (0 = all checks passed). Traces: $lab\traces" -ForegroundColor Green
