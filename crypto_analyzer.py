#!/usr/bin/env python3
"""
Crypto Market Data Analyzer for Kalshi

Analyzes collected data WITH RESULTS to identify optimal trading patterns.
Calculates actual win rates for different time/odds combinations.
Supports recency weighting to adapt to changing market conditions.
"""

import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

DATA_DIR = Path(__file__).parent / "data" / "crypto_analysis"
CSV_FILE = DATA_DIR / "market_observations.csv"
RESULTS_FILE = DATA_DIR / "market_results.json"
ANALYSIS_FILE = DATA_DIR / "strategy_analysis.json"

# Recency weighting parameters
# Half-life of 2 hours means data from 2 hours ago has half the weight
RECENCY_HALF_LIFE_HOURS = 2.0

# Maximum odds to consider for trading rules
# Higher odds have terrible risk/reward: at 85% odds, you risk 85c to win 15c
# At 75% odds: risk 75c to win 25c (3:1 loss:win ratio, need 75%+ win rate)
# This cap prevents generating rules that are statistically unfavorable
MAX_RULE_ODDS = 75  # Don't generate rules above 75% odds


def calculate_recency_weight(observation_time: datetime, now: datetime = None) -> float:
    """
    Calculate weight for an observation based on recency.
    Uses exponential decay with configurable half-life.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Parse observation time if it's a string
    if isinstance(observation_time, str):
        try:
            observation_time = datetime.fromisoformat(observation_time.replace("Z", "+00:00"))
        except ValueError:
            return 1.0  # Default weight if parsing fails

    # Ensure both are timezone-aware
    if observation_time.tzinfo is None:
        observation_time = observation_time.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    hours_ago = (now - observation_time).total_seconds() / 3600

    # Exponential decay: weight = 0.5 ^ (hours_ago / half_life)
    # Recent data (0 hours ago) = weight 1.0
    # 2 hours ago = weight 0.5
    # 4 hours ago = weight 0.25
    weight = math.pow(0.5, hours_ago / RECENCY_HALF_LIFE_HOURS)

    return max(weight, 0.01)  # Minimum weight of 1%


def load_data() -> list[dict]:
    """Load collected data from CSV."""
    if not CSV_FILE.exists():
        console.print("[red]No data file found. Run crypto_collector.py first.[/red]")
        return []

    data = []
    with open(CSV_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Convert numeric fields
            for field in ["seconds_to_close", "minutes_to_close",
                         "yes_bid", "yes_ask", "no_bid", "no_ask", "spread",
                         "volume", "open_interest", "last_price"]:
                if row.get(field) and row[field] != "":
                    try:
                        row[field] = float(row[field])
                    except ValueError:
                        row[field] = None
                else:
                    row[field] = None
            data.append(row)

    return data


def filter_with_results(data: list[dict]) -> list[dict]:
    """Filter to only observations that have results."""
    return [d for d in data if d.get("result") in ["yes", "no"]]


def calculate_win_rates(data: list[dict], use_recency_weighting: bool = True) -> dict:
    """
    Calculate win rates for different strategies.

    For each combination of:
    - Series (SOL, ETH, BTC)
    - Time bucket (30-second intervals for fine granularity)
    - Odds threshold (65%, 70%, 75%, 80%, 85%, 90%, 95%)
    - Side (YES or NO)

    Calculate: If you bet on [SIDE] when it's at [ODDS]% with [TIME] left,
               what % of the time does [SIDE] actually win?

    When use_recency_weighting=True, recent observations count more than older ones.
    """
    # Structure: results[series][time_bucket][side][odds_threshold] = {wins, total, win_rate}
    # Using weighted counts when recency_weighting is enabled
    results = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(
        lambda: {"wins": 0.0, "total": 0.0, "raw_wins": 0, "raw_total": 0}
    ))))

    # Only analyze odds up to MAX_RULE_ODDS for better risk/reward
    # Higher odds (80%+) have terrible payoff ratios
    odds_thresholds = [t for t in [65, 70, 75, 80, 85, 90, 95] if t <= MAX_RULE_ODDS]
    now = datetime.now(timezone.utc)

    for obs in data:
        series = obs["series_ticker"]
        minutes = obs["minutes_to_close"]
        result = obs["result"]  # "yes" or "no"
        yes_bid = obs["yes_bid"]
        no_bid = obs["no_bid"]
        timestamp = obs.get("timestamp", "")

        if minutes is None or result not in ["yes", "no"]:
            continue

        # CRITICAL: Skip observations with less than 45 seconds (0.75 minutes) to close
        # Kalshi requires time to process orders - we can't reliably trade in the last 45 seconds
        if minutes < 0.75:
            continue

        # Calculate weight based on recency
        weight = 1.0
        if use_recency_weighting and timestamp:
            weight = calculate_recency_weight(timestamp, now)

        # Time bucket: 30-second intervals (0.75-1, 1-1.5, 1.5-2, etc.)
        # Bucket 1 = 30-60sec (first valid bucket after 45sec cutoff)
        # Bucket 2 = 60-90sec, etc.
        time_bucket = min(int(minutes * 2), 29)  # Max 29 = 14.5-15 minutes

        # Check YES side at various thresholds
        if yes_bid is not None:
            for threshold in odds_thresholds:
                if yes_bid >= threshold:
                    results[series][time_bucket]["yes"][threshold]["total"] += weight
                    results[series][time_bucket]["yes"][threshold]["raw_total"] += 1
                    if result == "yes":
                        results[series][time_bucket]["yes"][threshold]["wins"] += weight
                        results[series][time_bucket]["yes"][threshold]["raw_wins"] += 1

        # Check NO side at various thresholds
        if no_bid is not None:
            for threshold in odds_thresholds:
                if no_bid >= threshold:
                    results[series][time_bucket]["no"][threshold]["total"] += weight
                    results[series][time_bucket]["no"][threshold]["raw_total"] += 1
                    if result == "no":
                        results[series][time_bucket]["no"][threshold]["wins"] += weight
                        results[series][time_bucket]["no"][threshold]["raw_wins"] += 1

    # Calculate win rates
    for series in results:
        for time_bucket in results[series]:
            for side in results[series][time_bucket]:
                for threshold in results[series][time_bucket][side]:
                    stats = results[series][time_bucket][side][threshold]
                    if stats["total"] > 0:
                        stats["win_rate"] = stats["wins"] / stats["total"] * 100
                    else:
                        stats["win_rate"] = None

    return dict(results)


def bucket_to_time_str(bucket: int) -> str:
    """Convert bucket number to readable time string."""
    # Bucket 0 = 0-30sec, Bucket 1 = 30-60sec, etc.
    start_sec = bucket * 30
    end_sec = start_sec + 30

    start_min = start_sec // 60
    start_sec_rem = start_sec % 60
    end_min = end_sec // 60
    end_sec_rem = end_sec % 60

    if start_min == end_min:
        return f"{start_min}:{start_sec_rem:02d}-{end_sec_rem:02d}"
    else:
        return f"{start_min}:{start_sec_rem:02d}-{end_min}:{end_sec_rem:02d}"


def find_best_strategies(win_rates: dict, min_samples: int = 5) -> list[dict]:
    """
    Find the best trading strategies based on win rates.

    Returns strategies sorted by expected value (win_rate * potential_profit - loss_rate * cost).
    """
    strategies = []

    for series, time_buckets in win_rates.items():
        for time_bucket, sides in time_buckets.items():
            for side, thresholds in sides.items():
                for threshold, stats in thresholds.items():
                    # Use raw_total for minimum sample requirement
                    raw_total = stats.get("raw_total", stats["total"])
                    if raw_total < min_samples:
                        continue

                    win_rate = stats["win_rate"]
                    if win_rate is None:
                        continue

                    # Calculate expected value
                    # If we bet at X% odds: cost = X cents, win = 100 cents
                    # Profit if win = 100 - X cents
                    # Loss if lose = X cents
                    cost = threshold  # cents
                    profit_if_win = 100 - threshold  # cents

                    expected_value = (win_rate / 100 * profit_if_win) - ((100 - win_rate) / 100 * cost)

                    # Calculate minutes remaining for readable output
                    min_left = time_bucket * 0.5  # Each bucket is 30 seconds
                    max_left = min_left + 0.5

                    strategies.append({
                        "series": series,
                        "time_bucket": time_bucket,
                        "time_range": bucket_to_time_str(time_bucket),
                        "minutes_left": f"{min_left:.1f}-{max_left:.1f}",
                        "min_minutes": min_left,
                        "max_minutes": max_left,
                        "side": side.upper(),
                        "min_odds": threshold,
                        "samples": int(raw_total),  # Raw sample count
                        "weighted_samples": round(stats["total"], 2),  # Weighted count
                        "wins": stats.get("raw_wins", int(stats["wins"])),
                        "weighted_wins": round(stats["wins"], 2),
                        "win_rate": round(win_rate, 1),
                        "expected_value": round(expected_value, 2),
                        "profit_if_win": profit_if_win,
                        "cost_if_lose": cost,
                    })

    # Sort by expected value (best first)
    strategies.sort(key=lambda x: x["expected_value"], reverse=True)

    return strategies


def display_summary(data: list[dict], data_with_results: list[dict]):
    """Display summary of collected data."""
    if not data:
        return

    # Count by series
    series_counts = defaultdict(lambda: {"total": 0, "with_result": 0})
    for row in data:
        series_counts[row["series_ticker"]]["total"] += 1
    for row in data_with_results:
        series_counts[row["series_ticker"]]["with_result"] += 1

    # Date range
    timestamps = [r["timestamp"] for r in data if r.get("timestamp")]
    date_min = min(timestamps) if timestamps else "N/A"
    date_max = max(timestamps) if timestamps else "N/A"

    # Results summary
    yes_wins = sum(1 for d in data_with_results if d["result"] == "yes")
    no_wins = sum(1 for d in data_with_results if d["result"] == "no")

    text = f"""[bold]Data Summary[/bold]

Total observations: {len(data):,}
With results: {len(data_with_results):,} ({len(data_with_results)/len(data)*100:.1f}% if data else 0)
Date range: {date_min[:19]} to {date_max[:19]}

Results breakdown: {yes_wins} YES wins, {no_wins} NO wins

By series:"""

    for series, counts in sorted(series_counts.items()):
        text += f"\n  {series}: {counts['total']:,} obs, {counts['with_result']:,} with results"

    console.print(Panel(text, border_style="blue"))


def display_win_rate_table(win_rates: dict, series: str):
    """Display win rate table for a specific series (summary by minute)."""
    if series not in win_rates:
        return

    # Aggregate 30-second buckets into 1-minute buckets for display
    minute_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"wins": 0, "total": 0})))

    for bucket, sides in win_rates[series].items():
        minute = bucket // 2  # Convert 30-sec bucket to minute
        for side, thresholds in sides.items():
            for threshold, stats in thresholds.items():
                minute_stats[minute][side][threshold]["wins"] += stats["wins"]
                minute_stats[minute][side][threshold]["total"] += stats["total"]

    # Create tables for YES and NO
    for side in ["yes", "no"]:
        table = Table(
            title=f"{series} - {side.upper()} Win Rates by Minute",
            show_header=True,
            header_style="bold cyan"
        )
        table.add_column("Min Left", justify="center", width=8)
        table.add_column("70%+", justify="center", width=14)
        table.add_column("75%+", justify="center", width=14)
        table.add_column("80%+", justify="center", width=14)
        table.add_column("85%+", justify="center", width=14)
        table.add_column("90%+", justify="center", width=14)

        for minute in sorted(minute_stats.keys(), reverse=True):
            if minute > 14:
                continue
            if side not in minute_stats[minute]:
                continue

            row = [f"{minute}-{minute+1}m"]

            for threshold in [70, 75, 80, 85, 90]:
                stats = minute_stats[minute][side].get(threshold, {})
                total = stats.get("total", 0)
                wins = stats.get("wins", 0)

                if total == 0:
                    row.append("-")
                else:
                    win_rate = wins / total * 100
                    # Color code: green if > odds, red if < odds
                    if win_rate >= threshold + 5:
                        style = "green"
                    elif win_rate < threshold - 5:
                        style = "red"
                    else:
                        style = "yellow"
                    row.append(f"[{style}]{win_rate:.0f}%[/{style}] ({total})")

            table.add_row(*row)

        console.print(table)
        console.print()


def display_best_strategies(strategies: list[dict], top_n: int = 20):
    """Display the best trading strategies."""
    if not strategies:
        console.print("[yellow]Not enough data with results to identify strategies.[/yellow]")
        console.print("[dim]Continue running the collector and wait for markets to settle.[/dim]")
        return

    # Filter to profitable strategies
    profitable = [s for s in strategies if s["expected_value"] > 0]

    console.print(f"\n[bold green]Top {min(top_n, len(profitable))} Profitable Strategies[/bold green]")
    console.print("[dim]Sorted by expected value per trade[/dim]\n")

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Rank", justify="right", width=5)
    table.add_column("Series", width=10)
    table.add_column("Time", width=12)
    table.add_column("Side", width=6)
    table.add_column("Min Odds", justify="right", width=9)
    table.add_column("Win Rate", justify="right", width=10)
    table.add_column("Samples", justify="right", width=8)
    table.add_column("EV/Trade", justify="right", width=10)

    for i, s in enumerate(profitable[:top_n], 1):
        ev_style = "green" if s["expected_value"] > 5 else "yellow" if s["expected_value"] > 0 else "red"
        wr_style = "green" if s["win_rate"] > s["min_odds"] else "red"

        table.add_row(
            str(i),
            s["series"],
            s["time_range"],
            s["side"],
            f"{s['min_odds']}%",
            f"[{wr_style}]{s['win_rate']}%[/{wr_style}]",
            str(s["samples"]),
            f"[{ev_style}]+{s['expected_value']}c[/{ev_style}]"
        )

    console.print(table)

    # Show losing strategies to avoid
    console.print(f"\n[bold red]Strategies to AVOID (Negative EV)[/bold red]\n")
    losing = [s for s in strategies if s["expected_value"] < -5 and s["samples"] >= 10][:10]

    if losing:
        table2 = Table(show_header=True, header_style="bold red")
        table2.add_column("Series", width=10)
        table2.add_column("Time", width=12)
        table2.add_column("Side", width=6)
        table2.add_column("Min Odds", justify="right", width=9)
        table2.add_column("Win Rate", justify="right", width=10)
        table2.add_column("Samples", justify="right", width=8)
        table2.add_column("EV/Trade", justify="right", width=10)

        for s in losing:
            table2.add_row(
                s["series"],
                s["time_range"],
                s["side"],
                f"{s['min_odds']}%",
                f"{s['win_rate']}%",
                str(s["samples"]),
                f"[red]{s['expected_value']}c[/red]"
            )

        console.print(table2)
    else:
        console.print("[dim]No significantly losing strategies found yet.[/dim]")


def save_analysis(strategies: list[dict], win_rates: dict):
    """Save analysis to JSON."""
    now = datetime.now(timezone.utc)

    # Filter for profitable strategies with enough confidence
    profitable = [s for s in strategies if s["expected_value"] > 0 and s["samples"] >= 3]

    output = {
        "generated_at": now.isoformat(),
        "recency_half_life_hours": RECENCY_HALF_LIFE_HOURS,
        "profitable_strategies": profitable[:100],
        "losing_strategies": [s for s in strategies if s["expected_value"] < 0][:50],
        "win_rates_by_series": {},
        # Trading rules for auto-trader consumption
        "trading_rules": generate_trading_rules(profitable),
    }

    # Simplify win_rates for JSON
    for series, time_buckets in win_rates.items():
        output["win_rates_by_series"][series] = {}
        for tb, sides in time_buckets.items():
            output["win_rates_by_series"][series][str(tb)] = {}
            for side, thresholds in sides.items():
                output["win_rates_by_series"][series][str(tb)][side] = {}
                for thresh, stats in thresholds.items():
                    if stats["total"] > 0:
                        output["win_rates_by_series"][series][str(tb)][side][str(thresh)] = {
                            "wins": round(stats["wins"], 2),
                            "total": round(stats["total"], 2),
                            "raw_wins": stats.get("raw_wins", int(stats["wins"])),
                            "raw_total": stats.get("raw_total", int(stats["total"])),
                            "win_rate": round(stats["win_rate"], 1) if stats["win_rate"] else None
                        }

    with open(ANALYSIS_FILE, "w") as f:
        json.dump(output, f, indent=2)

    console.print(f"\n[dim]Analysis saved to: {ANALYSIS_FILE}[/dim]")


def generate_trading_rules(profitable_strategies: list[dict]) -> list[dict]:
    """
    Generate simplified trading rules for auto-trader consumption.

    Rules are sorted by expected value and include:
    - series: Which crypto market
    - side: YES or NO
    - min_odds: Minimum odds to trigger (e.g., 70)
    - max_minutes: Maximum minutes before close to bet
    - min_minutes: Minimum minutes before close (safety buffer)
    - expected_ev: Expected value per trade in cents
    - confidence: Based on sample size
    """
    rules = []

    # Group strategies by series+side+odds and find optimal time windows
    seen = set()

    for strat in profitable_strategies:
        # Only include strategies with positive EV and decent sample size
        if strat["expected_value"] <= 0 or strat["samples"] < 3:
            continue

        # Create a unique key for this rule type
        key = (strat["series"], strat["side"], strat["min_odds"])
        if key in seen:
            continue
        seen.add(key)

        # Safety buffer: don't bet in last 45 seconds
        # Kalshi needs time to process orders before market closes
        min_minutes = max(0.75, strat["min_minutes"])  # At least 45 seconds buffer

        # Confidence score based on sample size
        if strat["samples"] >= 20:
            confidence = "high"
        elif strat["samples"] >= 10:
            confidence = "medium"
        else:
            confidence = "low"

        rules.append({
            "series": strat["series"],
            "side": strat["side"],
            "min_odds": strat["min_odds"],
            "min_minutes": min_minutes,
            "max_minutes": strat["max_minutes"],
            "expected_ev": strat["expected_value"],
            "win_rate": strat["win_rate"],
            "samples": strat["samples"],
            "confidence": confidence,
        })

    # Sort by expected value
    rules.sort(key=lambda x: x["expected_ev"], reverse=True)

    return rules


def main():
    console.print(Panel(
        "[bold]Kalshi Crypto Market Strategy Analyzer[/bold]\n\n"
        "Analyzes collected data WITH RESULTS to find profitable trading strategies.\n"
        "Calculates actual win rates for different time/odds combinations.\n\n"
        f"[dim]Recency weighting: half-life = {RECENCY_HALF_LIFE_HOURS} hours[/dim]\n"
        "[dim](Recent data is weighted more heavily than older data)[/dim]",
        border_style="blue"
    ))

    # Load all data
    data = load_data()
    if not data:
        return

    # Filter to only data with results
    data_with_results = filter_with_results(data)

    display_summary(data, data_with_results)

    if not data_with_results:
        console.print("\n[yellow]No observations with results yet.[/yellow]")
        console.print("[dim]The collector needs to run long enough for markets to settle (15+ minutes).[/dim]")
        console.print("[dim]Results are captured ~2 minutes after each market closes.[/dim]")
        return

    # Calculate win rates
    console.print("\n[bold]Calculating win rates...[/bold]\n")
    win_rates = calculate_win_rates(data_with_results)

    # Display win rate tables for each series
    for series in sorted(win_rates.keys()):
        display_win_rate_table(win_rates, series)

    # Find and display best strategies
    strategies = find_best_strategies(win_rates, min_samples=3)
    display_best_strategies(strategies)

    # Save analysis
    save_analysis(strategies, win_rates)

    # Summary recommendation - show top 3 strategies with clear language
    profitable = [s for s in strategies if s["expected_value"] > 0 and s["samples"] >= 5]
    if profitable:
        console.print("\n" + "="*70)
        console.print("[bold green]TOP STRATEGIES - CLEAR RECOMMENDATIONS[/bold green]")
        console.print("="*70 + "\n")

        for i, best in enumerate(profitable[:3], 1):
            mins = float(best['minutes_left'].split('-')[0])

            # Create clear, readable recommendation
            recommendation = (
                f"[bold]#{i} BEST BET:[/bold]\n"
                f"   [cyan]{best['series']}[/cyan]: Bet [bold]{best['side']}[/bold] "
                f"when {best['side']} odds are [bold]{best['min_odds']}%+[/bold]\n"
                f"   with [bold]{mins:.1f} minutes[/bold] left before close\n\n"
                f"   [green]Win rate: {best['win_rate']}%[/green] "
                f"(from {best['samples']} observations)\n"
                f"   If you bet $1: Win ${best['profit_if_win']/100:.2f} or Lose ${best['cost_if_lose']/100:.2f}\n"
                f"   [bold green]Expected profit: +{best['expected_value']}¢ per $1 bet[/bold green]"
            )
            console.print(Panel(recommendation, border_style="green"))

        console.print("\n[bold yellow]PLAIN ENGLISH SUMMARY:[/bold yellow]")
        best = profitable[0]
        mins = float(best['minutes_left'].split('-')[0])
        console.print(
            f'\n   "Placing bets on [bold]{best["side"]}[/bold] at [bold]{best["min_odds"]}%[/bold] odds '
            f'with [bold]{mins:.1f} minutes[/bold] left\n'
            f'    on [bold]{best["series"]}[/bold] wins [bold green]{best["win_rate"]}%[/bold green] of the time."\n'
        )
        console.print("[dim]More data = more confidence. Keep the collector running![/dim]")


if __name__ == "__main__":
    main()
