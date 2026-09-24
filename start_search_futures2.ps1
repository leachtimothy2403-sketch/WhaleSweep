# WhaleSweep -- per-asset parallel futures search, batch 2 (2026-09-24): MGC (gold), MCL (crude), 6J (yen).
# Built from start_search_futures.ps1; parquets built on the laptop from Databento GC.FUT / CL.v.0+v.1 / 6J.FUT.
#
# Costs (whale_sweep_cost_table.py, NinjaTrader/Tradovate all-in RT + 1 tick):
#   MGC 0.312 | MCL 0.0312 | 6J 0.000000996
# 3min/5min only, 6-year window. Requires ws_precomputed_{MGC,MCL,6J}_{3min,5min}.parquet
# copied from the laptop.
#
# One python process per asset (3 cores). BelowNormal priority.
#
# Usage (from the WhaleSweep folder):
#   .\start_search_futures2.ps1                       # 50,000 iterations per asset
#   .\start_search_futures2.ps1 -Iterations 100000
# Progress:
#   Get-Content run_futures2_MGC_console.log -Tail 20 -Wait   (MCL, 6J likewise)
# Stop:
#   "MGC","MCL","6J" | % { Stop-Process -Id (Get-Content "whale_sweep_output_futures2_$_\pid.txt") }

param(
    [int]$Iterations = 50000,          # PER ASSET (each asset gets its own process/core)
    [string]$OutPrefix = "whale_sweep_output_futures2"
)

$ErrorActionPreference = "Stop"
$assets = @("MGC", "MCL", "6J")
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
$env:WS_ITERATIONS = "$Iterations"
$env:OMP_NUM_THREADS = "1"; $env:OPENBLAS_NUM_THREADS = "1"; $env:MKL_NUM_THREADS = "1"
foreach ($a in $assets) {
    $out = "${OutPrefix}_$a"
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    $env:WS_ASSETS = $a
    $env:WS_OUTPUT_DIR = $out
    $proc = Start-Process -FilePath "py" -ArgumentList @("-3", "whale_sweep.py") `
        -RedirectStandardOutput "run_futures2_${a}_console.log" -RedirectStandardError "run_futures2_${a}_console.log.err" `
        -WindowStyle Hidden -PassThru
    $proc.PriorityClass = "BelowNormal"
    $proc.Id | Out-File -Encoding ascii "$out\pid.txt"
    Write-Host "  $a -> PID $($proc.Id), output $out, log run_futures2_${a}_console.log" -ForegroundColor Green
}

Remove-Item Env:\WS_ASSETS, Env:\WS_TIMEFRAMES, Env:\WS_HISTORY_YEARS, Env:\WS_OUTPUT_DIR, Env:\WS_ITERATIONS, `
    Env:\OMP_NUM_THREADS, Env:\OPENBLAS_NUM_THREADS, Env:\MKL_NUM_THREADS -ErrorAction SilentlyContinue

Write-Host "Futures batch 2 started: 3 processes, $Iterations iterations each, tfs $($tfs -join ','), 6y" -ForegroundColor Green
Write-Host "Progress: Get-Content run_futures2_MGC_console.log -Tail 5   (same for MCL, 6J)"
