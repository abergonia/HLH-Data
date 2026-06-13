"""
FRED Data Pull
==============
Pulls a curated set of macroeconomic series from the St. Louis Fed (FRED) and
writes fred_data.json. Companion to kalshi_pull.py — runs on the same GitHub
Actions runner, so nothing has to run on a work machine.

No API key needed: this uses FRED's public CSV download endpoint
(https://fred.stlouisfed.org/graph/fredgraph.csv?id=SERIES). Standard library only.

For each series it stores the latest value/date plus the value ~1 week, ~1 month,
and ~1 year earlier, so the briefing can show weekly change (daily series) and
year-over-year change (monthly series like CPI) without storing full history.
"""

import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta, date
import os

CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# Curated series, grouped by relevance to HLH's operating companies.
# (series_id, friendly_name, group)
SERIES = [
    # Rates & policy (briefing core; LCB)
    ("DFEDTARU",     "Fed Funds Target — Upper Bound",        "Rates & Policy"),
    ("DFEDTARL",     "Fed Funds Target — Lower Bound",        "Rates & Policy"),
    ("DGS2",         "2-Year Treasury Yield",                 "Rates & Policy"),
    ("DGS5",         "5-Year Treasury Yield",                 "Rates & Policy"),
    ("DGS10",        "10-Year Treasury Yield",                "Rates & Policy"),
    ("T10Y2Y",       "10Y-2Y Treasury Spread",                "Rates & Policy"),
    ("SOFR",         "SOFR",                                  "Rates & Policy"),
    ("MORTGAGE30US", "30-Year Fixed Mortgage Rate",           "Rates & Policy"),
    ("DPRIME",       "Bank Prime Loan Rate",                  "Rates & Policy"),
    # Inflation & growth
    ("DCOILWTICO",   "WTI Crude Oil (spot)",                  "Inflation & Growth"),
    ("CPIAUCSL",     "CPI — All Items (index)",               "Inflation & Growth"),
    ("CPILFESL",     "Core CPI (index)",                      "Inflation & Growth"),
    ("PCEPILFE",     "Core PCE (index)",                      "Inflation & Growth"),
    ("INDPRO",       "Industrial Production (index)",         "Inflation & Growth"),
    # GFS — storage / throughput
    ("ISRATIO",      "Total Business Inventories-to-Sales",   "Trade & Inventory (GFS)"),
    # HLC — construction / industrial RE
    ("TLNRESCONS",   "Nonresidential Construction Spending",  "Construction & RE (HLC)"),
    ("WPUSI012011",  "PPI — Inputs to Construction",          "Construction & RE (HLC)"),
    # LCB — labor & credit quality
    ("UNRATE",       "Unemployment Rate",                     "Labor & Credit (LCB)"),
    ("DRBLACBS",     "Delinquency Rate — Business Loans",     "Labor & Credit (LCB)"),
    ("DRCLACBS",     "Delinquency Rate — Consumer Loans",     "Labor & Credit (LCB)"),
]


def fetch_csv(series_id, start):
    url = CSV_URL + "?" + urllib.parse.urlencode({"id": series_id, "cosd": start})
    req = urllib.request.Request(url, headers={
        "Accept": "text/csv",
        "User-Agent": "HLH-Briefing/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode()
    except Exception as e:
        print(f"  Warning: could not fetch {series_id}: {e}")
        return None


def parse_observations(csv_text):
    """Return a list of (date, float) tuples, oldest first, skipping missing '.'."""
    obs = []
    if not csv_text:
        return obs
    lines = [l for l in csv_text.splitlines() if l.strip()]
    for line in lines[1:]:  # skip header
        parts = line.split(",")
        if len(parts) < 2:
            continue
        d_str, v_str = parts[0].strip(), parts[1].strip()
        if v_str in (".", "", "NA"):
            continue
        try:
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
            obs.append((d, float(v_str)))
        except ValueError:
            continue
    obs.sort(key=lambda x: x[0])
    return obs


def value_n_days_before(obs, ref_date, n):
    """Value of the most recent observation on/before (ref_date - n days)."""
    target = ref_date - timedelta(days=n)
    chosen = None
    for d, v in obs:
        if d <= target:
            chosen = v
        else:
            break
    return chosen


def main():
    print("=" * 50)
    print("FRED Data Pull")
    print("=" * 50)

    start = (date.today() - timedelta(days=550)).isoformat()  # ~18 months of history
    results = {
        "pulled_at": datetime.now(timezone.utc).isoformat(),
        "pulled_at_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "FRED public CSV (no API key)",
        "series": {},
    }

    empty = []
    for series_id, friendly_name, group in SERIES:
        print(f"Pulling {friendly_name} ({series_id})...")
        obs = parse_observations(fetch_csv(series_id, start))
        if not obs:
            empty.append(f"{friendly_name} ({series_id})")
            results["series"][series_id] = {
                "name": friendly_name, "group": group, "latest_value": None,
            }
            print("  No data returned.")
            continue
        latest_date, latest_value = obs[-1]
        prior_value = obs[-2][1] if len(obs) >= 2 else None
        results["series"][series_id] = {
            "name": friendly_name,
            "group": group,
            "latest_date": latest_date.isoformat(),
            "latest_value": latest_value,
            "prior_value": prior_value,
            "value_1w_ago": value_n_days_before(obs, latest_date, 7),
            "value_1m_ago": value_n_days_before(obs, latest_date, 30),
            "value_1y_ago": value_n_days_before(obs, latest_date, 365),
            "observation_count": len(obs),
        }
        print(f"  Latest {latest_date}: {latest_value}")

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fred_data.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    print()
    print(f"Data saved to: {out_path}")
    print(f"Pulled at (UTC): {results['pulled_at']}")
    if empty:
        print()
        print("Series returning NO data (check the series ID on fred.stlouisfed.org):")
        for e in empty:
            print(f"  - {e}")
    print("=" * 50)


if __name__ == "__main__":
    main()
