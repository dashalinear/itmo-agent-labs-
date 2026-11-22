<#
Records all lab 1 demo traces and results with the ITMO model, from a clean workspace and an empty
index, as one chain of CLI sessions. With -Push it then commits and pushes only lab1/traces and lab1/results.
Run inside the ITMO network (the LLM server is not reachable outside), from any directory:

    powershell -ExecutionPolicy Bypass -File lab1\scripts\run_all_demos.ps1
    ... -Push            # also git commit + push of traces and results (without it: record only)
    ... -SkipTests       # do not run pytest first
    ... -KeepOldTraces   # do not delete the traces that existed before this run
    ... -DryRun          # plumbing check without the LLM (deterministic stand-in model); only on a copy of
                         # lab1 outside the git repository; never commits, verify failures are not fatal

Chain (the order matters: every session continues from the workspace/index state of the previous one):
  1 main    demo/dialog_ru.txt        organize, browse, move, index, search, re-index, move indexed file,
                                      pause: this script copies fixtures/late/* into workspace/input from a
                                      separate PowerShell process; then the "new files" turn and error turns;
                                      afterwards scripts/verify.py writes results/verify_report_main.json
  2 limit   demo/dialog_limit_ru.txt  a looping request, stopped by the execution limit
  3 guard   demo/dialog_guard_ru.txt  every turn asks for a tool call: bad arguments, a junction pointing outside,
                                      web_fetch against a local fixture server (200, 404, timeout) + closed port
  4 errors  demo/dialog_errors_ru.txt Elasticsearch is stopped: upsert fails (error), move of an indexed doc
                                      (pending_sync)
  5 mcp_down demo/dialog_mcp_down_ru.txt  the MCP server itself cannot start (MCP_COMMAND points to a missing
                                      program): indexing gives mcp_unavailable (error), a move still works
  6 retry   demo/dialog_retry_ru.txt  MCP and Elasticsearch are back: path sync and indexing
  Before the chain: pytest, and the symlink/junction escape tests saved to results/symlink_test.txt
  (the symlink test runs only if Windows Developer Mode is on; otherwise it is recorded as skipped).
  After the chain: verify, index export, graph export, results/RUN_SUMMARY.md, secret scan,
  deletion of the traces from earlier runs, and git commit + push with -Push.
Limits are pinned for reproducibility: AGENT_MODEL_CALLS_PER_TURN=25, AGENT_RECURSION_LIMIT=110.
#>
param(
    [switch]$SkipPush,
    [switch]$Push,
    [switch]$SkipTests,
    [switch]$KeepOldTraces,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
# git commit + push of the results only on request (-Push); without it the script just records them
if (-not $Push) { $SkipPush = $true }
$lab = Split-Path -Parent $PSScriptRoot
Set-Location $lab
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$py = Join-Path $lab ".venv\Scripts\python.exe"
$t0 = Get-Date

function Step($text) { Write-Host "`n=== $text ===  [$([int]((Get-Date) - $t0).TotalSeconds)s]" -ForegroundColor Cyan }
function Warn($text) { Write-Host "WARNING: $text" -ForegroundColor Yellow }
function Run-Py { & $py @args; if ($LASTEXITCODE -ne 0) { throw "python $($args -join ' ') failed with exit code $LASTEXITCODE" } }
function Run-PyEs {   # ES-dependent admin step: fatal in a real run, only a warning in -DryRun
    & $py @args
    if ($LASTEXITCODE -ne 0) {
        if ($DryRun) { Warn "python $($args -join ' ') failed (dry run, continuing)" }
        else { throw "python $($args -join ' ') failed with exit code $LASTEXITCODE" }
    }
}
function Read-Env($name) {
    $line = Get-Content .env -Encoding UTF8 | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if (-not $line) { throw "$name is missing in .env" }
    return $line.Substring($name.Length + 1).Trim()
}
function Test-ES { try { Invoke-RestMethod -Uri "http://localhost:9200/_cluster/health" -TimeoutSec 3 | Out-Null; return $true } catch { return $false } }
function Wait-ES {
    $tries = 60
    if ($DryRun) { $tries = 3 }
    for ($i = 0; $i -lt $tries; $i++) { if (Test-ES) { return } ; Start-Sleep -Seconds 3 }
    if ($DryRun) { Warn "Elasticsearch is not reachable on http://localhost:9200 (dry run, continuing)"; return }
    throw "Elasticsearch did not become ready on http://localhost:9200"
}

$sessions = New-Object System.Collections.ArrayList
$modelArgs = @()
if ($DryRun) { $modelArgs = @("--dry-run-model") }
function Run-Session($label, $title, $dialog, [string[]]$extra = @()) {
    $before = @(Get-ChildItem traces -Filter "session_*.jsonl" -File | ForEach-Object { $_.Name })
    $cli = @("-m", "organizer.cli", "--script", $dialog) + $modelArgs + $extra
    Run-Py @cli
    $new = @(Get-ChildItem traces -Filter "session_*.jsonl" -File | ForEach-Object { $_.Name } | Where-Object { $before -notcontains $_ })
    if ($new.Count -ne 1) { throw "expected exactly one new trace for session '$label', found $($new.Count)" }
    [void]$sessions.Add([ordered]@{ label = $label; title = $title; dialog = $dialog; trace = "traces/$($new[0])" })
    Write-Host "trace of '$label': traces/$($new[0])" -ForegroundColor Green
}

if ($DryRun -and (Test-Path (Join-Path $lab "..\.git"))) {
    throw "-DryRun deletes traces and resets workspace/results: run it on a COPY of lab1 outside the git repository"
}

Step "0. Python environment"
if (-not (Test-Path $py)) {
    python -m venv .venv
    & $py -m pip install -r requirements.txt
}
$key = $null
if ($DryRun) {
    $env:ES_INDEX = "itmo_lab1_dryrun"          # never touch the real lab 1 index
    $modelName = "dry-run-model (NOT an LLM)"
} else {
    if (-not (Test-Path .env)) { throw ".env not found: copy .env.example to .env and set LLM_API_KEY" }
    Step "1. ITMO LLM reachability"
    $base = Read-Env "LLM_BASE_URL"
    $key = Read-Env "LLM_API_KEY"
    $modelName = Read-Env "LLM_MODEL"
    try {
        $models = Invoke-RestMethod -Uri "$base/models" -Headers @{ Authorization = "Bearer $key" } -TimeoutSec 10
        Write-Host ("LLM server OK, models: " + (($models.data | ForEach-Object { $_.id }) -join ", "))
    } catch {
        throw "LLM server $base is not reachable. Connect to the ITMO network (or VPN) and run again. Details: $($_.Exception.Message)"
    }
}

Step "2. Elasticsearch"
if (-not $DryRun) {
    docker compose up -d
    if ($LASTEXITCODE -ne 0) { throw "docker compose up failed: is Docker Desktop running?" }
}
Wait-ES

if (-not $SkipTests) {
    Step "3. Tests (offline + Elasticsearch integration)"
    Run-Py -m pytest -q -p no:cacheprovider
}

Step "3b. Path-escape tests through links (results/symlink_test.txt)"
$devMode = "unknown"
try {
    $v = Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock" -Name AllowDevelopmentWithoutDevLicense -ErrorAction Stop
    $devMode = if ($v.AllowDevelopmentWithoutDevLicense -eq 1) { "on" } else { "off" }
} catch { $devMode = "off (not configured)" }
$ErrorActionPreference = "Continue"   # PS 5.1: stderr of a native command under 2>&1 must not throw
$linkOut = & $py -m pytest tests/test_tools_fs.py -k "symlink or junction" -rs -p no:cacheprovider -W ignore 2>&1 | ForEach-Object { "$_" }
$linkExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
$symlinkSkipped = @($linkOut | Where-Object { $_ -match "SKIPPED" -and $_ -match "symlink" }).Count -gt 0
if ($linkExit -ne 0) { $linkStatus = "FAILED (see output below)" }
elseif (-not $symlinkSkipped) { $linkStatus = "real symlink test PASSED (symlinks are allowed on this machine)" }
else { $linkStatus = "skipped: no symlink privilege (Windows Developer Mode $devMode); the junction test covers the reparse-point escape" }
$linkReport = @("lab 1 path-escape tests through links, $(Get-Date -Format o)",
                "Windows Developer Mode: $devMode", "status: $linkStatus", "", "pytest output:") + $linkOut
[IO.File]::WriteAllLines((Join-Path $lab "results\symlink_test.txt"), [string[]]$linkReport, (New-Object System.Text.UTF8Encoding $false))
Write-Host "symlink/junction tests: $linkStatus"
if ($linkExit -ne 0) { throw "path-escape tests through links failed, see results\symlink_test.txt" }

# Traces of earlier runs: they are not part of this chain and are deleted after a successful run.
$oldTraces = @(Get-ChildItem traces -File | Where-Object { $_.Name -like "session_*" })
$env:AGENT_MODEL_CALLS_PER_TURN = "25"
$env:AGENT_RECURSION_LIMIT = "110"
$startedAt = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz")

Step "4. Fresh workspace (input = fixtures/input, empty data/) and empty index"
if ($DryRun -and -not (Test-ES)) { Run-Py scripts\prepare_workspace.py }
else { Run-Py scripts\prepare_workspace.py --reset-index }
Remove-Item results\late_files_added.json -ErrorAction SilentlyContinue

Step "5. Session 1 (main): late files are added by a SEPARATE process while the CLI waits"
$work = Join-Path $env:TEMP "itmo_lab1_demo"
New-Item -ItemType Directory -Force $work | Out-Null
$marker = Join-Path $work "cli_waiting.json"
Remove-Item $marker -ErrorAction SilentlyContinue
$job = Start-Job -ArgumentList $marker, (Join-Path $lab "fixtures\late"), (Join-Path $lab "workspace\input"), (Join-Path $lab "results\late_files_added.json") -ScriptBlock {
    param($marker, $src, $dst, $log)
    $deadline = (Get-Date).AddMinutes(40)
    while (-not (Test-Path $marker)) {
        if ((Get-Date) -gt $deadline) { throw "the CLI never reached @wait_for_files" }
        Start-Sleep -Milliseconds 300
    }
    $cliPid = (Get-Content $marker -Raw | ConvertFrom-Json).pid
    Start-Sleep -Seconds 2
    $files = @()
    foreach ($f in Get-ChildItem $src -File | Sort-Object Name) {
        Copy-Item $f.FullName (Join-Path $dst $f.Name)
        $files += "input/$($f.Name)"
    }
    $info = [ordered]@{ copier = "run_all_demos.ps1 background job (separate powershell.exe process)"
                        copier_pid = $PID; cli_pid = $cliPid; copied_at = (Get-Date).ToString("o"); files = $files }
    [IO.File]::WriteAllText($log, ($info | ConvertTo-Json), (New-Object System.Text.UTF8Encoding $false))
    "late files copied by PowerShell PID $PID while CLI PID $cliPid was waiting: $($files -join ', ')"
}
try {
    Run-Session "main" "organization, browsing, moves, indexing, search, late files, errors" "demo/dialog_ru.txt" @("--wait-marker", $marker, "--wait-timeout", "900")
} finally {
    Wait-Job $job -Timeout 5 | Out-Null
    $jobState = $job.State
    $jobOut = Receive-Job $job -ErrorAction SilentlyContinue
    Remove-Job $job -Force
}
if ($jobState -ne "Completed") { throw "late-files job did not complete (state: $jobState)" }
Write-Host $jobOut -ForegroundColor Green

Step "6. Verify after the main dialog"
& $py scripts\verify.py
Copy-Item results\verify_report.json results\verify_report_main.json -Force

Step "7. Session 2 (limit): a genuinely looping request with the normal limits"
Run-Session "limit" "looping request stopped by the execution limit" "demo/dialog_limit_ru.txt"

Step "8. Session 3 (guard): tool errors and a junction pointing outside the workspace"
$outside = Join-Path $env:TEMP "itmo_lab1_outside"
New-Item -ItemType Directory -Force $outside | Out-Null
Set-Content -Path (Join-Path $outside "secret.txt") -Value "outside-the-workspace secret" -Encoding UTF8
$link = Join-Path $lab "workspace\input\outside_link"
if (-not (Test-Path $link)) { New-Item -ItemType Junction -Path $link -Target $outside | Out-Null }
$env:NO_PROXY = "127.0.0.1,localhost"; $env:no_proxy = $env:NO_PROXY
$httpLog = Join-Path $lab "results\http_fixture_requests.log"
$httpErr = Join-Path $work "http_fixture_stderr.log"
$httpSrv = Start-Process -FilePath $py -ArgumentList @("scripts\http_fixture_server.py", "--port", "18765", "--slow", "20") `
    -WorkingDirectory $lab -RedirectStandardOutput $httpLog -RedirectStandardError $httpErr -NoNewWindow -PassThru
$httpReady = $false
$ErrorActionPreference = "Continue"   # PS 5.1: stderr of the probe must not throw
for ($i = 0; $i -lt 40 -and -not $httpReady; $i++) {
    Start-Sleep -Milliseconds 250
    & $py -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:18765/health', timeout=2).status == 200 else 1)" 2>$null
    $httpReady = ($LASTEXITCODE -eq 0)
}
$ErrorActionPreference = "Stop"
if (-not $httpReady) { Stop-Process -Id $httpSrv.Id -Force -ErrorAction SilentlyContinue; throw "local HTTP fixture server did not start (port 18765 busy?)" }
Write-Host "HTTP fixture server PID $($httpSrv.Id) on http://127.0.0.1:18765/"
try { Run-Session "guard" "tool contract errors, junction outside the workspace, web_fetch 200/404/timeout" "demo/dialog_guard_ru.txt" }
finally {
    cmd /c rmdir "$link" | Out-Null   # removes only the junction, never the target
    Stop-Process -Id $httpSrv.Id -Force -ErrorAction SilentlyContinue
}
if (-not (Test-Path (Join-Path $outside "secret.txt"))) { throw "outside file disappeared: guard failed" }

Step "9. Session 4 (errors): Elasticsearch STOPPED"
if ($DryRun) {
    $env:ES_URL = "http://127.0.0.1:9"      # dry run: unreachable ES without stopping the shared container
    try { Run-Session "errors" "Elasticsearch unavailable: error and pending_sync" "demo/dialog_errors_ru.txt" }
    finally { Remove-Item Env:ES_URL }
} else {
    docker compose stop
    try { Run-Session "errors" "Elasticsearch unavailable: error and pending_sync" "demo/dialog_errors_ru.txt" }
    finally { docker compose start; Wait-ES }
}

Step "9b. Session 5 (mcp_down): the MCP server cannot start at all"
$env:MCP_COMMAND = "lab1-mcp-server-does-not-exist.exe"   # the CLI fails to spawn the MCP server
try { Run-Session "mcp_down" "MCP server unavailable: indexing fails, organization works" "demo/dialog_mcp_down_ru.txt" }
finally { Remove-Item Env:MCP_COMMAND }
# catalog right after this session: the failed upsert must have left the document in status "error"
Copy-Item workspace\catalog.json results\catalog_after_mcp_down.json -Force

Step "10. Session 6 (retry): MCP and Elasticsearch running again"
Run-Session "retry" "retry after Elasticsearch is back" "demo/dialog_retry_ru.txt"

Step "11. Final verify, index export, graph export"
& $py scripts\verify.py
$verifyExit = $LASTEXITCODE
Run-PyEs scripts\export_index.py
Run-Py scripts\export_graph.py | Out-Null

Step "12. Run summary (results/RUN_SUMMARY.md)"
$manifest = [ordered]@{ started_at = $startedAt; model = $modelName; dry_run = [bool]$DryRun; sessions = $sessions }
$manifestPath = Join-Path $work "run_manifest.json"
[IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 5), (New-Object System.Text.UTF8Encoding $false))
Copy-Item $manifestPath results\run_manifest.json -Force
Run-Py scripts\summarize_run.py --manifest $manifestPath

if (-not $DryRun) {
    Step "13. Secret scan"
    $leaks = Get-ChildItem traces, results -Recurse -File | Select-String -SimpleMatch -Pattern $key -List
    if ($leaks) { throw "The LLM token was found in: $($leaks.Path -join ', '). Nothing was committed." }
    Write-Host "No token in traces/ and results/."
}

if ($verifyExit -ne 0 -and -not $DryRun) {
    throw "verify.py failed (see results/verify_report.json). Old traces were kept, nothing was committed: run the script again."
}

Step "14. Remove traces of earlier runs (not part of this chain)"
if ($KeepOldTraces) { Write-Host "kept (-KeepOldTraces)" }
else {
    foreach ($f in $oldTraces) { Remove-Item $f.FullName -Force; Write-Host "removed traces\$($f.Name)" }
    if (-not $oldTraces) { Write-Host "none" }
}

$elapsed = [int]((Get-Date) - $t0).TotalMinutes
if ($SkipPush -or $DryRun) { Write-Host "`nDone in ~$elapsed min (git skipped). verify exit code: $verifyExit" -ForegroundColor Green; exit 0 }

Step "15. Commit and push (only lab1/traces and lab1/results)"
git add -A -- traces results
if ($LASTEXITCODE -ne 0) { throw "git add failed" }
git commit -m "Lab 1: full demo chain re-recorded with $modelName (6 sessions from a clean workspace), verify, index export, run summary" -- traces results
if ($LASTEXITCODE -ne 0) { throw "git commit failed (nothing to commit?)" }
git push
if ($LASTEXITCODE -ne 0) { throw "git push failed" }
Write-Host "`nDone in ~$elapsed min. verify exit code: $verifyExit (0 = all checks passed). See results\RUN_SUMMARY.md" -ForegroundColor Green
