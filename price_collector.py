#!/usr/bin/env python3
"""
Cryptocurrency Price Collector for Kalshi Trading

Collects real-time BTC, ETH, and SOL prices from CoinGecko API.
Runs alongside the Kalshi market collector to enable correlation analysis.

Usage:
    python price_collector.py

Rate Limits (CoinGecko Demo):
    - 30 calls/minute
    - 10,000 calls/month
    - We use 2 calls/minute (every 30 seconds) = ~86,400/month
    - Well within limits, but consider upgrading if needed
"""

import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
import requests

load_dotenv()

# Import SQLite adapter for Priceline Negotiator
try:
    from sqlite_adapter import get_db
    SQLITE_ENABLED = True
except ImportError:
    SQLITE_ENABLED = False
    print("Warning: SQLite adapter not found. Only JSON output enabled.")

# CoinGecko API configuration
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"

# Cryptocurrencies to track (matching Kalshi markets)
CRYPTO_IDS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL"
}

# Collection interval in seconds (30 = 2 calls/minute, well under 30/min limit)
COLLECTION_INTERVAL = 30

# Data directory
DATA_DIR = Path(__file__).parent / "data" / "crypto_prices"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Price history file
PRICES_FILE = DATA_DIR / "prices.json"

# Statistics file
STATS_FILE = DATA_DIR / "collection_stats.json"


def load_prices() -> dict:
    """Load existing price history."""
    if PRICES_FILE.exists():
        with open(PRICES_FILE, "r") as f:
            return json.load(f)
    return {"observations": [], "metadata": {}}


def save_prices(data: dict):
    """Save price history."""
    with open(PRICES_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_stats() -> dict:
    """Load collection statistics."""
    if STATS_FILE.exists():
        with open(STATS_FILE, "r") as f:
            return json.load(f)
    return {
        "total_observations": 0,
        "api_errors": 0,
        "started_at": None,
        "last_observation": None
    }


def save_stats(stats: dict):
    """Save collection statistics."""
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f, indent=2)


def fetch_prices() -> dict | None:
    """
    Fetch current prices from CoinGecko API.
    Returns dict with prices or None on error.
    """
    try:
        # Build request URL
        ids = ",".join(CRYPTO_IDS.keys())
        url = f"{COINGECKO_BASE_URL}/simple/price"

        params = {
            "ids": ids,
            "vs_currencies": "usd",
            "include_last_updated_at": "true",
            "include_24hr_change": "true"
        }

        # Add API key if available (increases rate limit stability)
        if COINGECKO_API_KEY:
            params["x_cg_demo_api_key"] = COINGECKO_API_KEY

        headers = {
            "Accept": "application/json",
            "User-Agent": "KalshiTradingBot/1.0"
        }

        response = requests.get(url, params=params, headers=headers, timeout=10)

        if response.status_code == 429:
            print("  Rate limited by CoinGecko. Waiting 60 seconds...")
            time.sleep(60)
            return None

        if response.status_code != 200:
            print(f"  API Error {response.status_code}: {response.text[:100]}")
            return None

        return response.json()

    except requests.exceptions.Timeout:
        print("  Request timeout")
        return None
    except requests.exceptions.RequestException as e:
        print(f"  Request error: {e}")
        return None
    except json.JSONDecodeError:
        print("  Invalid JSON response")
        return None


def process_prices(raw_data: dict) -> dict:
    """
    Process raw CoinGecko response into our storage format.
    """
    now = datetime.now(timezone.utc)

    observation = {
        "timestamp": now.isoformat(),
        "unix_timestamp": int(now.timestamp()),
        "prices": {}
    }

    for coin_id, symbol in CRYPTO_IDS.items():
        if coin_id in raw_data:
            coin_data = raw_data[coin_id]
            observation["prices"][symbol] = {
                "usd": coin_data.get("usd"),
                "usd_24h_change": coin_data.get("usd_24h_change"),
                "source_updated_at": coin_data.get("last_updated_at")
            }

    # Also write to SQLite for Priceline Negotiator dashboard
    if SQLITE_ENABLED:
        try:
            db = get_db()
            prices = observation["prices"]
            db.insert_price_observation({
                "timestamp": observation["timestamp"],
                "unix_timestamp": observation["unix_timestamp"],
                "btc_usd": prices.get("BTC", {}).get("usd"),
                "btc_24h_change": prices.get("BTC", {}).get("usd_24h_change"),
                "eth_usd": prices.get("ETH", {}).get("usd"),
                "eth_24h_change": prices.get("ETH", {}).get("usd_24h_change"),
                "sol_usd": prices.get("SOL", {}).get("usd"),
                "sol_24h_change": prices.get("SOL", {}).get("usd_24h_change"),
            })
        except Exception as e:
            # Don't let SQLite errors stop JSON collection
            pass

    return observation


def calculate_price_changes(observations: list) -> dict:
    """
    Calculate recent price changes for display.
    """
    if len(observations) < 2:
        return {}

    latest = observations[-1]["prices"]

    changes = {}

    # Find observations from ~1 min, ~5 min, ~15 min ago
    lookbacks = {
        "1m": 2,    # 2 observations * 30s = 1 min
        "5m": 10,   # 10 observations * 30s = 5 min
        "15m": 30   # 30 observations * 30s = 15 min
    }

    for label, idx in lookbacks.items():
        if len(observations) > idx:
            past = observations[-idx-1]["prices"]
            changes[label] = {}
            for symbol in CRYPTO_IDS.values():
                if symbol in latest and symbol in past:
                    current = latest[symbol]["usd"]
                    previous = past[symbol]["usd"]
                    if current and previous:
                        pct_change = ((current - previous) / previous) * 100
                        changes[label][symbol] = round(pct_change, 3)

    return changes


def trim_old_observations(observations: list, max_hours: int = 48) -> list:
    """
    Keep only observations from the last N hours to prevent unbounded growth.
    """
    if not observations:
        return observations

    cutoff = datetime.now(timezone.utc).timestamp() - (max_hours * 3600)

    return [obs for obs in observations if obs.get("unix_timestamp", 0) > cutoff]


def print_status(observation: dict, changes: dict, stats: dict):
    """Print current status line."""
    prices = observation["prices"]

    # Format prices
    btc = prices.get("BTC", {}).get("usd", 0)
    eth = prices.get("ETH", {}).get("usd", 0)
    sol = prices.get("SOL", {}).get("usd", 0)

    status = f"  BTC: ${btc:,.2f}"
    status += f" | ETH: ${eth:,.2f}"
    status += f" | SOL: ${sol:,.2f}"

    # Add 15-minute change if available (matches Kalshi market window)
    if "15m" in changes:
        ch = changes["15m"]
        btc_ch = ch.get("BTC", 0)
        eth_ch = ch.get("ETH", 0)
        sol_ch = ch.get("SOL", 0)

        def fmt_change(val):
            if val > 0:
                return f"+{val:.2f}%"
            return f"{val:.2f}%"

        status += f"  [15m: BTC {fmt_change(btc_ch)}, ETH {fmt_change(eth_ch)}, SOL {fmt_change(sol_ch)}]"

    print(status)


def main():
    """Main collection loop."""
    print("=" * 70)
    print("Cryptocurrency Price Collector (CoinGecko)")
    print("=" * 70)
    print(f"Tracking: {', '.join(CRYPTO_IDS.values())}")
    print(f"Interval: {COLLECTION_INTERVAL} seconds")
    print(f"Data file: {PRICES_FILE}")
    print(f"SQLite output: {'Enabled (Priceline Negotiator)' if SQLITE_ENABLED else 'Disabled'}")

    if COINGECKO_API_KEY:
        print(f"API Key: {COINGECKO_API_KEY[:8]}... (configured)")
    else:
        print("API Key: Not configured (using public rate limits)")

    print()
    print("This collector tracks:")
    print("  - Real crypto prices every 30 seconds")
    print("  - Price changes over 1m, 5m, 15m windows")
    print("  - Data for correlation with Kalshi market outcomes")
    print()
    print("Press Ctrl+C to stop")
    print("=" * 70)

    # Load existing data
    price_data = load_prices()
    stats = load_stats()

    if not stats["started_at"]:
        stats["started_at"] = datetime.now(timezone.utc).isoformat()

    observations = price_data.get("observations", [])
    print(f"\nLoaded {len(observations)} existing observations")

    # Test connection
    print("\nTesting CoinGecko API connection...")
    test_data = fetch_prices()
    if test_data:
        print("Connection successful!")
        for coin_id, symbol in CRYPTO_IDS.items():
            if coin_id in test_data:
                price = test_data[coin_id].get("usd", "N/A")
                print(f"  {symbol}: ${price:,.2f}" if isinstance(price, (int, float)) else f"  {symbol}: {price}")
    else:
        print("Warning: Initial fetch failed. Will retry...")

    print()
    cycle = 0

    try:
        while True:
            cycle += 1
            now = datetime.now()

            # Fetch prices
            raw_data = fetch_prices()

            if raw_data:
                # Process and store
                observation = process_prices(raw_data)
                observations.append(observation)

                # Trim old data periodically (every 100 cycles)
                if cycle % 100 == 0:
                    observations = trim_old_observations(observations)

                # Calculate changes
                changes = calculate_price_changes(observations)

                # Update stats
                stats["total_observations"] += 1
                stats["last_observation"] = observation["timestamp"]

                # Print status every cycle
                print(f"[{now.strftime('%H:%M:%S')}] #{stats['total_observations']}", end="")
                print_status(observation, changes, stats)

                # Save data
                price_data["observations"] = observations
                price_data["metadata"] = {
                    "last_updated": observation["timestamp"],
                    "observation_count": len(observations),
                    "collection_interval_seconds": COLLECTION_INTERVAL
                }
                save_prices(price_data)
                save_stats(stats)

            else:
                stats["api_errors"] += 1
                print(f"[{now.strftime('%H:%M:%S')}] Fetch failed (errors: {stats['api_errors']})")
                save_stats(stats)

            # Wait for next collection
            time.sleep(COLLECTION_INTERVAL)

    except KeyboardInterrupt:
        # Final save
        price_data["observations"] = observations
        save_prices(price_data)
        save_stats(stats)

        print(f"\n\n{'='*70}")
        print("Collection stopped.")
        print(f"Total observations: {stats['total_observations']}")
        print(f"API errors: {stats['api_errors']}")
        print(f"Data saved to: {PRICES_FILE}")
        print("=" * 70)


if __name__ == "__main__":
    main()
