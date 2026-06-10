#!/usr/bin/env python3
"""
fetch_rates.py - pull free benchmark rates + commodity spot prices, then write
data/rates.json (latest snapshot) and append data/history.csv (long format).

WHAT IS SOURCED HERE (free, no paid keys):
  SOFR, EFFR          -> New York Fed Markets API   (solid, no key, JSON)
  ESTR                -> ECB Data Portal SDMX API    (solid, no key, JSON)
  SONIA               -> Bank of England IADB CSV     (works; verify the URL)
  TONA                -> Bank of Japan                (stub; wire up + verify)
  Gold / WTI / Brent  -> Stooq CSV spot               (solid, no key; unofficial)

DELIBERATELY NOT INCLUDED (redistribution is licensed/restricted - do not
publish these even though you can see them): Term SOFR, Term SONIA, EURIBOR,
SARON, the LBMA gold AM/PM fixes, and exchange settlement prices. Your engine
already handles those as on-chart time-window proxies, which need no feed.

DESIGN: every fetcher returns (float value, str date) or raises. A raise is
caught, logged, and the last-known value from history.csv is reused so one
broken source never blanks the dashboard. The two flagged sources (SONIA, TONA)
are the only ones likely to need a tweak when a site changes its format.
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


# --- Bank of England: SONIA (series IUDSOIA). Free CSV. VERIFY this URL. -------
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


# --- Bank of Japan: TONA. Least reliable free path; wire up + verify. ---------
def fetch_tona():
    # BOJ's time-series CSV endpoint/codes shift periodically and there is no
    # clean public REST API. Find a current free TONA source, parse it here,
    # and return (value, "YYYY-MM-DD"). Until then this raises, and the script
    # keeps the last-known value (flagging it stale on the dashboard).
    raise NotImplementedError("Wire up a current free BOJ TONA source")


# --- Stooq: commodity spot close. Free CSV, no key (unofficial). ---------------
def fetch_stooq(symbol):
    url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlc&h&e=csv"
    rows = list(csv.reader(io.StringIO(_get(url).text)))
    rec = dict(zip([h.lower() for h in rows[0]], rows[1]))
    return float(rec["close"]), rec["date"]


# name, unit, source label, fetcher
SOURCES = [
    ("SOFR",   "%", "NY Fed", lambda: fetch_nyfed("sofr", True)),
    ("EFFR",   "%", "NY Fed", lambda: fetch_nyfed("effr", False)),
    ("ESTR",   "%", "ECB",    fetch_estr),
    ("SONIA",  "%", "BoE",    fetch_sonia),
    ("TONA",   "%", "BoJ",    fetch_tona),
    ("XAUUSD", "$", "Stooq",  lambda: fetch_stooq("xauusd")),
    ("WTI",    "$", "Stooq",  lambda: fetch_stooq("cl.f")),
    ("BRENT",  "$", "Stooq",  lambda: fetch_stooq("cb.f")),
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
