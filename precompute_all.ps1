# WhaleSweep — precompute every asset (forex + indices + stocks).
# Run this once on the VPS before any search, and again if the shared
# Dukascopy cache gets refreshed with new history.
#
# Point $env:WS_DUKASCOPY_ROOT at the VPS's actual Dukascopy cache first
# if it differs from the default (see precompute.py's docstring) — in
# particular, confirm the STOCK_ASSETS Dukascopy codes in precompute.py
# (AAPL.US/USD etc.) match that cache's real folder names for
# AAPL/WMT/XOM/WBD before relying on the stock legs of the sweep.
#
# Usage:
#   .\precompute_all.ps1

$ErrorActionPreference = "Stop"

$assets = @(
    "EURUSD","GBPUSD","USDJPY","AUDUSD","NZDUSD","USDCAD","USDCHF",
    "NDX100","SPX500","US30","GER40","FRA40","UK100","JPN225",
    "AAPL","WMT","XOM","WBD"
)

foreach ($a in $assets) {
    Write-Host "=== precompute $a ===" -ForegroundColor Cyan
    py -3 precompute.py $a
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "$a failed or has no cached data — check the Dukascopy code/path before the search run."
    }
}
Write-Host "Done. Check for ws_precomputed_<ASSET>_<TF>.parquet files above." -ForegroundColor Green
