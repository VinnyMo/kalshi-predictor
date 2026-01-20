#!/usr/bin/env python3
"""
Crypto Market Data Collector for Kalshi

Collects data every 60 seconds for KXSOL15M, KXETH15M, and KXBTC15M markets.
Tracks odds at different points during the 15-minute cycle.
ALSO tracks how each market resolves to calculate actual win rates.
"""

import os
import csv
import time
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
import base64

load_dotenv()

# Import SQLite adapter for Priceline Negotiator
try:
    from sqlite_adapter import get_db
    SQLITE_ENABLED = True
except ImportError:
    SQLITE_ENABLED = False
    print("Warning: SQLite adapter not found. Only CSV output enabled.")

# Config
API_KEY_ID = os.getenv("KALSHI_API_KEY_ID", "")
PRIVATE_KEY_RAW = os.getenv("KALSHI_PRIVATE_KEY", "").replace("\\n", "\n")
BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

# Markets to track (series tickers)
SERIES_TICKERS = ["KXSOL15M", "KXETH15M", "KXBTC15M"]

# Data directory
DATA_DIR = Path(__file__).parent / "data" / "crypto_analysis"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# CSV file for collected observations
CSV_FILE = DATA_DIR / "market_observations.csv"
CSV_FIELDS = [
    "observation_id",      # Unique ID for this observation
    "timestamp",           # When we captured this data
    "series_ticker",       # KXSOL15M, KXETH15M, KXBTC15M
    "market_ticker",       # Full market ticker (unique per 15-min cycle)
    "title",
    "close_time",
    "seconds_to_close",
    "minutes_to_close",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
    "spread",
    "volume",
    "open_interest",
    "last_price",
    # Result fields - filled in after market settles
    "result",              # "yes", "no", or "" if not yet settled
    "result_updated_at",   # When we captured the result
]

# JSON file to track markets we're monitoring for results
PENDING_MARKETS_FILE = DATA_DIR / "pending_markets.json"

# JSON file for settled market results
RESULTS_FILE = DATA_DIR / "market_results.json"


class KalshiClient:
    """Simple API client for data collection."""

    def __init__(self):
        self.private_key = serialization.load_pem_private_key(
            PRIVATE_KEY_RAW.encode("utf-8"),
            password=None,
            backend=default_backend()
        )
        self.session = requests.Session()

    def _sign_request(self, timestamp: str, method: str, path: str) -> str:
        message = f"{timestamp}{method}{path}".encode("utf-8")
        signature = self.private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        return base64.b64encode(signature).decode("utf-8")

    def get(self, endpoint: str, params: dict = None) -> dict:
        timestamp = str(int(time.time() * 1000))
        path = f"/trade-api/v2{endpoint}"
        signature = self._sign_request(timestamp, "GET", path)

        headers = {
            "KALSHI-ACCESS-KEY": API_KEY_ID,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        response = self.session.get(f"{BASE_URL}{endpoint}", headers=headers, params=params, timeout=30)

        if response.status_code != 200:
            print(f"API Error {response.status_code}: {response.text[:200]}")
            return {}

        return response.json()

    def get_markets_by_series(self, series_ticker: str, status: str = "open") -> list:
        """Get markets for a series."""
        result = self.get("/markets", params={
            "series_ticker": series_ticker,
            "status": status,
            "limit": 50
        })
        return result.get("markets", [])

    def get_market(self, ticker: str) -> dict:
        """Get single market details."""
        result = self.get(f"/markets/{ticker}")
        return result.get("market", {})


def parse_timestamp(ts_str: str) -> datetime:
    """Parse ISO timestamp."""
    if not ts_str:
        return None
    ts_str = ts_str.replace("Z", "+00:00")
    return datetime.fromisoformat(ts_str)


def load_pending_markets() -> dict:
    """Load markets we're waiting for results on."""
    if PENDING_MARKETS_FILE.exists():
        with open(PENDING_MARKETS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_pending_markets(pending: dict):
    """Save pending markets."""
    with open(PENDING_MARKETS_FILE, "w") as f:
        json.dump(pending, f, indent=2)


def load_results() -> dict:
    """Load settled market results."""
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_results(results: dict):
    """Save market results."""
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2)


def init_csv():
    """Initialize CSV file with headers if it doesn't exist."""
    if not CSV_FILE.exists():
        with open(CSV_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()
        print(f"Created CSV file: {CSV_FILE}")


def append_to_csv(row: dict):
    """Append a row to the CSV file."""
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writerow(row)


def update_csv_results(market_ticker: str, result: str):
    """Update all observations for a market with its result."""
    if not CSV_FILE.exists():
        return 0

    # Read all rows
    rows = []
    with open(CSV_FILE, "r", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Update matching rows
    updated = 0
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        if row["market_ticker"] == market_ticker and not row.get("result"):
            row["result"] = result
            row["result_updated_at"] = now
            updated += 1

    # Write back
    with open(CSV_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    return updated


def check_settled_markets(client: KalshiClient, pending: dict, results: dict) -> tuple[dict, dict, int]:
    """
    Check pending markets for settlement results.
    Returns updated (pending, results, count_settled).
    """
    settled_count = 0
    now = datetime.now(timezone.utc)

    markets_to_remove = []

    for ticker, info in pending.items():
        close_time = parse_timestamp(info["close_time"])

        # Only check if market should have closed (add 2 min buffer for settlement)
        if close_time and now > close_time + timedelta(minutes=2):
            try:
                market = client.get_market(ticker)

                status = market.get("status", "").lower()
                # Kalshi uses "finalized" for settled markets
                if status in ["settled", "finalized"]:
                    result = market.get("result", "").lower()  # "yes" or "no"

                    if result in ["yes", "no"]:
                        # Record result
                        results[ticker] = {
                            "result": result,
                            "settled_at": now.isoformat(),
                            "series": info["series"],
                            "close_time": info["close_time"],
                        }

                        # Update CSV observations
                        updated = update_csv_results(ticker, result)

                        # Also update SQLite for Priceline Negotiator
                        if SQLITE_ENABLED:
                            try:
                                db = get_db()
                                db.insert_market_result({
                                    "market_ticker": ticker,
                                    "series_ticker": info["series"],
                                    "result": result,
                                    "close_time": info["close_time"],
                                    "settled_at": now.isoformat()
                                })
                                db.update_observation_result(ticker, result)
                            except Exception as e:
                                pass

                        print(f"  SETTLED: {ticker} -> {result.upper()} (updated {updated} observations)")
                        markets_to_remove.append(ticker)
                        settled_count += 1

                elif status == "closed":
                    # Market closed but not yet finalized, keep waiting
                    pass

            except Exception as e:
                print(f"  Error checking {ticker}: {e}")

    # Remove settled markets from pending
    for ticker in markets_to_remove:
        del pending[ticker]

    return pending, results, settled_count


def collect_data(client: KalshiClient, pending: dict) -> tuple[int, dict]:
    """
    Collect data for all tracked markets.
    Returns (observations_count, updated_pending).
    """
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()
    observation_base = int(now.timestamp() * 1000)

    collected = 0

    for series in SERIES_TICKERS:
        try:
            markets = client.get_markets_by_series(series, status="open")

            if not markets:
                print(f"  {series}: No open markets found")
                continue

            for market in markets:
                ticker = market.get("ticker", "")
                close_time_str = market.get("close_time", "")
                close_time = parse_timestamp(close_time_str)

                if not close_time:
                    continue

                # Skip if already closed
                if close_time < now:
                    continue

                seconds_to_close = (close_time - now).total_seconds()
                minutes_to_close = seconds_to_close / 60

                # Extract price data
                yes_bid = market.get("yes_bid")
                yes_ask = market.get("yes_ask")
                no_bid = market.get("no_bid")
                no_ask = market.get("no_ask")

                # Calculate spread
                spread = None
                if yes_bid and yes_ask:
                    spread = yes_ask - yes_bid

                # Generate unique observation ID
                obs_id = f"{observation_base}_{ticker}"

                row = {
                    "observation_id": obs_id,
                    "timestamp": timestamp,
                    "series_ticker": series,
                    "market_ticker": ticker,
                    "title": market.get("title", "")[:100],
                    "close_time": close_time_str,
                    "seconds_to_close": round(seconds_to_close, 1),
                    "minutes_to_close": round(minutes_to_close, 2),
                    "yes_bid": yes_bid,
                    "yes_ask": yes_ask,
                    "no_bid": no_bid,
                    "no_ask": no_ask,
                    "spread": spread if spread else "",
                    "volume": market.get("volume", 0),
                    "open_interest": market.get("open_interest", 0),
                    "last_price": market.get("last_price", ""),
                    "result": "",  # Will be updated when market settles
                    "result_updated_at": "",
                }

                append_to_csv(row)

                # Also write to SQLite for Priceline Negotiator dashboard
                if SQLITE_ENABLED:
                    try:
                        db = get_db()
                        db.insert_market_observation(row)
                    except Exception as e:
                        # Don't let SQLite errors stop CSV collection
                        pass

                collected += 1

                # Add to pending markets if not already there
                if ticker not in pending:
                    pending[ticker] = {
                        "series": series,
                        "close_time": close_time_str,
                        "first_seen": timestamp,
                    }

                # Only print detailed info every 10 seconds to reduce noise
                if int(time.time()) % 10 == 0:
                    print(f"  {series}: {minutes_to_close:.1f}m | YES {yes_bid}/{yes_ask} | NO {no_bid}/{no_ask}")

        except Exception as e:
            print(f"  {series}: Error - {e}")

    return collected, pending


def print_stats(results: dict, pending: dict):
    """Print statistics about collected data."""
    if results:
        yes_wins = sum(1 for r in results.values() if r["result"] == "yes")
        no_wins = sum(1 for r in results.values() if r["result"] == "no")
        print(f"\n  Results so far: {len(results)} settled ({yes_wins} YES, {no_wins} NO)")

    if pending:
        print(f"  Pending markets: {len(pending)}")


def main():
    """Main collection loop."""
    print("=" * 70)
    print("Kalshi Crypto Market Data Collector (with Result Tracking)")
    print("=" * 70)
    print(f"Tracking: {', '.join(SERIES_TICKERS)}")
    print(f"Interval: 1 second (3 requests/sec, limit is 20/sec)")
    print(f"Data file: {CSV_FILE}")
    print(f"Results file: {RESULTS_FILE}")
    print(f"SQLite output: {'Enabled (Priceline Negotiator)' if SQLITE_ENABLED else 'Disabled'}")
    print()
    print("This collector tracks:")
    print("  1. Odds snapshots every SECOND for fine-grained analysis")
    print("  2. Final results when markets settle")
    print("  3. Links each observation to outcome for win-rate analysis")
    print()
    print("Press Ctrl+C to stop")
    print("=" * 70)

    init_csv()
    client = KalshiClient()

    # Load existing state
    pending = load_pending_markets()
    results = load_results()

    print(f"\nLoaded state: {len(pending)} pending, {len(results)} settled")

    # Test connection first
    print("\nTesting API connection...")
    test = client.get("/markets", params={"limit": 1})
    if not test:
        print("Failed to connect to API. Check credentials.")
        return
    print("Connection successful!\n")

    cycle = 0
    total_observations = 0
    total_settled = 0

    try:
        while True:
            cycle += 1
            now = datetime.now()
            # Only print cycle header every 10 seconds
            if cycle % 10 == 1:
                print(f"\n[{now.strftime('%H:%M:%S')}] Cycles {cycle}-{cycle+9}")

            # Collect new observations
            collected, pending = collect_data(client, pending)
            total_observations += collected

            # Check for settled markets
            pending, results, settled = check_settled_markets(client, pending, results)
            total_settled += settled

            # Save state
            save_pending_markets(pending)
            save_results(results)

            # Compact output for 1-second intervals
            if settled > 0:
                print(f"  >> SETTLED {settled} markets this cycle")

            # Print summary every 60 cycles (1 minute)
            if cycle % 60 == 0:
                print(f"\n  [1-min summary] Observations: {total_observations} | Settled: {total_settled} | Pending: {len(pending)}")
                print_stats(results, pending)

            # Wait 1 second
            time.sleep(1)

    except KeyboardInterrupt:
        # Save state before exit
        save_pending_markets(pending)
        save_results(results)

        print(f"\n\n{'='*70}")
        print("Collection stopped.")
        print(f"Total observations: {total_observations}")
        print(f"Total settled markets: {total_settled}")
        print(f"Pending markets: {len(pending)}")
        print(f"\nData saved to: {CSV_FILE}")
        print(f"Results saved to: {RESULTS_FILE}")
        print("=" * 70)


if __name__ == "__main__":
    main()
