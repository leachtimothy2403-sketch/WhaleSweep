# WhaleSweep -- one-command, N-way parallel search.
# Mirrors ../MeanReversion/start_search_4x.ps1's own approach deliberately
# (Start-Process -FilePath py directly, no intermediate cmd.exe to
# mis-parse a quote -- see that script's own comments for why).
#
# Splits the full asset universe into $Groups round-robin buckets (each
# process gets its own WS_ASSETS + WS_OUTPUT_DIR so they never write to
# the same checkpoint/results files), launches one `py -3 whale_sweep.py`
# per bucket at BelowNormal priority, and logs each to its own
# run<N>_console.log in this directory. Safe to disconnect the RDP
# session afterward (a plain disconnect doesn't kill the processes, only
# a log-off or reboot does).
#
# Usage:
#   .\start_search_4x.ps1                     # 4 groups, 100,000-iteration ceiling per process
#   .\start_search_4x.ps1 -Groups 6 -Iterations 50000
#
# Stop everything (hard kill -- loses progress since the last checkpoint,
# <= WS_CHECKPOINT_EVERY iterations per group):
#   Get-Process -Name py -ErrorAction SilentlyContinue | Stop-Process

param(
    [int]$Groups = 4,
    [int]$Iterations = 100000
)

$ErrorActionPreference = "Stop"

$allAssets = @(
    "EURUSD","GBPUSD","XAUUSD","NDX100","SPX500","US30","AAPL"
)

# Only search assets that actually precomputed successfully.
$available = $allAssets | Where-Object { Test-Path "ws_precomputed_$($_)_1min.parquet" }
if ($available.Count -eq 0) {
    Write-Error "No ws_precomputed_*.parquet files found -- run precompute_all.ps1 first."
    exit 1
}
Write-Host "Assets available for search: $($available -join ', ')" -ForegroundColor Cyan

# Round-robin split into $Groups buckets.
$buckets = @{}
for ($i = 0; $i -lt $Groups; $i++) { $buckets[$i] = @() }
for ($i = 0; $i -lt $available.Count; $i++) { $buckets[$i % $Groups] += $available[$i] }

for ($g = 0; $g -lt $Groups; $g++) {
    if ($buckets[$g].Count -eq 0) { continue }
    $assetList = $buckets[$g] -join ","
    $outDir = "whale_sweep_output_group$g"
    $logFile = "run${g}_console.log"
    Write-Host "Group $g -> assets [$assetList] -> $outDir (log: $logFile)" -ForegroundColor Yellow

    $psi = @{
        FilePath = "py"
        ArgumentList = @("-3", "whale_sweep.py")
        RedirectStandardOutput = $logFile
        RedirectStandardError = "${logFile}.err"
        WindowStyle = "Hidden"
        PassThru = $true
    }
    $env:WS_ASSETS = $assetList
    $env:WS_OUTPUT_DIR = $outDir
    $env:WS_ITERATIONS = "$Iterations"
    $proc = Start-Process @psi
    $proc.PriorityClass = "BelowNormal"
    Write-Host "  started PID $($proc.Id)" -ForegroundColor Green
}

Remove-Item Env:\WS_ASSETS, Env:\WS_OUTPUT_DIR, Env:\WS_ITERATIONS -ErrorAction SilentlyContinue
Write-Host "`nAll groups launched. Safe to disconnect RDP (not log off)." -ForegroundColor Cyan
Write-Host "Check progress: Get-Content whale_sweep_output_group0\run.log -Tail 20 -Wait"
