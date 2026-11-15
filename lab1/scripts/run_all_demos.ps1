<#
Records the lab 1 demo traces with the real ITMO model and publishes the results.
Run INSIDE the ITMO network (the LLM server is not reachable outside):

    powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1                # everything from scratch
    powershell -ExecutionPolicy Bypass -File scripts\run_all_demos.ps1 -Part extra    # only guard/errors/retry on the current workspace
    ... -SkipPush                                                                       # no git commit/push

Part "all":   LLM check -> ES up -> fresh workspace/index -> main dialog -> verify/export -> limit dialog
              -> then everything from part "extra".
Part "extra": guard dialog (tool errors, junction outside the workspace) -> errors dialog with ES stopped
              -> retry dialog with ES started -> final verify/export -> secret scan -> git commit + push.
#>
param(
    [ValidateSet("all", "extra")][string]$Part = "all",
    [switch]$SkipPush
)

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

if ($Part -eq "all") {
    Step "3. Fresh workspace and empty index"
    Run-Py scripts\prepare_workspace.py --reset-index

    Step "4. Main dialog (organize, browse, move, index, search, re-index, late files)"
    Run-Py -m organizer.cli --script demo\dialog_ru.txt

    Step "5. Verify and export after the main dialog"
    & $py scripts\verify.py
    Copy-Item results\verify_report.json results\verify_report_main.json -Force
    Run-Py scripts\export_index.py

    Step "6. Execution-limit dialog (AGENT_MODEL_CALLS_PER_TURN=3)"
    $env:AGENT_MODEL_CALLS_PER_TURN = "3"
    try { Run-Py -m organizer.cli --script demo\dialog_limit_ru.txt } finally { Remove-Item Env:AGENT_MODEL_CALLS_PER_TURN }
} elseif (-not (Test-Path workspace\catalog.json)) {
    throw "workspace\catalog.json not found: run the full demo first (without -Part extra)"
}

Step "7. Guard dialog: tool errors and a junction pointing outside the workspace"
$outside = Join-Path $env:TEMP "itmo_lab1_outside"
New-Item -ItemType Directory -Force $outside | Out-Null
Set-Content -Path (Join-Path $outside "secret.txt") -Value "outside-the-workspace secret" -Encoding UTF8
$link = Join-Path $lab "workspace\input\outside_link"
if (-not (Test-Path $link)) { New-Item -ItemType Junction -Path $link -Target $outside | Out-Null }
try { Run-Py -m organizer.cli --script demo\dialog_guard_ru.txt }
finally { cmd /c rmdir "$link" | Out-Null }   # removes only the junction, never the target
if (-not (Test-Path (Join-Path $outside "secret.txt"))) { throw "outside file disappeared: guard failed" }

Step "8. Errors dialog with Elasticsearch STOPPED"
docker compose stop
try { Run-Py -m organizer.cli --script demo\dialog_errors_ru.txt } finally { docker compose start; Wait-ES }

Step "9. Retry dialog with Elasticsearch running again"
Run-Py -m organizer.cli --script demo\dialog_retry_ru.txt

Step "10. Final verify and export"
& $py scripts\verify.py
$verifyExit = $LASTEXITCODE
Run-Py scripts\export_index.py
Run-Py scripts\export_graph.py | Out-Null

Step "11. Secret scan"
$leaks = Get-ChildItem traces, results -Recurse -File | Select-String -SimpleMatch -Pattern $key -List
if ($leaks) { throw "The LLM token was found in: $($leaks.Path -join ', '). Nothing was committed." }
Write-Host "No token in traces/ and results/."

if ($SkipPush) { Write-Host "`nDone (git skipped). verify exit code: $verifyExit"; exit 0 }

Step "12. Commit and push"
git add traces results docs demo
if ($Part -eq "all") { $msg = "Lab 1: demo traces with qwen3.8-27b, verify report, index export and workspace snapshot" }
else { $msg = "Lab 1: guard, ES-failure and retry traces with qwen3.8-27b; updated results" }
git commit -m $msg
if ($LASTEXITCODE -ne 0) { throw "git commit failed (nothing to commit?)" }
git push
if ($LASTEXITCODE -ne 0) { throw "git push failed" }
Write-Host "`nDone. verify exit code: $verifyExit (0 = all checks passed). Traces: $lab\traces" -ForegroundColor Green
