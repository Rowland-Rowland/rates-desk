#!/usr/bin/env python3
"""
fetch_rates.py - pull free benchmark rates + oil, then write data/rates.json
(latest snapshot) and append data/history.csv (long format).

SOURCED HERE (free):
  SOFR, EFFR  -> New York Fed Markets API   (solid, no key, JSON)
  ESTR        -> ECB Data Portal SDMX API    (solid, no key, JSON)
  SONIA       -> Bank of England IADB CSV     (works; verify URL if it breaks)
  TONA        -> FRED (Japan overnight call money rate) - MONTHLY proxy for TONA
  WTI, Brent  -> FRED (EIA daily spot)        (official, daily)

FRED needs a FREE api key. Get one at https://fred.stlouisfed.org (My Account
-> API Keys), then add it to the repo as a Secret named FRED_API_KEY. The
GitHub Action passes it in; locally, set it in your shell:  export FRED_API_KEY=xxxx

NOT INCLUDED (redistribution licensed): Term SOFR/SONIA, EURIBOR, SARON, gold
spot/LBMA fixes, exchange settlement prices. Gold has no free server-friendly
source, so it is intentionally left out rather than shown broken.

Each fetcher returns (float value, str date) or raises. A raise is caught,
logged, and the last-known value from history.csv is reused so one bad source
never blanks the dashboard.
"""

import csv
import io
import json
import os
import sys
from datetime import datetime, timezone

import requests

TIMEOUT = 20
HEADERS = {"User-Agent": "rates-desk/1.0 (github actions)"}

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
RATES_JSON = os.path.join(DATA_DIR, "rates.json")
HISTORY_CSV = os.path.join(DATA_DIR, "history.csv")


def _get(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


# --- New York Fed: SOFR (secured), EFFR (unsecured). Free JSON, no key. -------
def fetch_nyfed(code, secured):
    segment = "secured" if secured else "unsecured"
    url = f"https://markets.newyorkfed.org/api/rates/{segment}/{code}/last/1.json"
    row = _get(url).json()["refRates"][0]
    return float(row["percentRate"]), row["effectiveDate"]


# --- ECB: euro short-term rate (ESTR). Free SDMX-JSON, no key. -----------------
def fetch_estr():
    url = (
        "https://data-api.ecb.europa.eu/service/data/EST/"
        "B.EU000A2X2A25.WT?lastNObservations=1&format=jsondata"
    )
    j = _get(url).json()
    series = j["dataSets"][0]["series"]
    skey = next(iter(series))
    obs = series[skey]["observations"]
    okey = next(iter(obs))
    value = float(obs[okey][0])
    times = j["structure"]["dimensions"]["observation"][0]["values"]
    date = times[int(okey)]["id"]
    return value, date


# --- Bank of England: SONIA (series IUDSOIA). Free CSV. VERIFY if it breaks. ---
def fetch_sonia():
    url = (
        "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
        "?csv.x=yes&Datefrom=01/Jan/2024&Dateto=now&SeriesCodes=IUDSOIA"
        "&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N"
    )
    rows = list(csv.reader(io.StringIO(_get(url).text)))
    data_rows = [r for r in rows[1:] if len(r) >= 2 and r[1].strip()]
    last = data_rows[-1]
    return float(last[1]), last[0]


# --- FRED: free key required. Used for TONA proxy + WTI + Brent. ---------------
def fetch_fred(series_id):
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise RuntimeError("FRED_API_KEY not set - add it as a repo Secret")
    url = (
        "https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={key}&file_type=json"
        "&sort_order=desc&limit=12"
    )
    for o in _get(url).json()["observations"]:
        if o["value"] not in (".", "", None):       # newest first; skip gaps
            return float(o["value"]), o["date"]
    raise RuntimeError("no numeric observation returned")


# name, unit, source label, fetcher
SOURCES = [
    ("SOFR",  "%", "NY Fed", lambda: fetch_nyfed("sofr", True)),
    ("EFFR",  "%", "NY Fed", lambda: fetch_nyfed("effr", False)),
    ("ESTR",  "%", "ECB",    fetch_estr),
    ("SONIA", "%", "BoE",    fetch_sonia),
    ("TONA",  "%", "FRED",   lambda: fetch_fred("IRSTCI01JPM156N")),  # monthly
    ("WTI",   "$", "FRED",   lambda: fetch_fred("DCOILWTICO")),
    ("BRENT", "$", "FRED",   lambda: fetch_fred("DCOILBRENTEU")),
]


def load_last_known():
    last = {}
    if os.path.exists(HISTORY_CSV):
        with open(HISTORY_CSV, newline="") as f:
            for row in csv.DictReader(f):
                try:
                    last[row["series"]] = (float(row["value"]), row["date"])
                except (KeyError, ValueError):
                    continue
    return last


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    last_known = load_last_known()
    now = datetime.now(timezone.utc)
    out = {"as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "rates": {}}
    fresh_rows = []

    for name, unit, source, fn in SOURCES:
        ok = True
        try:
            value, date = fn()
            fresh_rows.append({"date": date, "series": name, "value": value})
        except Exception as exc:  # noqa: BLE001 - one bad source must not stop the run
            ok = False
            print(f"[warn] {name}: {exc}", file=sys.stderr)
            value, date = last_known.get(name, (None, None))
        out["rates"][name] = {
            "value": value, "date": date, "unit": unit,
            "source": source, "ok": ok,
        }

    with open(RATES_JSON, "w") as f:
        json.dump(out, f, indent=2)

    write_header = not os.path.exists(HISTORY_CSV)
    with open(HISTORY_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "series", "value"])
        if write_header:
            w.writeheader()
        for row in fresh_rows:
            w.writerow(row)

    fetched = sum(1 for r in out["rates"].values() if r["ok"])
    print(f"wrote {RATES_JSON} ({fetched}/{len(SOURCES)} sources fresh)")


if __name__ == "__main__":
    main()
