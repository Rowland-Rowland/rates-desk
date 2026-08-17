#!/usr/bin/env python3
"""Fetch the VIX regime for the index desk from FRED and write vix.json.

  VIX    -> VIXCLS  (CBOE Volatility Index, 30-day implied vol)
  VIX3M  -> VXVCLS  (CBOE S&P 500 3-Month Volatility Index, formerly VXV)

Term structure = VIX / VIX3M:
  < 1  contango       -> calm, dealers likely long gamma, pinning / mean-reversion
  > 1  backwardation  -> stress, acceleration / trend days

Regime -> fade (reversion setups valid) vs follow (reversion is a trap).
Needs env var FRED_API_KEY. Standard library only.
"""
import json, os, sys, datetime, urllib.request, urllib.parse

API = "https://api.stlouisfed.org/fred/series/observations"
KEY = os.environ.get("FRED_API_KEY")

# Boundaries (tune to taste)
CONTANGO_HI = 0.98   # ratio below this = clear contango
BACKWARD_LO = 1.02   # ratio above this = clear backwardation
VIX_LOW     = 20.0   # below = calm level
VIX_HIGH    = 28.0   # above = high absolute fear

def latest(sid):
    q = urllib.parse.urlencode({
        "series_id": sid, "api_key": KEY, "file_type": "json",
        "sort_order": "desc", "limit": 10,
    })
    with urllib.request.urlopen(API + "?" + q, timeout=30) as r:
        data = json.load(r)
    for o in data.get("observations", []):
        v = o.get("value", ".")
        if v not in (".", "", None):
            return o["date"], float(v)
    raise RuntimeError("no valid observation for " + sid)

def grade(vix, ratio):
    # structure
    structure = "contango" if ratio < CONTANGO_HI else ("backwardation" if ratio > BACKWARD_LO else "flat")
    # regime
    if structure == "backwardation":
        regime, play = "stressed-trend", "follow"
    elif structure == "flat":
        if vix < VIX_LOW:
            regime, play = "mixed", "mixed"
        else:
            regime, play = "stressed-trend", "follow"
    else:  # contango
        if vix < VIX_LOW:
            regime, play = "calm-pin", "fade"
        elif vix < VIX_HIGH:
            regime, play = "mixed", "mixed"
        else:
            regime, play = "stressed-trend", "follow"
    return structure, regime, play

def main():
    if not KEY:
        print("FRED_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    d1, vix   = latest("VIXCLS")
    d2, vix3m = latest("VXVCLS")
    ratio = round(vix / vix3m, 4) if vix3m else None
    structure, regime, play = grade(vix, ratio)
    out = {
        "updated": datetime.date.today().isoformat(),
        "vix":   {"series": "VIXCLS", "value": round(vix, 2),   "asof": d1},
        "vix3m": {"series": "VXVCLS", "value": round(vix3m, 2), "asof": d2},
        "ratio": ratio,
        "structure": structure,
        "regime": regime,
        "play": play,
    }
    with open("vix.json", "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
