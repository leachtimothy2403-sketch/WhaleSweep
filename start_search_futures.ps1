# WhaleSweep -- single-process futures search (2026-09-24).
#
# Searches MNQ (NASDAQ futures, real CME data), 6E (Euro FX futures, real
# CME data) and FDXM (Mini-DAX, priced on GER40 CFD data -- see
# claude/dax_cfd_vs_futures.md) on 3min/5min only, 6-year window, using
# the futures cost-table entries (Tradeify commission + 1 tick):
#   MNQ 1.16 pts (CFD NDX100 1.83) | FDXM 1.744 pts (CFD GER40 3.39)
#   6E 0.0000996 (CFD EURUSD 0.00007 -- futures are MORE expensive here)
#
# One python process = one core. Runs at BelowNormal priority so it can
# share the VPS with the CFD search.
#
# Usage (from the WhaleSweep folder):
#   .\start_search_futures.ps1                       # 100,000 iterations
#   .\start_search_futures.ps1 -Iterations 50000
# Progress:
#   Get-Content run_futures_console.log -Tail 20 -Wait
# Stop:
#   Stop-Process -Id (Get-Content whale_sweep_output_futures\pid.txt)

param(
    [int]$Iterations = 100000,
    [string]$OutDir = "whale_sweep_output_futures"
)

$ErrorActionPreference = "Stop"
$assets = @("MNQ", "6E", "FDXM")
$tfs = @("3min", "5min")

# 1. Build FDXM from the VPS's full-history GER40 CFD 1-min file if needed.
if (-not (Test-Path "ws_precomputed_FDXM_3min.parquet") -or -not (Test-Path "ws_precomputed_FDXM_5min.parquet")) {
    if (-not (Test-Path "ws_precomputed_GER40_1min.parquet")) {
        Write-Error "ws_precomputed_GER40_1min.parquet not found -- needed to build FDXM."
        exit 1
    }
    Write-Host "Building FDXM from GER40 CFD 1-min data (one-off, a few minutes)..." -ForegroundColor Yellow
    $env:FUT_HISTORY_YEARS = "6.5"
    py -3 precompute_futures.py cfd FDXM ws_precomputed_GER40_1min.parquet
    if ($LASTEXITCODE -ne 0) { Write-Error "FDXM precompute failed"; exit 1 }
    Remove-Item Env:\FUT_HISTORY_YEARS -ErrorAction SilentlyContinue
}

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
    -RedirectStandardOutput "run_futures_console.log" -RedirectStandardError "run_futures_console.log.err" `
    -WindowStyle Hidden -PassThru
$proc.PriorityClass = "BelowNormal"
$proc.Id | Out-File -Encoding ascii "$OutDir\pid.txt"

Remove-Item Env:\WS_ASSETS, Env:\WS_TIMEFRAMES, Env:\WS_HISTORY_YEARS, Env:\WS_OUTPUT_DIR, Env:\WS_ITERATIONS, `
    Env:\OMP_NUM_THREADS, Env:\OPENBLAS_NUM_THREADS, Env:\MKL_NUM_THREADS -ErrorAction SilentlyContinue

Write-Host "Futures search started: PID $($proc.Id), assets $($assets -join ','), tfs $($tfs -join ','), 6y, $Iterations iterations -> $OutDir" -ForegroundColor Green
Write-Host "Progress: Get-Content run_futures_console.log -Tail 20 -Wait"
