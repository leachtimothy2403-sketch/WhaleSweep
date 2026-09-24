#!/usr/bin/env python3
"""Builds two_accounts_report.html from two_accounts.json, two_accounts_B_grid.csv,
two_accounts_A_sweep.csv (all produced by two_accounts.py)."""
import json
import pandas as pd

d = json.load(open("two_accounts.json"))
d["cohorts"] = [c for c in d["cohorts"] if c["start"] >= "2024-09-23"]
d["grid"] = pd.read_csv("two_accounts_B_grid.csv").to_dict("records")
d["sweep"] = pd.read_csv("two_accounts_A_sweep.csv").to_dict("records")
html = open("two_accounts_template.html", encoding="utf-8").read().replace("/*__DATA__*/null", json.dumps(d, default=str))
open("two_accounts_report.html", "w", encoding="utf-8").write(html)
print("wrote two_accounts_report.html", len(d["cohorts"]), "cohorts")
