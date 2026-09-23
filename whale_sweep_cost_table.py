"""
WhaleSweep — real per-asset trading costs (spread + commission), used to
make the backtest reflect a real FTMO fill instead of a free/swept
"cost_atr_mult" search parameter that could (and did) settle at zero.

Values copied from RCTBE's `layer2_cost_table.py` / `opr_cost_table.py`
(2026-09-20) -- RCTBE live-measured these directly against a real
FTMO-Demo MT5 account (`_measure_ftmo_costs.py`: reads live bid/ask
spread per symbol via the MetaTrader5 python package, confirmed stable
across repeated samples ~20s apart). Per RCTBE's own docstring:
  - Gold/oil/indices: live spread measured via tick bid/ask
    (commission-free on indices/energy per FTMO's published fee
    schedule; gold/silver carry a small metals commission folded into
    the total).
  - FX pairs: live spread + FTMO's confirmed $5/lot round-trip
    commission (converted to price-unit terms via each symbol's
    tick_value/tick_size).
No artificial slippage padding -- FTMO's raw spread already is the
realistic fill cost. Units are round-trip cost in the SAME raw price
units as `risk` (entry-to-stop distance) elsewhere in this codebase, so
no conversion is needed to use these as `cost` in generate_signals().

CAVEAT (not yet independently verified for WhaleSweep specifically):
these numbers were measured on RCTBE's own FTMO-Demo account. Confirm
WhaleSweep will trade the same broker/account type before trusting them
as-is -- spread and commission schedules can differ by account type. The
more rigorous fix is to re-run a copy of RCTBE's `_measure_ftmo_costs.py`
directly against the actual WhaleSweep FTMO account. Treat these as a
large improvement over the old cost_atr_mult=0.0 default, not as a final
verified number.

AAPL is a rough, UNVERIFIED estimate (~0.1% of a ~$150-300 price range) --
RCTBE never traded stocks, so there is no broker-measured figure to copy.
Flag loudly if a real AAPL candidate emerges before trusting its
economics; get an actual measured spread/commission first.
"""
from typing import Dict

# Round-trip cost per unit position, in the instrument's own raw price
# units (same units as `entry - sl` / ATR elsewhere in this codebase).
COST_TABLE: Dict[str, float] = {
    "XAUUSD": 0.53,
    "NDX100": 1.83,
    "SPX500": 0.60,
    "EURUSD": 0.00007,
    "US30":   2.78,
    "GER40":  3.39,
    "GBPUSD": 0.00010,
    # UNVERIFIED -- placeholder for BTCUSD. Replace with live measured data.
    "BTCUSD": 5.0,
    # UNVERIFIED -- see module docstring. Not broker-measured.
    "AAPL":   0.30,

    # UNVERIFIED placeholders added 2026-09-23 for the 7-asset expansion
    # (Tim's request, see precompute.py) -- rough analyst-judgment
    # figures based on typical retail CFD spreads for each instrument
    # class, NOT broker-measured. The patched RCTBE/_measure_ftmo_costs.py
    # already probes AUDUSD/USDJPY/USDCAD/FRA40.cash/UK100.cash on the
    # live FTMO-Demo MT5 terminal -- replace these five the moment that
    # comes back. XAGUSD and JPN225(.cash) were added to that script's
    # SYMBOLS list too but hadn't been run as of this commit.
    "AUDUSD": 0.00012,
    "USDCAD": 0.00015,
    "USDJPY": 0.012,
    "XAGUSD": 0.03,
    "FRA40":  2.5,
    "UK100":  2.5,
    # Nikkei's much higher absolute price level (~30-40k) means its
    # typical retail CFD spread in raw points is much larger than the
    # European/US indices above -- do not mistake this for a worse
    # relative cost.
    "JPN225": 8.0,
}
