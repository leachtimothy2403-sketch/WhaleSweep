# WhaleSweep -- per-asset parallel search with the look-ahead fix (2026-09-24).
# whale_sweep.py now defaults to WS_CAUSAL_OPEN=1: the session-open anchor is the
# open of the first in-session bar (not the future 09:30 open) and LONDON levels
# only arm after 08:00. Every result produced before this fix is inflated.
#
# Assets are split round-robin over -Cpus processes (default 4); each process
# samples its own assets at random, iterations = IterationsPerAsset x assets.
# 3min/5min, 6-year window.
# Usage:
#   .\start_search_causal.ps1                                    # 7 assets on 4 CPUs, 40,000 iterations per asset
#   .\start_search_causal.ps1 -Cpus 4 -IterationsPerAsset 50000
#   .\start_search_causal.ps1 -Assets MNQ,6E,MGC,MCL
# Progress:
#   Get-ChildItem run_causal_*_console.log | % { $_.Name; Get-Content $_ -Tail 1 }
# Stop:
#   Get-ChildItem whale_sweep_output_causal_*\pid.txt | % { Stop-Process -Id (Get-Content $_) }

param(
    [int]$Cpus = 4,
    [int]$IterationsPerAsset = 40000,
    [string]$OutPrefix = "whale_sweep_output_causal",
    [string[]]$Assets = @("MNQ", "6E", "MGC", "MCL", "6J", "6JT", "MGCT")
)

$ErrorActionPreference = "Stop"
$tfs = @("3min", "5min")

# 1. Every (asset, tf) file must be present (parquets are gitignored -- copy from the laptop).
$missing = @()
foreach ($a in $Assets) { foreach ($t in $tfs) {
    if (-not (Test-Path "ws_precomputed_${a}_${t}.parquet")) { $missing += "ws_precomputed_${a}_${t}.parquet" }
} }
if ($missing.Count -gt 0) {
    Write-Error ("Missing: " + ($missing -join ", ") + " -- copy them from the laptop's WhaleSweep folder.")
    exit 1
}

# 2. Round-robin assets into $Cpus groups.
$groups = @{}
for ($g = 0; $g -lt $Cpus; $g++) { $groups[$g] = @() }
for ($i = 0; $i -lt $Assets.Count; $i++) { $groups[$i % $Cpus] += $Assets[$i] }

# 3. Refuse to 'resume' into an old checkpoint (the silent no-op gotcha).
for ($g = 0; $g -lt $Cpus; $g++) {
    if ($groups[$g].Count -eq 0) { continue }
    if (Test-Path "${OutPrefix}_g$g\checkpoint.json") {
        Write-Error "${OutPrefix}_g$g\checkpoint.json already exists. Rename/move that folder first, or pass -OutPrefix <new name>."
        exit 1
    }
}

# 4. One process per group.
$env:WS_TIMEFRAMES = ($tfs -join ",")
$env:WS_HISTORY_YEARS = "6"
$env:WS_CAUSAL_OPEN = "1"
$env:OMP_NUM_THREADS = "1"; $env:OPENBLAS_NUM_THREADS = "1"; $env:MKL_NUM_THREADS = "1"
for ($g = 0; $g -lt $Cpus; $g++) {
    if ($groups[$g].Count -eq 0) { continue }
    $out = "${OutPrefix}_g$g"
    $iters = $IterationsPerAsset * $groups[$g].Count
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    $env:WS_ASSETS = ($groups[$g] -join ",")
    $env:WS_OUTPUT_DIR = $out
    $env:WS_ITERATIONS = "$iters"
    $proc = Start-Process -FilePath "py" -ArgumentList @("-3", "whale_sweep.py") `
        -RedirectStandardOutput "run_causal_g${g}_console.log" -RedirectStandardError "run_causal_g${g}_console.log.err" `
        -WindowStyle Hidden -PassThru
    $proc.PriorityClass = "BelowNormal"
    $proc.Id | Out-File -Encoding ascii "$out\pid.txt"
    Write-Host "  g$g [$($groups[$g] -join ',')] -> PID $($proc.Id), $iters iterations, output $out, log run_causal_g${g}_console.log" -ForegroundColor Green
}

Remove-Item Env:\WS_ASSETS, Env:\WS_TIMEFRAMES, Env:\WS_HISTORY_YEARS, Env:\WS_OUTPUT_DIR, Env:\WS_ITERATIONS, Env:\WS_CAUSAL_OPEN, `
    Env:\OMP_NUM_THREADS, Env:\OPENBLAS_NUM_THREADS, Env:\MKL_NUM_THREADS -ErrorAction SilentlyContinue

Write-Host "Causal search started on $Cpus CPUs, $IterationsPerAsset iterations per asset" -ForegroundColor Green
Write-Host "Progress: Get-ChildItem run_causal_g*_console.log | % { `$_.Name; Get-Content `$_ -Tail 1 }"
