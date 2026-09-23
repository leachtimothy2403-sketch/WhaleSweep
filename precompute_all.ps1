# WhaleSweep -- precompute the 8-asset universe (narrowed to 7 on
# 2026-09-19 per Tim, GER40/DAX added back same day so the 4-way
# parallel launcher's 4th group isn't left with NDX100 alone):
# EURUSD, GBPUSD, XAUUSD, NDX100, SPX500, US30, GER40, AAPL.
# Run this once on the VPS before any search, and again if the shared
# Dukascopy cache gets refreshed with new history.
#
# Point $env:WS_DUKASCOPY_ROOT at the VPS's Dukascopy cache first, e.g.:
#   $env:WS_DUKASCOPY_ROOT = "C:\Users\Administrator\RCTBE\data\dukascopy"
# (confirmed 2026-09-19 against Tim's own directory listing -- AAPL's
# code in precompute.py's STOCK_ASSETS, "AAPLUSUSD", matches that
# listing's real folder name).
#
# Usage:
#   .\precompute_all.ps1

$ErrorActionPreference = "Stop"

$assets = @(
    "EURUSD","GBPUSD","XAUUSD","NDX100","SPX500","US30","AAPL","GER40","BTCUSD",
    # Added 2026-09-23 per Tim, from the VPS Dukascopy cache listing --
    # see precompute.py's FOREX_ASSETS/METAL_ASSETS/INDEX_ASSETS docstrings
    # for why these 7 specifically were picked.
    "AUDUSD","USDJPY","USDCAD","XAGUSD","FRA40","UK100","JPN225"
)

foreach ($a in $assets) {
    Write-Host "=== precompute $a ===" -ForegroundColor Cyan
    py -3 precompute.py $a
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "$a failed or has no cached data -- check the Dukascopy code/path before the search run."
    }
}
Write-Host "Done. Check for ws_precomputed_<ASSET>_<TF>.parquet files above." -ForegroundColor Green
