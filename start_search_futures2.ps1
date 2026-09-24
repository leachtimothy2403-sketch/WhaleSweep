# WhaleSweep -- single-process futures search, batch 2 (2026-09-24): MGC (gold), MCL (crude), 6J (yen).
# Built from start_search_futures.ps1; parquets built on the laptop from Databento GC.FUT / CL.v.0+v.1 / 6J.FUT.
#
# Costs (whale_sweep_cost_table.py, NinjaTrader/Tradovate all-in RT + 1 tick):
#   MGC 0.312 | MCL 0.0312 | 6J 0.000000996
# 3min/5min only, 6-year window. Requires ws_precomputed_{MGC,MCL,6J}_{3min,5min}.parquet
# copied from the laptop.
#
# One python process = one core. Runs at BelowNormal priority so it can
# share the VPS with the CFD search.
#
# Usage (from the WhaleSweep folder):
#   .\start_search_futures2.ps1                       # 100,000 iterations
#   .\start_search_futures2.ps1 -Iterations 50000
# Progress:
#   Get-Content run_futures2_console.log -Tail 20 -Wait
# Stop:
#   Stop-Process -Id (Get-Content whale_sweep_output_futures2\pid.txt)

param(
    [int]$Iterations = 100000,
    [string]$OutDir = "whale_sweep_output_futures2"
)

$ErrorActionPreference = "Stop"
$assets = @("MGC", "MCL", "6J")
$tfs = @("3min", "5min")

# 2. Every (asset, tf) file must be present -- MNQ and 6E are built on the
#    laptop and copied over by hand (parquets are gitignored).
$missing = @()
foreach ($a in $assets) { foreach ($t in $tfs) {
    if (-not (Test-Path "ws_precomputed_${a}_${t}.parquet")) { $missing += "ws_precomputed_${a}_${t}.parquet" }
} }
if ($missing.Count -gt 0) {
    Write-Error ("Missing: " + ($missing -join ", ") + " -- copy them from the laptop's WhaleSweep folder.")
    exit 1
}

# 3. Refuse to 'resume' into an old checkpoint (the silent no-op gotcha).
if (Test-Path "$OutDir\checkpoint.json") {
    Write-Error "$OutDir\checkpoint.json already exists. Rename/move $OutDir first, or pass -OutDir <new folder>."
    exit 1
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# 4. Launch one process.
$env:WS_ASSETS = ($assets -join ",")
$env:WS_TIMEFRAMES = ($tfs -join ",")
$env:WS_HISTORY_YEARS = "6"
$env:WS_OUTPUT_DIR = $OutDir
$env:WS_ITERATIONS = "$Iterations"
$env:OMP_NUM_THREADS = "1"; $env:OPENBLAS_NUM_THREADS = "1"; $env:MKL_NUM_THREADS = "1"

$proc = Start-Process -FilePath "py" -ArgumentList @("-3", "whale_sweep.py") `
    -RedirectStandardOutput "run_futures2_console.log" -RedirectStandardError "run_futures2_console.log.err" `
    -WindowStyle Hidden -PassThru
$proc.PriorityClass = "BelowNormal"
$proc.Id | Out-File -Encoding ascii "$OutDir\pid.txt"

Remove-Item Env:\WS_ASSETS, Env:\WS_TIMEFRAMES, Env:\WS_HISTORY_YEARS, Env:\WS_OUTPUT_DIR, Env:\WS_ITERATIONS, `
    Env:\OMP_NUM_THREADS, Env:\OPENBLAS_NUM_THREADS, Env:\MKL_NUM_THREADS -ErrorAction SilentlyContinue

Write-Host "Futures search started: PID $($proc.Id), assets $($assets -join ','), tfs $($tfs -join ','), 6y, $Iterations iterations -> $OutDir" -ForegroundColor Green
Write-Host "Progress: Get-Content run_futures2_console.log -Tail 20 -Wait"
