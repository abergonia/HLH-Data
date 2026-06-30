"""
Kalshi API Data Pull
====================
Pulls fresh prediction-market data from Kalshi's public API and writes
kalshi_data.json. Designed to run on a GitHub Actions runner (which can
reach the Kalshi API), so nothing has to run on a work machine.

No API key needed for market data. Standard library only (no pip installs).
Works with Python 3.7+.

What was fixed vs. the original script:
  1. Base URL  -> external-api.kalshi.com (the public read host; the old
     api.kalshi.com host returns errors / is not the right endpoint).
  2. Field names -> the API now exposes *_dollars / *_fp variants. This
     script captures BOTH the legacy and current field names and computes
     a normalized probability + volume so the briefing works either way.
  3. Series ticker -> Fed series is KXFED (was FED).

If a series comes back empty, the ticker may have changed — the run log
prints exactly which series returned no markets so it is easy to adjust.
"""

import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone
import os

# FIX #1: correct public API host
BASE_URL = "https://external-api.kalshi.com/trade-api/v2"

# Series we care about. (series_ticker, friendly_name)
# FIX #3: FED -> KXFED. Others kept; the run log flags any that return empty.
SERIES = [
    ("KXFED",       "Fed Funds Rate"),
    ("KXWTI",       "WTI Crude Oil (weekly)"),
    ("KXCPI",       "CPI / Inflation"),
  ("KXRECSSNBER", "Recession Probability"),
    ("KXPAYROLLS",  "Jobs Report / Nonfarm Payrolls"),
    ("KXTNOTED",    "10Y Treasury Yield (daily)"),
    ("KXTNOTEW",    "10Y Treasury Yield (weekly)"),
    ("KXGDP",       "GDP Growth (quarterly)"),
    ("KXGDPYEAR",   "GDP Growth (annual)"),
    ("KXTARIFF",    "Tariff / Trade Policy"),
]


def api_get(path, params=None):
    """GET request to Kalshi public API. No auth needed for market data."""
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "HLH-Briefing/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  Warning: could not fetch {path}: {e}")
        return None


def _first_not_none(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


def _to_num(v):
    """Coerce an API value (which may arrive as a string) to a number, else None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        s = str(v).replace(",", "").strip()
        if s == "":
            return None
        f = float(s)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return None


def _normalize_probability(last_price, last_price_dollars, yes_bid, yes_ask):
    """Return an integer probability 0-100, regardless of API field schema.

    - Legacy: last_price is in CENTS (72 == 72%).
    - Current: last_price_dollars is in DOLLARS (0.72 == 72%).
    Falls back to the yes bid/ask midpoint if last price is missing.
    """
    if last_price_dollars is not None:
        try:
            return round(float(last_price_dollars) * 100)
        except (TypeError, ValueError):
            pass
    if last_price is not None:
        try:
            lp = float(last_price)
            # If it looks like dollars (<= 1), scale up; else assume cents.
            return round(lp * 100) if lp <= 1 else round(lp)
        except (TypeError, ValueError):
            pass
    # Fallback: midpoint of yes bid/ask (these are typically in cents)
    bid, ask = yes_bid, yes_ask
    if bid is not None and ask is not None:
        try:
            return round((float(bid) + float(ask)) / 2)
        except (TypeError, ValueError):
            pass
    return None


def pull_markets_for_series(series_ticker):
    """Pull open markets for a series, following pagination cursors."""
    markets = []
    cursor = None
    for _ in range(10):  # safety cap on pages
        params = {"series_ticker": series_ticker, "status": "open", "limit": 100}
        if cursor:
            params["cursor"] = cursor
        data = api_get("/markets", params)
        if not data or "markets" not in data:
            break
        for m in data["markets"]:
            last_price = m.get("last_price")
            last_price_dollars = m.get("last_price_dollars")
            volume = _to_num(_first_not_none(m.get("volume"), m.get("volume_fp")))
            open_interest = _to_num(_first_not_none(m.get("open_interest"), m.get("open_interest_fp")))
            markets.append({
                "ticker": m.get("ticker"),
                "title": m.get("title", ""),
                "subtitle": m.get("subtitle", "") or m.get("yes_sub_title", ""),
                "yes_bid": m.get("yes_bid"),
                "yes_ask": m.get("yes_ask"),
                "no_bid": m.get("no_bid"),
                "no_ask": m.get("no_ask"),
                # raw fields (both schemas) for transparency / debugging
                "last_price": last_price,
                "last_price_dollars": last_price_dollars,
                "volume": m.get("volume"),
                "volume_fp": m.get("volume_fp"),
                "open_interest": m.get("open_interest"),
                "open_interest_fp": m.get("open_interest_fp"),
                # normalized fields the briefing can rely on directly
                "probability_pct": _normalize_probability(
                    last_price, last_price_dollars, m.get("yes_bid"), m.get("yes_ask")),
                "volume_num": volume,
                "open_interest_num": open_interest,
                "close_time": m.get("close_time"),
                "expiration_time": m.get("expiration_time"),
            })
        cursor = data.get("cursor")
        if not cursor:
            break
    # Sort by volume (desc) so the most-traded contracts come first
    markets.sort(key=lambda x: (x.get("volume_num") or 0), reverse=True)
    return markets


def main():
    print("=" * 50)
    print("Kalshi API Data Pull")
    print("=" * 50)

    results = {
        "pulled_at": datetime.now(timezone.utc).isoformat(),
        "pulled_at_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "base_url": BASE_URL,
        "series": {},
    }

    empty = []
    for series_ticker, friendly_name in SERIES:
        print(f"Pulling {friendly_name} ({series_ticker})...")
        markets = pull_markets_for_series(series_ticker)
        results["series"][series_ticker] = {
            "name": friendly_name,
            "market_count": len(markets),
            "markets": markets,
        }
        if markets:
            total_vol = sum((m.get("volume_num") or 0) for m in markets)
            print(f"  Found {len(markets)} open markets, total volume: {total_vol:,}")
        else:
            empty.append(f"{friendly_name} ({series_ticker})")
            print(f"  No open markets found — series ticker may have changed.")

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kalshi_data.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    print()
    print(f"Data saved to: {out_path}")
    print(f"Pulled at (UTC): {results['pulled_at']}")
    if empty:
        print()
        print("Series returning NO data (check tickers on kalshi.com):")
        for e in empty:
            print(f"  - {e}")
    print("=" * 50)


if __name__ == "__main__":
    main()
