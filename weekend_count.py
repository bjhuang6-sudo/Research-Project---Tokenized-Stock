"""
Weekend volume count for tokenized-stock pools on GeckoTerminal.

What it does, for each pool listed in POOLS:
  1. Downloads hourly candles (price + dollar volume) from GeckoTerminal's free API.
  2. Saves the raw candles to raw_candles_<name>.csv  (keep these: they are your raw data).
  3. Keeps only the weekend window: Friday 4 p.m. ET through Sunday 6 p.m. ET.
  4. Totals the dollar volume for each weekend and counts how many weekends
     clear MIN_VOLUME.
  5. Saves weekend_summary_<name>.csv and prints a comparison table.

To run:   pip install requests pandas      (once)
          python weekend_count.py
It takes a few minutes because the API allows only 30 calls per minute.
"""

import time

import pandas as pd
import requests

# ---- Edit these -----------------------------------------------------------
POOLS = {
    "SPYx_RaydiumA_USDC": "4pCZCVEiYyT4efNdXUdL2tJF8VGMgiMXrZWq6FiNXhRw",
    "SPYx_RaydiumB_USDC": "6truu3rZuiB9rKQg4VYC3Dt3QwV7DgwGqXrYUcrvnDDE",
    "SPYx_Byreal_USDC": "27x6aSxcAm6SoazoxmmTtFg6fWMQuNmdKJmHagq1DUZy",
    "SPYx_Orca_USDC": "Fae5dWVntUt6zbWu2voXxioDpMii7SqQwtsxBmoVCsHR",
    "SPYx_RaydiumC_SOL": "BS9uyGV6XmNnPkM4f3xgxCdQEaFv7RSKs6fwrpvYHxfL",
}
MIN_VOLUME = 10_000     # placeholder: dollars of weekend volume that counts as "usable"
MAX_PAGES = 25          # each page is up to 1000 hourly candles
PAUSE_SECONDS = 2.5     # keeps us under 30 calls per minute
# ---------------------------------------------------------------------------

BASE_URL = "https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/hour"
ET = "America/New_York"


def fetch_candles(pool):
    """Download hourly candles for one pool, newest first, paging backwards."""
    url = BASE_URL.format(pool=pool)
    rows, before = [], None
    for _ in range(MAX_PAGES):
        params = {"aggregate": 1, "limit": 1000}
        if before is not None:
            params["before_timestamp"] = before
        response = None
        for attempt in range(4):
            response = requests.get(url, params=params, timeout=30)
            if response.status_code == 429:      # rate limited: wait and retry
                time.sleep(30)
                continue
            break
        response.raise_for_status()
        candles = response.json()["data"]["attributes"]["ohlcv_list"]
        if not candles:
            break
        rows += candles
        oldest = min(c[0] for c in candles)
        if before is not None and oldest >= before:   # no progress: stop
            break
        before = oldest
        time.sleep(PAUSE_SECONDS)
    return rows


def to_frame(rows):
    """Turn raw candle rows into a clean table with Eastern-time columns."""
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    df["et"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(ET)
    # The Monday that starts this calendar week, used to label each weekend.
    df["week"] = (
        df["et"].dt.normalize().dt.tz_localize(None)
        - pd.to_timedelta(df["et"].dt.dayofweek, unit="D")
    ).dt.date
    return df


def summarize(df, min_volume, now=None):
    """One row per finished weekend: dollar volume and number of hours with trades."""
    dow, hour = df["et"].dt.dayofweek, df["et"].dt.hour       # Monday=0 ... Sunday=6
    in_window = ((dow == 4) & (hour >= 16)) | (dow == 5) | ((dow == 6) & (hour < 18))
    weekend = df[in_window]
    summary = weekend.groupby("week").agg(
        usd_volume=("volume", "sum"),
        active_hours=("volume", lambda v: int((v > 0).sum())),
    )
    summary["usable"] = summary["usd_volume"] >= min_volume

    # Drop a weekend that has not finished yet (before Sunday 6 p.m. ET).
    now = now or pd.Timestamp.now(tz=ET)
    keep = []
    for monday in summary.index:
        sunday = monday + pd.Timedelta(days=6)
        finished = sunday < now.date() or (sunday == now.date() and now.hour >= 18)
        keep.append(finished)
    return summary[keep]


def main():
    comparison = []
    for name, pool in POOLS.items():
        print(f"Downloading {name} ...")
        try:
            rows = fetch_candles(pool)
        except Exception as error:               # keep going if one pool fails
            print(f"  FAILED: {error}")
            continue
        if not rows:
            print("  No candles returned.")
            continue
        df = to_frame(rows)
        df.drop(columns=["week"]).to_csv(f"raw_candles_{name}.csv", index=False)
        summary = summarize(df, MIN_VOLUME)
        summary.to_csv(f"weekend_summary_{name}.csv")
        comparison.append({
            "pool": name,
            "first_candle": df["et"].min().date(),
            "weekends_found": len(summary),
            "weekends_with_any_volume": int((summary["usd_volume"] > 0).sum()),
            f"weekends_over_${MIN_VOLUME:,}": int(summary["usable"].sum()),
            "median_weekend_usd": round(float(summary["usd_volume"].median()), 0),
        })
    print()
    if comparison:
        print(pd.DataFrame(comparison).to_string(index=False))
    else:
        print("No results. Check your internet connection and the pool addresses.")


if __name__ == "__main__":
    main()
