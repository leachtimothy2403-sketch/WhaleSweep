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

    # UNVERIFIED placeholder for MNQ (Micro Nasdaq-100 futures), added
    # 2026-09-23, REVISED 2026-09-23 per Tim (a genuine exchange-traded
    # futures contract should cost meaningfully less than a CFD broker's
    # marked-up spread -- the first cut here didn't reflect that). Built
    # bottom-up from confirmed numbers instead of a borrowed figure:
    #   - Commission: Tradeify's published MNQ round-turn cost is
    #     $1.82/contract, all-in (exchange + NFA + clearing + commission,
    #     help.tradeify.co, confirmed 2026-09-23) -> at MNQ's $2/point
    #     value that's 1.82 / 2.0 = 0.91 points.
    #   - Spread/slippage: MNQ's tick size is 0.25 index points
    #     (quantvps.com, confirmed 2026-09-23); it is one of the most
    #     liquid micro futures contracts and trades at the minimum 1-tick
    #     spread almost continuously during normal hours, so 1 tick
    #     (0.25 points) is used as the baseline fill-slippage assumption.
    #   - Total: 0.91 + 0.25 = 1.16 points.
    # This is deliberately the FIRST cut of a real futures cost model,
    # not a padded worst case -- it does NOT extra-pad for the fact that
    # this strategy's entries cluster right around volatile liquidity-
    # sweep wicks, where a resting stop-market order could occasionally
    # slip more than 1 tick. Still meaningfully (37%) cheaper than
    # NDX100's CFD estimate of 1.83 points, consistent with an exchange-
    # traded futures contract vs. a CFD broker's own dealt spread.
    # Replace with a real measured figure from actual fills if/when
    # available -- this remains an estimate, just a better-justified one
    # than the original 1.57 (which borrowed an unrelated strategy's
    # slippage calibration).
    "MNQ": 1.16,

    # Futures via Tradeify, added 2026-09-24. Same bottom-up method as MNQ:
    # Tradeify's published round-trip commission per contract
    # (help.tradeify.co "Trading Commission Fees", checked 2026-09-24)
    # divided by the contract's point value, plus ONE tick for spread /
    # slippage. Units are raw price units, like every other entry.
    #   6E  (Euro FX, CME): $6.20 RT / $125,000 per 1.0 = 0.0000496
    #       + 1 tick 0.00005                              = 0.0000996
    #       NOTE: this is MORE than the FTMO EURUSD CFD figure (0.00007) --
    #       FX futures are not cheaper than a raw-spread FX CFD. (M6E is
    #       worse still: $1.60/$12,500 + 0.0001 tick = 0.000228.)
    #   FDXM (Mini-DAX, Eurex): EUR 3.72 RT / EUR 5 per point = 0.744 pts
    #       + 1 tick (1.0 pt)                             = 1.744 pts
    #       (vs GER40 CFD 3.39). FDAX would be 5.94/25 + 1 = 1.24 pts but
    #       its EUR 25/pt size is too coarse for ~$1k risk sizing; FDXS
    #       would be 1.42/1 + 1 = 2.42 pts.
    #   FDXM is searched on GER40 CFD price data (Databento's Eurex history
    #   only starts 2025-03) -- see claude/dax_cfd_vs_futures.md.
    "6E":   0.0000996,
    "FDXM": 1.744,
    # 2026-09-24, second futures batch (FundedNext uses the same NinjaTrader/
    # Tradovate all-in rates as Tradeify: NQ 2.88/side = $5.76 RT on both).
    # Round-trip commission / point value + 1 tick, in raw price units:
    #   MGC (micro gold, $10/pt, tick 0.10):   $2.12/10  + 0.10      = 0.312
    #       (GC mini would be 6.20/100 + 0.10 = 0.162 but $100/pt is too coarse)
    #   MCL (micro crude, $100/pt, tick 0.01): $2.12/100 + 0.01      = 0.0312
    #       (CL mini 6.00/1000 + 0.01 = 0.016, $1,000/pt too coarse)
    #   6J  (yen, 12.5M JPY, tick 0.0000005):  $6.20/12.5M + 0.0000005 = 0.000000996
    # Priced on full-size GC / CL / 6J Databento data (same points).
    # Median cost / 5-min ATR (2024+): MGC 0.12, MCL 0.31, 6J 0.42 (6E 0.34, MNQ 0.07).
    "MGC":  0.312,
    "MCL":  0.0312,
    "6J":   0.000000996,
}
