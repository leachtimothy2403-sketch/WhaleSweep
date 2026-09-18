# WhaleSweep — precompute every asset (forex + indices + stocks).
# Run this once on the VPS before any search, and again if the shared
# Dukascopy cache gets refreshed with new history.
#
# Point $env:WS_DUKASCOPY_ROOT at the VPS's Dukascopy cache first, e.g.:
#   $env:WS_DUKASCOPY_ROOT = "C:\Users\Administrator\RCTBE\data\dukascopy"
# (confirmed 2026-09-19 against Tim's own directory listing — the stock
# codes in precompute.py's STOCK_ASSETS, AAPLUSUSD/WMTUSUSD/XOMUSUSD/
# DISUSUSD, match that listing's real folder names). Note: Tim asked for
# WBD (Discovery) but that listing has no WBD folder, only DISUSUSD
# (Disney) — DIS is used in its place, see precompute.py's own comment.
#
# Usage:
#   .\precompute_all.ps1

$ErrorActionPreference = "Stop"

$assets = @(
    "EURUSD","GBPUSD","USDJPY","AUDUSD","NZDUSD","USDCAD","USDCHF",
    "NDX100","SPX500","US30","GER40","FRA40","UK100","JPN225",
    "AAPL","WMT","XOM","DIS"
)

foreach ($a in $assets) {
    Write-Host "=== precompute $a ===" -ForegroundColor Cyan
    py -3 precompute.py $a
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "$a failed or has no cached data — check the Dukascopy code/path before the search run."
    }
}
Write-Host "Done. Check for ws_precomputed_<ASSET>_<TF>.parquet files above." -ForegroundColor Green
