"""
WhaleSweep -- per-asset leverage caps, used to derive the REAL margin a
position requires given the strategy's own risk-based sizing (position
size = risk_dollars / stop_distance; notional = position_size *
entry_price; margin = notional / leverage). Companion table to
`whale_sweep_cost_table.py` (real per-asset trading cost) -- this one
answers "how much margin does a given position tie up", the missing
piece identified in the 2026-09-20 leverage/margin investigation (see
claude/handoff.md in the WhaleSweep Claude Project for the full
writeup: portfolio_optimizer.py's simulation had ZERO concept of
margin/leverage/notional before this).

Values are FTMO's published per-asset-class leverage caps as Tim
relayed them (2026-09-20): Forex up to 1:100; most indices up to 1:50
(HK50/US2000/SPN35 are capped lower, at 1:30, but none of those are
traded here); metals up to 1:30; commodities up to 1:3 (not currently
traded here either). These have NOT been independently cross-checked
against FTMO's own current published trading-conditions page -- worth
doing before treating a margin-gated pass rate as final, same caveat
`whale_sweep_cost_table.py` carries for its own numbers.

AAPL is a rough, UNVERIFIED placeholder (FTMO typically caps single
US-stock leverage much lower than FX/indices, commonly around 1:5) --
AAPL/1min has already failed screening on other grounds as of the
2026-09-20 real-cost re-screen, so this is not expected to matter in
practice, but is flagged loudly in case an AAPL candidate ever survives
screening: get FTMO's actual stock leverage figure before trusting this.
"""
from typing import Dict

LEVERAGE_TABLE: Dict[str, float] = {
    "EURUSD": 100.0,
    "GBPUSD": 100.0,
    "GER40": 50.0,
    "NDX100": 50.0,
    "SPX500": 50.0,
    "US30": 50.0,
    "XAUUSD": 30.0,
    # UNVERIFIED -- see module docstring.
    "AAPL": 5.0,
}


def leverage_for(asset: str) -> float:
    """Raises a clear, actionable error rather than a bare KeyError if
    an asset shows up in a portfolio combo with no known leverage cap --
    silently defaulting here would be exactly the kind of
    quietly-wrong-forever number this table exists to avoid."""
    try:
        return LEVERAGE_TABLE[asset]
    except KeyError:
        raise SystemExit(
            f"No leverage cap known for asset {asset!r} in "
            f"whale_sweep_leverage_table.LEVERAGE_TABLE -- add a real "
            f"(ideally FTMO-confirmed) figure before margin-gating any "
            f"combo that includes it."
        )
