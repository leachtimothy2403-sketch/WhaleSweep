# WhaleSweep -- per-asset parallel search with the look-ahead fix (2026-09-24).
# whale_sweep.py now defaults to WS_CAUSAL_OPEN=1: the session-open anchor is the
# open of the first in-session bar (not the future 09:30 open) and LONDON levels
# only arm after 08:00. Every result produced before this fix is inflated.
#
# One process (= one core) per asset, 3min/5min, 6-year window.
# Usage:
#   .\start_search_causal.ps1                                   # all 7 assets, 50,000 iterations each
#   .\start_search_causal.ps1 -Assets MNQ,6E,MGC -Iterations 50000
# Progress:
#   Get-ChildItem run_causal_*_console.log | % { $_.Name; Get-Content $_ -Tail 1 }
# Stop:
#   Get-ChildItem whale_sweep_output_causal_*\pid.txt | % { Stop-Process -Id (Get-Content $_) }

param(
    [int]$Iterations = 50000,          # PER ASSET (each asset gets its own process/core)
    [string]$OutPrefix = "whale_sweep_output_causal",
    [string[]]$Assets = @("MNQ", "6E", "MGC", "MCL", "6J", "6JT", "MGCT")
)

$ErrorActionPreference = "Stop"
$assets = $Assets
$tfs = @("3min", "5min")

# 1. Every (asset, tf) file must be present -- built on the laptop and
#    copied over by hand (parquets are gitignored).
$missing = @()
foreach ($a in $assets) { foreach ($t in $tfs) {
    if (-not (Test-Path "ws_precomputed_${a}_${t}.parquet")) { $missing += "ws_precomputed_${a}_${t}.parquet" }
} }
if ($missing.Count -gt 0) {
    Write-Error ("Missing: " + ($missing -join ", ") + " -- copy them from the laptop's WhaleSweep folder.")
    exit 1
}

# 2. Refuse to 'resume' into an old checkpoint (the silent no-op gotcha).
foreach ($a in $assets) {
    if (Test-Path "${OutPrefix}_$a\checkpoint.json") {
        Write-Error "${OutPrefix}_$a\checkpoint.json already exists. Rename/move that folder first, or pass -OutPrefix <new name>."
        exit 1
    }
}

# 3. One process (= one core) per asset, each with its own output folder and log.
$env:WS_TIMEFRAMES = ($tfs -join ",")
$env:WS_HISTORY_YEARS = "6"
$env:WS_CAUSAL_OPEN = "1"
$env:WS_ITERATIONS = "$Iterations"
$env:OMP_NUM_THREADS = "1"; $env:OPENBLAS_NUM_THREADS = "1"; $env:MKL_NUM_THREADS = "1"
foreach ($a in $assets) {
    $out = "${OutPrefix}_$a"
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    $env:WS_ASSETS = $a
    $env:WS_OUTPUT_DIR = $out
    $proc = Start-Process -FilePath "py" -ArgumentList @("-3", "whale_sweep.py") `
        -RedirectStandardOutput "run_causal_${a}_console.log" -RedirectStandardError "run_causal_${a}_console.log.err" `
        -WindowStyle Hidden -PassThru
    $proc.PriorityClass = "BelowNormal"
    $proc.Id | Out-File -Encoding ascii "$out\pid.txt"
    Write-Host "  $a -> PID $($proc.Id), output $out, log run_causal_${a}_console.log" -ForegroundColor Green
}

Remove-Item Env:\WS_ASSETS, Env:\WS_TIMEFRAMES, Env:\WS_HISTORY_YEARS, Env:\WS_OUTPUT_DIR, Env:\WS_ITERATIONS, Env:\WS_CAUSAL_OPEN, `
    Env:\OMP_NUM_THREADS, Env:\OPENBLAS_NUM_THREADS, Env:\MKL_NUM_THREADS -ErrorAction SilentlyContinue

Write-Host "Causal search started: $($assets.Count) processes, $Iterations iterations each, tfs $($tfs -join ','), 6y" -ForegroundColor Green
Write-Host "Progress: Get-ChildItem run_causal_*_console.log | % { $_.Name; Get-Content $_ -Tail 1 }"
