# Project Context: WhaleSweep Quantitative Trading System

## 1. Core Objective
WhaleSweep is an institutional liquidity-sweep reversal strategy built to pass FTMO 2-step challenges:
- **Profit Targets**: 5% (Phase 1) / 10% (Phase 2)
- **Hard Floor Rules**: Max $5,000 Daily Drawdown (5%), Static Equity Floor.
- **Goal**: High-probability sequence pass rates using multi-asset decorrelated candidates rather than single-strategy bets.

## 2. Architecture & Key Files
- `portfolio_optimizer.py`: Handles Candidate discovery, Kelly-weighted risk allocation, covariance matrices, and historical pass-rate simulations.
- `data_ingest.py`: Fetches local MT5/cTrader tick data (GER40, EURUSD, XAUUSD, NDX100).
- `vps_sync.py`: Synchronizes remote trade logs from VPS (GBPUSD, SPX500, US30).
- `portfolio_report.py`: Generates Monte Carlo distributions, rolling pass-rate window buckets, and drawdown analyses.

## 3. Critical Portfolio Constraints & Edge Cases
- **Fat-Tailed Asymmetry**: Average R-returns are high (e.g., 9.55R on EURUSD/3min), but win rates can be as low as ~23%. Expectancy relies on long winners running; never optimize for raw win rate.
- **03:00 NY Session Concurrency**: All 6 legs share the exact same session open. Cross-asset correlations approach 1.0 during macro news spikes.
- **Margin & Leverage Gating**: Always implement an explicit combined margin/equity cap (target: 50–80% max utilization) across simultaneous open positions to suppress multi-leg concurrent drawdown spikes.
- **Data Horizon**: Prioritize models calibrated over the 6-year history (2016–2026) to handle the 2022–2024 weak-edge regime.

## 4. Coding Conventions
- Standard Python 3.11+, typed hints using `typing`.
- Vectorized operations via `numpy` and `pandas` for backtesting loops.
- Asynchronous non-blocking checks (`asyncio`) for real-time MT5 execution wrappers.