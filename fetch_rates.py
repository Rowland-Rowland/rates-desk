#!/usr/bin/env python3
"""
fetch_rates.py - pull free benchmark rates + oil + the Forex Factory high-impact
calendar, then write data/rates.json (latest snapshot) and append
data/history.csv (long format).

SOURCED HERE (free):
  SOFR, EFFR  -> New York Fed Markets API   (solid, no key, JSON)
  ESTR        -> ECB Data Portal SDMX API    (solid, no key, JSON)
  SONIA       -> Bank of England IADB CSV     (works; verify URL if it breaks)
  TONA        -> FRED (Japan overnight call money rate) - MONTHLY proxy for TONA
  WTI, Brent  -> FRED (EIA daily spot)        (official, daily)
  NEWS        -> Forex Factory weekly calendar export (Fair Economy CDN), free,
                 no key. High-impact events only. Carries forecast + previous
                 but NOT the actual (released) value.

FRED needs a FREE api key. Get one at https://fred.stlouisfed.org (My Account
-> API Keys), then add it to the repo as a Secret named FRED_API_KEY. The
GitHub Action passes it in; locally, set it in your shell:  export FRED_API_KEY=xxxx

NEWS BEHAVIOUR
  - Currencies are configurable: set NEWS_CURRENCIES (comma separated) in the
    workflow env to match your Forex Factory filter, e.g. "USD,EUR,GBP,JPY".
  - Only events within +/- NEWS_WINDOW_DAYS of now are kept, so a stale feed can
    never put last week's (or last month's) releases on the page.
  - If the feed looks stale or the request fails, the LAST GOOD calendar is kept
    (not wiped) and "news_as_of" is left at its old time, so the page can flag it.
  - "news_as_of" is the UTC time the calendar was last fetched successfully.

NOT INCLUDED (redistribution licensed): Term SOFR/SONIA, EURIBOR, SARON, gold
spot/LBMA fixes, exchange settlement prices. Gold has no free server-friendly
source, so it is intentionally left out rather than shown broken.

Each rate fetcher returns (float value, str date) or raises. A raise is caught,
logged, and the last-known value from history.csv is reused so one bad source
never blanks the dashboard.
"""

import csv
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

TIMEOUT = 20
HEADERS = {"User-Agent": "rates-desk/1.1 (github actions)"}

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
RATES_JSON = os.path.join(DATA_DIR, "rates.json")
HISTORY_CSV = os.path.join(DATA_DIR, "history.csv")

# Forex Factory weekly calendar export (Fair Economy CDN). Free, no key.
NEWS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_CURRENCIES = {
    c.strip().upper()
    for c in os.environ.get(
        "NEWS_CURRENCIES", "USD,EUR,GBP,JPY,CHF,CAD,AUD,NZD,CNY"
    ).split(",")
    if c.strip()
}
NEWS_WINDOW_DAYS = 8      # keep events within +/- this many days of now
NEWS_RETRIES = 3          # feed rate-limits aggressive polling; retry gently
NEWS_RETRY_DELAY = 5      # seconds, multiplied by attempt number


def _get(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def _get_with_retry(url, tries=NEWS_RETRIES, delay=NEWS_RETRY_DELAY):
    last = None
    for attempt in range(1, tries + 1):
        try:
            return _get(url)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < tries:
                time.sleep(delay * attempt)
    raise last


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


# --- Forex Factory weekly calendar (via Fair Economy). Free, no key. ----------
def _parse_iso(s):
    s = (s or "").strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def fetch_news(now):
    """Return this week's HIGH-impact events for the configured currencies,
    soonest first. Each item: {time (UTC ISO), currency, title, forecast,
    previous}.

    Raises (so main() keeps the last good calendar) if the feed is empty,
    malformed, or looks stale (no event of ANY impact inside the time window).
    """
    raw = _get_with_retry(NEWS_URL).json()
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("calendar feed returned no data")

    lo = now - timedelta(days=NEWS_WINDOW_DAYS)
    hi = now + timedelta(days=NEWS_WINDOW_DAYS)

    in_window = 0
    seen = set()
    events = []
    for e in raw:
        try:
            when = _parse_iso(e.get("date")).astimezone(timezone.utc)
        except (ValueError, TypeError):
            continue
        if not (lo <= when <= hi):
            continue
        in_window += 1
        if (e.get("impact") or "").strip().lower() != "high":
            continue
        cur = (e.get("country") or "").strip().upper()
        if cur not in NEWS_CURRENCIES:
            continue
        title = (e.get("title") or "").strip()
        stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
        key = (stamp, cur, title)
        if key in seen:
            continue
        seen.add(key)
        events.append({
            "time": stamp,
            "currency": cur,
            "title": title,
            "forecast": (e.get("forecast") or "").strip(),
            "previous": (e.get("previous") or "").strip(),
        })

    if in_window == 0:
        raise RuntimeError(
            f"calendar feed looks stale (no events within +/-{NEWS_WINDOW_DAYS} days)"
        )

    events.sort(key=lambda x: x["time"])
    return events


def load_last_news():
    """Previous (news, news_as_of) from rates.json, or ([], None)."""
    try:
        with open(RATES_JSON) as f:
            j = json.load(f)
        news = j.get("news")
        return (news if isinstance(news, list) else []), j.get("news_as_of")
    except Exception:  # noqa: BLE001 - missing/corrupt file is fine on first run
        return [], None


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
    prev_news, prev_news_as_of = load_last_news()
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

    # News: never blocks rates, never wipes the last good calendar.
    try:
        out["news"] = fetch_news(now)
        out["news_as_of"] = out["as_of"]
        print(f"news: {len(out['news'])} high-impact events "
              f"({'/'.join(sorted(NEWS_CURRENCIES))})")
    except Exception as exc:  # noqa: BLE001
        out["news"] = prev_news
        out["news_as_of"] = prev_news_as_of
        print(f"[warn] news: {exc} - keeping last good calendar "
              f"(as of {prev_news_as_of})", file=sys.stderr)

    tmp = RATES_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, indent=2)
    os.replace(tmp, RATES_JSON)

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
