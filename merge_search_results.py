#!/usr/bin/env python3
"""
WhaleSweep — merge N parallel search groups' top_strategies.json into one
ranked list.

Ported from RCTBE's `_merge_layer2_results.py`, adapted for WhaleSweep's
own candidate key: RCTBE (and MeanReversion) rank one strategy per
ASSET, but WhaleSweep sweeps entry_timeframe itself as a parameter, so
two candidates for the same asset on different timeframes are genuinely
different strategies — the floor here is per (asset, entry_timeframe),
not per asset alone.

start_search_4x.ps1 runs $Groups independent processes, each with its
own WS_OUTPUT_DIR (whale_sweep_output_group0, group1, ...) so their
checkpoints/results never collide — each ends up with its own top-50
(WS's own TOP_N) by its own local view, potentially mixing several
(asset, tf) pairs per group. This concatenates all of them and re-ranks.

Writes TWO files, mirroring RCTBE's convention so the fix can be
compared against naive behavior instead of just trusted blind:
  - <out>            : NEW method — union of (global top TOP_N_GLOBAL by
                        score) and (each (asset, tf)'s own top
                        TOP_N_PER_KEY by score). Without the per-key
                        floor, one (asset, tf) pair with an inflated
                        score (e.g. a short-history asset, or a
                        higher-frequency timeframe with more trades
                        propping up the score's log(n_trades) term)
                        can crowd out every other pair's candidates from
                        a pure global top-N, even ones with a real edge.
  - <out>.old_global.json : OLD method — pure global top N by score, no
                        per-key floor.

gate2_holdout.py / plateau_check.py / candidate_report.py all default
their --top-json to whale_sweep_output/top_strategies.json — this
script's default --out writes exactly there, so no extra flag is needed
after merging.

Usage:
    py -3 merge_search_results.py
    py -3 merge_search_results.py --pattern "whale_sweep_output_group*"
"""
import argparse
import glob
import json
import os
from collections import defaultdict

DEFAULT_PATTERN = "whale_sweep_output_group*"
DEFAULT_OUT = os.path.join("whale_sweep_output", "top_strategies.json")
TOP_N_GLOBAL = 50
TOP_N_PER_KEY = 10


def _key(row: dict):
    return (row.get("asset"), row.get("entry_timeframe"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default=DEFAULT_PATTERN)
    ap.add_argument("--out", default=DEFAULT_OUT,
                     help="Where to write the merged top_strategies.json — matches what "
                          "gate2_holdout.py/plateau_check.py/candidate_report.py already "
                          "default --top-json to.")
    args = ap.parse_args()

    group_dirs = sorted(d for d in glob.glob(args.pattern) if os.path.isdir(d))
    if not group_dirs:
        print(f"No group output dirs matched '{args.pattern}' — nothing to merge.")
        return

    merged = []
    for d in group_dirs:
        f = os.path.join(d, "top_strategies.json")
        if not os.path.exists(f):
            print(f"[SKIP] {d} — no top_strategies.json yet")
            continue
        with open(f, encoding="utf-8") as fh:
            rows = json.load(fh)
        print(f"[{d}] {len(rows)} strategies")
        merged.extend(rows)

    if not merged:
        print("Nothing to merge.")
        return

    merged.sort(key=lambda x: x.get("score", -999), reverse=True)

    old_global = merged[:TOP_N_GLOBAL]

    global_top = merged[:TOP_N_GLOBAL]
    by_key = defaultdict(list)
    for r in merged:
        by_key[_key(r)].append(r)  # already sorted by score, so [:N] below is each key's own top-N
    per_key_top = [r for rows in by_key.values() for r in rows[:TOP_N_PER_KEY]]

    seen_ids = set()
    new_merged = []
    for r in global_top + per_key_top:
        if id(r) not in seen_ids:
            seen_ids.add(id(r))
            new_merged.append(r)
    new_merged.sort(key=lambda x: x.get("score", -999), reverse=True)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(new_merged, f, indent=2, default=str)

    old_out = args.out + ".old_global.json"
    with open(old_out, "w", encoding="utf-8") as f:
        json.dump(old_global, f, indent=2, default=str)

    print(f"\nMerged {len(group_dirs)} groups, {len(merged)} total candidates, "
          f"{len(by_key)} (asset, tf) pairs: {sorted(by_key.keys())}")
    print(f"  NEW (top-{TOP_N_GLOBAL} global + top-{TOP_N_PER_KEY}/(asset,tf) floor, deduped): "
          f"{len(new_merged)} strategies -> {args.out}")
    print(f"  OLD (top-{TOP_N_GLOBAL} global, no floor): {len(old_global)} strategies -> {old_out}")

    print("\nPer-(asset,tf) candidate count in NEW merge (compare against OLD to see the floor's effect):")
    new_by_key = defaultdict(int)
    for r in new_merged:
        new_by_key[_key(r)] += 1
    old_by_key = defaultdict(int)
    for r in old_global:
        old_by_key[_key(r)] += 1
    for key in sorted(by_key.keys()):
        asset, tf = key
        print(f"  {asset:8s}/{tf:5s} OLD={old_by_key.get(key, 0):3d}  NEW={new_by_key.get(key, 0):3d}")

    print("\nTop 5 (NEW):")
    for i, s in enumerate(new_merged[:5]):
        print(f"  [{i}] {s.get('asset')}/{s.get('entry_timeframe')} score={s.get('score')} "
              f"min_pf={s.get('min_profit_factor')} n_trades={s.get('n_trades')}")


if __name__ == "__main__":
    main()
