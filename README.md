# Benchmark Desk

A zero-cost standalone dashboard for the freely-sourceable overnight benchmark
rates plus gold and oil spot. A daily job fetches the values; a static page
plots them. No paid API keys, no always-on server.

**Sourced here (free):** SOFR, EFFR (New York Fed) · ESTR (ECB) · SONIA (Bank of
England) · TONA (Bank of Japan) · Gold, WTI, Brent spot (Stooq).

**Deliberately excluded (licensed / redistribution-restricted):** Term SOFR,
Term SONIA, EURIBOR, SARON, the LBMA gold AM/PM fixes, and exchange settlement
prices. Your TradingView engine already covers those as on-chart time-window
proxies, which need no feed. Don't publish those values from here.

---

## Files

| File | What it does |
|------|--------------|
| `fetch_rates.py` | Pulls each source, writes `data/rates.json` + appends `data/history.csv` |
| `index.html` | The dashboard — reads the two data files, plots with Lightweight Charts |
| `requirements.txt` | Python deps (just `requests`) |
| `.github/workflows/update.yml` | Daily GitHub Action that runs the fetcher and commits the data |
| `data/rates.json`, `data/history.csv` | Sample data so the page renders before the first real run |

---

## Setup steps

1. **Create the repo.** Put these files in a new GitHub repository and clone it locally.
2. **Install locally:** Python 3, then `pip install -r requirements.txt`.
3. **Verify the two flagged sources.** `SOFR`, `EFFR`, `ESTR`, and the Stooq
   commodities are solid. **`SONIA` and `TONA` are the two that may need a
   tweak** — open `fetch_rates.py`, confirm the Bank of England CSV URL still
   returns data, and wire up a current free `TONA` source in `fetch_tona()`
   (it ships as a clearly-marked stub).
4. **Run it:** `python fetch_rates.py`. Confirm `data/rates.json` fills in and
   the console reports how many sources came back fresh. A failed source keeps
   its last-known value from `history.csv` and is flagged on the dashboard
   rather than crashing the run.
5. **Preview the page:** from the project folder run `python -m http.server`
   and open `http://localhost:8000`. (Open via a server, not the `file://`
   path, or the `fetch()` calls are blocked.)
6. **Automate the fetch:** the included workflow runs every weekday at 13:05 UTC
   and on demand. Push it, then trigger it once from the repo's **Actions** tab
   to confirm it runs green and commits `data/`.
7. **Add any API keys as Secrets,** not in code, if you later swap in a source
   that needs one. Read them from the workflow environment.
8. **Host it free:** enable **GitHub Pages** on the repo (Settings → Pages →
   deploy from branch), or connect Cloudflare Pages / Netlify. Open the
   published URL.

## Maintain

- Official sites occasionally change formats or paths, which breaks a parser.
  When a tile reads **source down**, fix that one fetcher function.
- A tile reads **stale** when its date is older than `STALE_DAYS` (default 5)
  in `index.html`. Over long weekends/holidays a 3–4 day gap is normal.
- Stay inside the licensing line: only the free series above are safe to display
  publicly.

## Notes

- `data/history.csv` is long format (`date,series,value`). The page de-dupes by
  date (last value per date wins), so running the fetcher more than once a day
  is harmless.
- Rates show as `%`, commodities as `$`. Click any tile to chart its history;
  the theme toggle (top right) flips dark/desk and light.
