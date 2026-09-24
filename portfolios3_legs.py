"""Leg registry + daily-R correlation matrix for the 3-portfolio design (2026-09-24)."""
import pandas as pd, combo_flex as C
F1 = "whale_sweep_output_futures/results.csv"; F2 = "whale_sweep_output_futures2_{}/results.csv"
EXTRA = {
    "6E#12654": (F1, 12654, 125_000.0, 10), "6E#38431": (F1, 38431, 125_000.0, 10),
    **{f"MGC#{r}": (F2.format("MGC"), r, 10.0, 1) for r in (7068, 4104, 22471, 9163, 16609)},
    **{f"MCL#{r}": (F2.format("MCL"), r, 100.0, 1) for r in (8044, 16931, 15549, 11854)},
    **{f"6J#{r}": (F2.format("6J"), r, 12_500_000.0, 10) for r in (9981, 969, 7271, 1053)},
}
C.LEGS.update(EXTRA)
if __name__ == "__main__":
    legs = list(C.LEGS)
    S = {}
    for l in legs:
        tr = C.leg_trades(l)
        S[l] = pd.Series([t["r"] for t in tr], index=[t["t_in"].date() for t in tr]).groupby(level=0).sum()
    D = pd.DataFrame(S).fillna(0); D.index = pd.to_datetime(D.index); D = D.loc["2020-09-22":"2026-09-15"]
    c = D.corr().round(2); c.to_csv("portfolios3_leg_corr.csv")
    pd.set_option("display.width", 300); print(c.to_string())
