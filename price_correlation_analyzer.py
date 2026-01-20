#!/usr/bin/env python3
"""
Price Correlation Analyzer for Kalshi Crypto Trading

Correlates actual cryptocurrency price movements with Kalshi market outcomes.
Generates price-based trading signals that improve as more data is collected.

Key Metrics Analyzed:
1. Price momentum (rate of change over different windows)
2. Price volatility (standard deviation of price changes)
3. Distance to market bracket boundaries
4. Cross-asset correlation (BTC leading ETH/SOL)

Output: price_correlations.json consumed by crypto_autotrader.py
"""

import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

# Data paths
DATA_DIR = Path(__file__).parent / "data"
PRICE_FILE = DATA_DIR / "crypto_prices" / "prices.json"
RESULTS_FILE = DATA_DIR / "crypto_analysis" / "market_results.json"
CORRELATIONS_FILE = DATA_DIR / "crypto_analysis" / "price_correlations.json"

# Crypto mapping (Kalshi series -> CoinGecko symbol)
SERIES_TO_SYMBOL = {
    "KXBTC15M": "BTC",
    "KXETH15M": "ETH",
    "KXSOL15M": "SOL",
}

# Analysis parameters
MOMENTUM_WINDOWS_MINUTES = [1, 3, 5, 10]  # Windows to calculate price momentum
VOLATILITY_WINDOW_MINUTES = 5  # Window for volatility calculation
MIN_OBSERVATIONS_FOR_SIGNAL = 3  # Minimum data points to generate a signal


def load_price_data() -> list[dict]:
    """Load collected price data."""
    if not PRICE_FILE.exists():
        console.print("[red]No price data found. Run price_collector.py first.[/red]")
        return []

    with open(PRICE_FILE, "r") as f:
        data = json.load(f)

    return data.get("observations", [])


def load_market_results() -> dict:
    """Load settled market results."""
    if not RESULTS_FILE.exists():
        console.print("[red]No market results found. Run crypto_collector.py first.[/red]")
        return {}

    with open(RESULTS_FILE, "r") as f:
        return json.load(f)


def parse_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse ISO timestamp string."""
    if not ts_str:
        return None
    try:
        ts_str = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str)
    except ValueError:
        return None


def get_price_at_time(observations: list[dict], target_time: datetime, symbol: str) -> Optional[float]:
    """
    Find the price observation closest to target_time.
    Returns None if no observation within 2 minutes.
    """
    best_obs = None
    best_diff = float('inf')

    for obs in observations:
        obs_time = parse_timestamp(obs.get("timestamp", ""))
        if not obs_time:
            continue

        diff = abs((obs_time - target_time).total_seconds())
        if diff < best_diff:
            best_diff = diff
            best_obs = obs

    # Require observation within 2 minutes
    if best_diff > 120 or not best_obs:
        return None

    prices = best_obs.get("prices", {})
    return prices.get(symbol, {}).get("usd")


def get_prices_in_window(observations: list[dict], end_time: datetime,
                         window_minutes: float, symbol: str) -> list[tuple[datetime, float]]:
    """
    Get all price observations in a time window ending at end_time.
    Returns list of (timestamp, price) tuples.
    """
    start_time = end_time - timedelta(minutes=window_minutes)
    prices = []

    for obs in observations:
        obs_time = parse_timestamp(obs.get("timestamp", ""))
        if not obs_time:
            continue

        if start_time <= obs_time <= end_time:
            price = obs.get("prices", {}).get(symbol, {}).get("usd")
            if price:
                prices.append((obs_time, price))

    return sorted(prices, key=lambda x: x[0])


def calculate_momentum(prices: list[tuple[datetime, float]]) -> Optional[float]:
    """
    Calculate price momentum (percent change from start to end).
    Returns None if insufficient data.
    """
    if len(prices) < 2:
        return None

    start_price = prices[0][1]
    end_price = prices[-1][1]

    if start_price == 0:
        return None

    return ((end_price - start_price) / start_price) * 100


def calculate_volatility(prices: list[tuple[datetime, float]]) -> Optional[float]:
    """
    Calculate price volatility (standard deviation of percent changes).
    Returns None if insufficient data.
    """
    if len(prices) < 3:
        return None

    # Calculate percent changes between consecutive observations
    changes = []
    for i in range(1, len(prices)):
        prev_price = prices[i-1][1]
        curr_price = prices[i][1]
        if prev_price > 0:
            pct_change = ((curr_price - prev_price) / prev_price) * 100
            changes.append(pct_change)

    if len(changes) < 2:
        return None

    # Calculate standard deviation
    mean = sum(changes) / len(changes)
    variance = sum((x - mean) ** 2 for x in changes) / len(changes)
    return math.sqrt(variance)


def extract_bracket_from_ticker(ticker: str, series: str) -> Optional[tuple[float, float]]:
    """
    Extract price bracket from market ticker.

    Ticker format examples:
    - KXBTC15M-26JAN191400-00 (bracket ending in 00)
    - KXETH15M-26JAN191415-15 (bracket ending in 15)

    These encode the expected price range. We need to figure out what
    price ranges Kalshi is using for each crypto.

    Returns (lower_bound, upper_bound) or None if can't determine.
    """
    # For now, we can't reliably extract brackets from tickers alone
    # The actual bracket is in the market title
    # We'll need to get this from the market data when available
    return None


def analyze_market_outcomes(price_data: list[dict], results: dict) -> list[dict]:
    """
    Analyze price conditions around each market outcome.

    For each settled market:
    1. Find the close time
    2. Look up price data around that time
    3. Calculate price metrics (momentum, volatility)
    4. Record outcome with metrics
    """
    analyzed = []

    for ticker, result_info in results.items():
        series = result_info.get("series", "")
        symbol = SERIES_TO_SYMBOL.get(series)

        if not symbol:
            continue

        close_time = parse_timestamp(result_info.get("close_time", ""))
        if not close_time:
            continue

        outcome = result_info.get("result", "").lower()
        if outcome not in ["yes", "no"]:
            continue

        # Calculate metrics at different time points before close
        metrics = {
            "ticker": ticker,
            "series": series,
            "symbol": symbol,
            "close_time": close_time.isoformat(),
            "outcome": outcome,
            "momentum": {},
            "volatility": {},
            "price_at_close": None,
        }

        # Get price at close time
        metrics["price_at_close"] = get_price_at_time(price_data, close_time, symbol)

        # Calculate momentum for different windows
        for window in MOMENTUM_WINDOWS_MINUTES:
            # Get prices in window ending at close time
            prices = get_prices_in_window(price_data, close_time, window, symbol)
            momentum = calculate_momentum(prices)
            if momentum is not None:
                metrics["momentum"][f"{window}m"] = round(momentum, 4)

        # Calculate volatility in the 5-minute window before close
        vol_prices = get_prices_in_window(price_data, close_time, VOLATILITY_WINDOW_MINUTES, symbol)
        volatility = calculate_volatility(vol_prices)
        if volatility is not None:
            metrics["volatility"]["5m"] = round(volatility, 4)

        # Only include if we have at least some price data
        if metrics["price_at_close"] or metrics["momentum"]:
            analyzed.append(metrics)

    return analyzed


def calculate_correlations(analyzed_data: list[dict]) -> dict:
    """
    Calculate correlations between price metrics and outcomes.

    Groups data by:
    - Symbol (BTC, ETH, SOL)
    - Momentum direction (positive, negative, neutral)
    - Volatility level (high, medium, low)

    For each group, calculates YES win rate.
    """
    correlations = {
        "by_momentum": defaultdict(lambda: defaultdict(lambda: {"yes_wins": 0, "total": 0})),
        "by_volatility": defaultdict(lambda: defaultdict(lambda: {"yes_wins": 0, "total": 0})),
        "by_momentum_and_volatility": defaultdict(lambda: defaultdict(lambda: {"yes_wins": 0, "total": 0})),
        "summary": {},
    }

    for item in analyzed_data:
        symbol = item["symbol"]
        outcome = item["outcome"]

        # Categorize by 5-minute momentum
        momentum_5m = item.get("momentum", {}).get("5m")
        if momentum_5m is not None:
            if momentum_5m > 0.1:
                momentum_category = "positive"
            elif momentum_5m < -0.1:
                momentum_category = "negative"
            else:
                momentum_category = "neutral"

            key = f"{symbol}_{momentum_category}"
            correlations["by_momentum"][symbol][momentum_category]["total"] += 1
            if outcome == "yes":
                correlations["by_momentum"][symbol][momentum_category]["yes_wins"] += 1

        # Categorize by volatility
        volatility = item.get("volatility", {}).get("5m")
        if volatility is not None:
            if volatility > 0.1:
                vol_category = "high"
            elif volatility > 0.03:
                vol_category = "medium"
            else:
                vol_category = "low"

            correlations["by_volatility"][symbol][vol_category]["total"] += 1
            if outcome == "yes":
                correlations["by_volatility"][symbol][vol_category]["yes_wins"] += 1

        # Combined momentum + volatility
        if momentum_5m is not None and volatility is not None:
            combined_key = f"{momentum_category}_{vol_category}"
            correlations["by_momentum_and_volatility"][symbol][combined_key]["total"] += 1
            if outcome == "yes":
                correlations["by_momentum_and_volatility"][symbol][combined_key]["yes_wins"] += 1

    # Calculate win rates
    for category_type in ["by_momentum", "by_volatility", "by_momentum_and_volatility"]:
        for symbol in correlations[category_type]:
            for category, stats in correlations[category_type][symbol].items():
                if stats["total"] > 0:
                    stats["yes_win_rate"] = round(stats["yes_wins"] / stats["total"] * 100, 1)

    # Convert defaultdicts to regular dicts for JSON
    correlations["by_momentum"] = {k: dict(v) for k, v in correlations["by_momentum"].items()}
    correlations["by_volatility"] = {k: dict(v) for k, v in correlations["by_volatility"].items()}
    correlations["by_momentum_and_volatility"] = {k: dict(v) for k, v in correlations["by_momentum_and_volatility"].items()}

    return correlations


def generate_price_signals(correlations: dict, analyzed_data: list[dict]) -> list[dict]:
    """
    Generate trading signals based on price correlations.

    These signals indicate when price conditions favor YES or NO bets.
    The auto-trader can use these to filter or boost trading decisions.
    """
    signals = []

    # Generate signals from momentum correlations
    for symbol, categories in correlations.get("by_momentum", {}).items():
        for category, stats in categories.items():
            if stats["total"] < MIN_OBSERVATIONS_FOR_SIGNAL:
                continue

            win_rate = stats.get("yes_win_rate", 50)

            # Strong YES signal if YES wins significantly more than 50%
            if win_rate >= 60:
                signals.append({
                    "symbol": symbol,
                    "condition": "momentum",
                    "value": category,
                    "favors": "YES",
                    "confidence": min(stats["total"] / 10, 1.0),  # Increases with more data
                    "yes_win_rate": win_rate,
                    "samples": stats["total"],
                    "description": f"{symbol} with {category} momentum favors YES ({win_rate}% win rate)"
                })
            # Strong NO signal if YES wins significantly less than 50%
            elif win_rate <= 40:
                signals.append({
                    "symbol": symbol,
                    "condition": "momentum",
                    "value": category,
                    "favors": "NO",
                    "confidence": min(stats["total"] / 10, 1.0),
                    "yes_win_rate": win_rate,
                    "samples": stats["total"],
                    "description": f"{symbol} with {category} momentum favors NO ({100-win_rate}% win rate)"
                })

    # Generate signals from volatility correlations
    for symbol, categories in correlations.get("by_volatility", {}).items():
        for category, stats in categories.items():
            if stats["total"] < MIN_OBSERVATIONS_FOR_SIGNAL:
                continue

            win_rate = stats.get("yes_win_rate", 50)

            # High volatility often means unpredictable - might want to avoid
            if category == "high" and stats["total"] >= 5:
                signals.append({
                    "symbol": symbol,
                    "condition": "volatility",
                    "value": "high",
                    "favors": "AVOID",
                    "confidence": min(stats["total"] / 10, 1.0),
                    "yes_win_rate": win_rate,
                    "samples": stats["total"],
                    "description": f"{symbol} high volatility periods are unpredictable (YES wins {win_rate}%)"
                })

    # Sort by confidence
    signals.sort(key=lambda x: x["confidence"], reverse=True)

    return signals


def save_correlations(correlations: dict, signals: list[dict], analyzed_data: list[dict]):
    """Save correlation analysis to JSON."""
    now = datetime.now(timezone.utc)

    output = {
        "generated_at": now.isoformat(),
        "data_points": len(analyzed_data),
        "correlations": correlations,
        "signals": signals,
        "analyzed_markets": analyzed_data[-50:],  # Keep last 50 for debugging
        "metadata": {
            "momentum_windows": MOMENTUM_WINDOWS_MINUTES,
            "volatility_window": VOLATILITY_WINDOW_MINUTES,
            "min_observations_for_signal": MIN_OBSERVATIONS_FOR_SIGNAL,
        }
    }

    # Ensure directory exists
    CORRELATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(CORRELATIONS_FILE, "w") as f:
        json.dump(output, f, indent=2)

    console.print(f"\n[dim]Correlations saved to: {CORRELATIONS_FILE}[/dim]")


def display_correlations(correlations: dict, signals: list[dict], analyzed_count: int):
    """Display correlation analysis results."""
    console.print(Panel(
        f"[bold]Price Correlation Analysis[/bold]\n\n"
        f"Analyzed markets: {analyzed_count}\n"
        f"Generated signals: {len(signals)}\n\n"
        "[dim]Correlations improve as more data is collected![/dim]",
        border_style="blue"
    ))

    # Momentum correlations table
    if correlations.get("by_momentum"):
        table = Table(title="Momentum vs Outcome", show_header=True, header_style="bold cyan")
        table.add_column("Symbol", width=8)
        table.add_column("Momentum", width=10)
        table.add_column("YES Wins", justify="right", width=10)
        table.add_column("Total", justify="right", width=8)
        table.add_column("YES Rate", justify="right", width=10)

        for symbol in sorted(correlations["by_momentum"].keys()):
            for category in ["positive", "neutral", "negative"]:
                stats = correlations["by_momentum"][symbol].get(category, {})
                if stats.get("total", 0) > 0:
                    win_rate = stats.get("yes_win_rate", 0)
                    style = "green" if win_rate > 55 else "red" if win_rate < 45 else "yellow"
                    table.add_row(
                        symbol,
                        category,
                        str(stats.get("yes_wins", 0)),
                        str(stats.get("total", 0)),
                        f"[{style}]{win_rate}%[/{style}]"
                    )

        console.print(table)
        console.print()

    # Volatility correlations table
    if correlations.get("by_volatility"):
        table = Table(title="Volatility vs Outcome", show_header=True, header_style="bold cyan")
        table.add_column("Symbol", width=8)
        table.add_column("Volatility", width=10)
        table.add_column("YES Wins", justify="right", width=10)
        table.add_column("Total", justify="right", width=8)
        table.add_column("YES Rate", justify="right", width=10)

        for symbol in sorted(correlations["by_volatility"].keys()):
            for category in ["low", "medium", "high"]:
                stats = correlations["by_volatility"][symbol].get(category, {})
                if stats.get("total", 0) > 0:
                    win_rate = stats.get("yes_win_rate", 0)
                    style = "green" if win_rate > 55 else "red" if win_rate < 45 else "yellow"
                    table.add_row(
                        symbol,
                        category,
                        str(stats.get("yes_wins", 0)),
                        str(stats.get("total", 0)),
                        f"[{style}]{win_rate}%[/{style}]"
                    )

        console.print(table)
        console.print()

    # Signals table
    if signals:
        table = Table(title="Generated Trading Signals", show_header=True, header_style="bold green")
        table.add_column("Symbol", width=8)
        table.add_column("Condition", width=12)
        table.add_column("Favors", width=8)
        table.add_column("Confidence", width=10)
        table.add_column("Samples", justify="right", width=8)
        table.add_column("Description", width=40)

        for signal in signals[:10]:  # Top 10 signals
            conf = signal.get("confidence", 0)
            conf_style = "green" if conf > 0.7 else "yellow" if conf > 0.3 else "dim"
            favors = signal.get("favors", "")
            favors_style = "green" if favors == "YES" else "red" if favors == "NO" else "yellow"

            table.add_row(
                signal.get("symbol", ""),
                f"{signal.get('condition', '')}={signal.get('value', '')}",
                f"[{favors_style}]{favors}[/{favors_style}]",
                f"[{conf_style}]{conf:.1%}[/{conf_style}]",
                str(signal.get("samples", 0)),
                signal.get("description", "")[:40]
            )

        console.print(table)
    else:
        console.print("[yellow]Not enough data to generate signals yet.[/yellow]")
        console.print("[dim]Keep running the collectors to gather more data.[/dim]")


def main():
    console.print("=" * 70)
    console.print("[bold]Price Correlation Analyzer[/bold]")
    console.print("=" * 70)
    console.print("Correlates actual crypto prices with Kalshi market outcomes.")
    console.print()

    # Load data
    console.print("[dim]Loading price data...[/dim]")
    price_data = load_price_data()

    console.print("[dim]Loading market results...[/dim]")
    results = load_market_results()

    if not price_data:
        console.print("\n[yellow]No price data available yet.[/yellow]")
        console.print("[dim]Run price_collector.py to start collecting price data.[/dim]")
        return

    if not results:
        console.print("\n[yellow]No market results available yet.[/yellow]")
        console.print("[dim]Run crypto_collector.py and wait for markets to settle.[/dim]")
        return

    console.print(f"\nLoaded {len(price_data)} price observations")
    console.print(f"Loaded {len(results)} market results")

    # Analyze outcomes with price data
    console.print("\n[dim]Analyzing price conditions around market outcomes...[/dim]")
    analyzed = analyze_market_outcomes(price_data, results)
    console.print(f"Successfully matched {len(analyzed)} markets with price data")

    if not analyzed:
        console.print("\n[yellow]No overlap between price data and market results.[/yellow]")
        console.print("[dim]The price collector needs to run during market hours.[/dim]")
        console.print("[dim]Price data and market closes need to overlap in time.[/dim]")
        return

    # Calculate correlations
    console.print("\n[dim]Calculating correlations...[/dim]")
    correlations = calculate_correlations(analyzed)

    # Generate signals
    console.print("[dim]Generating trading signals...[/dim]")
    signals = generate_price_signals(correlations, analyzed)

    # Display results
    console.print()
    display_correlations(correlations, signals, len(analyzed))

    # Save results
    save_correlations(correlations, signals, analyzed)

    # Summary
    console.print("\n" + "=" * 70)
    if signals:
        console.print("[bold green]Signals generated![/bold green]")
        console.print("The auto-trader will use these to improve trading decisions.")
    else:
        console.print("[yellow]More data needed for reliable signals.[/yellow]")
    console.print(f"\nCollected {len(price_data)} price points, analyzed {len(analyzed)} markets.")
    console.print("[dim]Keep both collectors running to improve correlation accuracy.[/dim]")
    console.print("=" * 70)


if __name__ == "__main__":
    main()
