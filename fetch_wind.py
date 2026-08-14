#!/usr/bin/env python3
"""Fetch the two macro 'winds' for the gold desk from FRED and write wind.json.

  Real yield  -> DFII10   (10Y TIPS constant-maturity: the ex-ante real yield;
                           identical in meaning to US10Y minus T10YIE, one clean series)
  Dollar      -> DTWEXBGS (nominal broad trade-weighted USD index; note ~1wk FRED lag)

Needs env var FRED_API_KEY. Standard library only, so it runs anywhere.
"""
import json, os, sys, datetime, urllib.request, urllib.parse

API = "https://api.stlouisfed.org/fred/series/observations"
KEY = os.environ.get("FRED_API_KEY")
LOOKBACK = 5   # observations back to measure the slope (~1 trading week)

# Deadband per series: if |change over LOOKBACK| is below this, call it 'flat'
# (keeps tiny wiggles from flipping the wind read). Tune to taste.
FLAT_EPS = {"DFII10": 0.03, "DTWEXBGS": 0.15}

def series(sid, limit=40):
    q = urllib.parse.urlencode({
        "series_id": sid, "api_key": KEY, "file_type": "json",
        "sort_order": "desc", "limit": limit,
    })
    with urllib.request.urlopen(API + "?" + q, timeout=30) as r:
        data = json.load(r)
    out = []
    for o in data.get("observations", []):
        v = o.get("value", ".")
        if v not in (".", "", None):
            out.append((o["date"], float(v)))
    return out  # newest first

def read(sid):
    obs = series(sid)
    if len(obs) < LOOKBACK + 1:
        raise RuntimeError("not enough data for " + sid)
    latest_date, latest = obs[0]
    _, past = obs[LOOKBACK]
    change = round(latest - past, 4)
    eps = FLAT_EPS.get(sid, 0.0)
    direction = "flat" if abs(change) < eps else ("rising" if change > 0 else "falling")
    return {
        "series": sid, "value": round(latest, 4), "asof": latest_date,
        "dir": direction, "change": change, "lookback": LOOKBACK,
    }

def main():
    if not KEY:
        print("FRED_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    out = {
        "updated": datetime.date.today().isoformat(),
        "yields": read("DFII10"),
        "dollar": read("DTWEXBGS"),
    }
    out["dollar"]["note"] = "Broad trade-weighted USD proxy for DXY; FRED H.10 lags ~1 week."
    with open("wind.json", "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
