#!/usr/bin/env python3
"""
Data-Driven Crypto Auto-Trader for Kalshi

Uses analysis from crypto_analyzer.py to make trading decisions.
Only trades when conditions match historically profitable strategies.
Adapts to changing market conditions through recency-weighted analysis.
"""

import json
import time
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich.text import Text

from config import config
from kalshi_client import client, KalshiAPIError
from simulator import simulator

console = Console()

# Paths
DATA_DIR = Path(__file__).parent / "data" / "crypto_analysis"
ANALYSIS_FILE = DATA_DIR / "strategy_analysis.json"
TRADED_FILE = DATA_DIR / "crypto_traded.json"
CORRELATIONS_FILE = DATA_DIR / "price_correlations.json"
PRICES_FILE = Path(__file__).parent / "data" / "crypto_prices" / "prices.json"

# Series we trade
CRYPTO_SERIES = ["KXSOL15M", "KXETH15M", "KXBTC15M"]

# Symbol mapping
SERIES_TO_SYMBOL = {
    "KXSOL15M": "SOL",
    "KXETH15M": "ETH",
    "KXBTC15M": "BTC",
}


@dataclass
class TradingRule:
    """A trading rule derived from analysis."""
    series: str
    side: str  # YES or NO
    min_odds: int
    min_minutes: float  # Minimum time before close (safety buffer)
    max_minutes: float  # Maximum time before close
    expected_ev: float  # Expected value in cents
    win_rate: float
    samples: int
    confidence: str  # high, medium, low


@dataclass
class CryptoAutoTraderConfig:
    """Configuration for crypto auto-trader.

    RISK/REWARD OPTIMIZATION:
    The key insight is that high-odds bets have terrible risk/reward ratios.
    At 80% odds: risk 80c to win 20c = need 80% win rate just to break even
    At 70% odds: risk 70c to win 30c = need 70% win rate to break even
    At 60% odds: risk 60c to win 40c = need 60% win rate to break even

    Targeting 60-75% odds gives us:
    - Better profit per win (25-40c vs 5-20c at high odds)
    - More margin for error (one loss doesn't wipe out many wins)
    - With 85%+ actual win rate, we make consistent profit
    """
    # Safety buffer: don't trade in the last N seconds before close
    # Kalshi needs time to process orders
    min_seconds_to_close: int = 45  # 45 second safety buffer

    # Maximum time to close - don't bet too early (odds less reliable)
    # 8 minutes gives good balance of reliable odds and opportunity window
    max_minutes_to_close: float = 8.0

    # Minimum expected value (cents) to consider a trade
    # Increased from 1.0 to require more significant edge
    min_expected_ev: float = 5.0

    # Minimum win rate to consider (percentage)
    # Win rate must exceed odds + buffer for profit
    min_win_rate: float = 75.0

    # Minimum samples required for confidence
    min_samples: int = 10  # Increased for more statistical significance

    # Only use high/medium confidence strategies
    required_confidence: list = field(default_factory=lambda: ["high", "medium"])

    # Budget allocation - Kelly-inspired sizing
    # Conservative: bet ~25% of optimal Kelly to reduce variance
    # With 80% win rate at 70% odds: Kelly suggests ~30%, we use ~7.5%
    budget_per_position: float = 0.08  # 8% of balance per trade

    # CRITICAL: Max 1 position per cycle to avoid correlated crypto risk
    # All 3 cryptos tend to move together - betting on all 3 = 3x loss if wrong
    max_positions_per_cycle: int = 1

    # IMPROVED Odds limits for better risk/reward ratio
    # OLD: 65-85% (at 85%: risk 85c to win 15c, need 85%+ win rate)
    # NEW: 60-75% (at 75%: risk 75c to win 25c, need 75%+ win rate)
    # This gives us better profit margins per win!
    min_odds: int = 60  # Minimum odds - don't bet when market is too uncertain
    max_odds: int = 75  # Maximum odds - CRITICAL for risk/reward

    # Minimum profit ratio: profit_if_win / risk (cost)
    # At 70% odds: 30/70 = 0.43 (win $0.43 per $1 risked)
    # At 80% odds: 20/80 = 0.25 (win $0.25 per $1 risked)
    # Require at least 0.33 (win $0.33 per $1 risked, ~75% max odds)
    min_profit_ratio: float = 0.33

    # Scan interval
    scan_interval_seconds: int = 10  # Check every 10 seconds

    # Re-analyze data periodically
    reanalyze_interval_minutes: int = 15


class CryptoAutoTrader:
    """Data-driven auto-trader for crypto markets."""

    def __init__(self):
        self.config = CryptoAutoTraderConfig()
        self.trading_rules: list[TradingRule] = []
        self.traded_this_cycle: set[str] = set()  # Market tickers traded in current cycle
        self.all_traded: dict = self._load_traded()
        self.last_analysis_time: Optional[datetime] = None
        self.analysis_age_warning_shown = False
        # Price correlation data
        self.price_signals: list[dict] = []
        self.current_prices: dict = {}  # Symbol -> current price info
        self.price_momentum: dict = {}  # Symbol -> momentum category

    def _load_traded(self) -> dict:
        """Load history of traded positions."""
        if TRADED_FILE.exists():
            try:
                with open(TRADED_FILE, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, KeyError):
                pass
        return {"traded": [], "results": {}}

    def _save_traded(self):
        """Save traded positions."""
        with open(TRADED_FILE, "w") as f:
            json.dump(self.all_traded, f, indent=2)

    def load_price_signals(self) -> bool:
        """Load price correlation signals."""
        if not CORRELATIONS_FILE.exists():
            # No correlations yet - that's OK, will improve over time
            self.price_signals = []
            return False

        try:
            with open(CORRELATIONS_FILE, "r") as f:
                data = json.load(f)

            self.price_signals = data.get("signals", [])
            return len(self.price_signals) > 0

        except Exception as e:
            console.print(f"[dim]Could not load price signals: {e}[/dim]")
            self.price_signals = []
            return False

    def load_current_prices(self) -> bool:
        """Load most recent price data and calculate momentum."""
        if not PRICES_FILE.exists():
            return False

        try:
            with open(PRICES_FILE, "r") as f:
                data = json.load(f)

            observations = data.get("observations", [])
            if len(observations) < 2:
                return False

            # Get most recent prices
            latest = observations[-1].get("prices", {})
            self.current_prices = latest

            # Calculate 5-minute momentum (need ~10 observations at 30-sec intervals)
            if len(observations) >= 10:
                past = observations[-10].get("prices", {})
                for symbol in ["BTC", "ETH", "SOL"]:
                    current_price = latest.get(symbol, {}).get("usd")
                    past_price = past.get(symbol, {}).get("usd")

                    if current_price and past_price and past_price > 0:
                        momentum_pct = ((current_price - past_price) / past_price) * 100

                        if momentum_pct > 0.1:
                            self.price_momentum[symbol] = "positive"
                        elif momentum_pct < -0.1:
                            self.price_momentum[symbol] = "negative"
                        else:
                            self.price_momentum[symbol] = "neutral"

            return True

        except Exception as e:
            console.print(f"[dim]Could not load prices: {e}[/dim]")
            return False

    def get_price_signal_for_trade(self, series: str, side: str) -> Optional[dict]:
        """
        Check if there's a price signal that affects this trade.

        Returns signal dict if found, None otherwise.
        Signal can indicate:
        - FAVOR: Price conditions support this trade
        - AVOID: Price conditions suggest avoiding this trade
        - None: No signal / insufficient data
        """
        symbol = SERIES_TO_SYMBOL.get(series)
        if not symbol:
            return None

        momentum = self.price_momentum.get(symbol)
        if not momentum:
            return None

        # Check signals for this symbol and momentum
        for signal in self.price_signals:
            if signal.get("symbol") != symbol:
                continue
            if signal.get("condition") != "momentum":
                continue
            if signal.get("value") != momentum:
                continue

            favors = signal.get("favors", "")

            # Check if signal aligns with our trade
            if favors == "AVOID":
                return {"action": "AVOID", "reason": signal.get("description", ""), "confidence": signal.get("confidence", 0)}
            elif favors == side:
                return {"action": "BOOST", "reason": signal.get("description", ""), "confidence": signal.get("confidence", 0)}
            elif favors in ["YES", "NO"] and favors != side:
                return {"action": "AVOID", "reason": f"Signal favors {favors}, not {side}", "confidence": signal.get("confidence", 0)}

        return None

    def load_trading_rules(self) -> bool:
        """Load trading rules from analysis file."""
        if not ANALYSIS_FILE.exists():
            console.print("[red]No analysis file found. Run crypto_analyzer.py first.[/red]")
            return False

        try:
            with open(ANALYSIS_FILE, "r") as f:
                data = json.load(f)

            # Check analysis age
            generated_at = data.get("generated_at", "")
            if generated_at:
                try:
                    gen_time = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                    age_minutes = (datetime.now(timezone.utc) - gen_time).total_seconds() / 60
                    if age_minutes > 60 and not self.analysis_age_warning_shown:
                        console.print(f"[yellow]Warning: Analysis is {age_minutes:.0f} minutes old. Consider re-running analyzer.[/yellow]")
                        self.analysis_age_warning_shown = True
                    self.last_analysis_time = gen_time
                except ValueError:
                    pass

            rules = data.get("trading_rules", [])
            self.trading_rules = []

            for r in rules:
                rule = TradingRule(
                    series=r["series"],
                    side=r["side"],
                    min_odds=r["min_odds"],
                    min_minutes=r["min_minutes"],
                    max_minutes=r["max_minutes"],
                    expected_ev=r["expected_ev"],
                    win_rate=r["win_rate"],
                    samples=r["samples"],
                    confidence=r["confidence"],
                )
                self.trading_rules.append(rule)

            return True

        except Exception as e:
            console.print(f"[red]Error loading analysis: {e}[/red]")
            return False

    def reanalyze_data(self):
        """Run both analyzers to update trading rules and price correlations."""
        console.print("\n[bold cyan]Re-analyzing market data...[/bold cyan]")
        try:
            # Run the market odds analyzer
            result = subprocess.run(
                [sys.executable, "crypto_analyzer.py"],
                capture_output=True,
                text=True,
                cwd=Path(__file__).parent
            )
            if result.returncode != 0:
                console.print(f"[yellow]Odds analyzer warning: {result.stderr[:200]}[/yellow]")

            # Reload rules
            self.load_trading_rules()
            console.print(f"[green]Loaded {len(self.trading_rules)} trading rules[/green]")

            # Run the price correlation analyzer
            console.print("[dim]Analyzing price correlations...[/dim]")
            result2 = subprocess.run(
                [sys.executable, "price_correlation_analyzer.py"],
                capture_output=True,
                text=True,
                cwd=Path(__file__).parent
            )
            if result2.returncode != 0:
                console.print(f"[dim]Price correlation note: {result2.stderr[:100]}[/dim]")

            # Reload price signals
            self.load_price_signals()
            if self.price_signals:
                console.print(f"[green]Loaded {len(self.price_signals)} price signals[/green]")
            else:
                console.print("[dim]No price signals yet (need more overlapping data)[/dim]")

        except Exception as e:
            console.print(f"[red]Error running analyzers: {e}[/red]")

    def get_applicable_rules(self, series: str, minutes_to_close: float,
                             yes_bid: Optional[int], no_bid: Optional[int],
                             yes_ask: Optional[int], no_ask: Optional[int]) -> list[TradingRule]:
        """Find rules that apply to current market conditions.

        IMPORTANT: We check BOTH bid AND ask prices against max_odds.
        The bid is what the market shows, but the ask is what we actually PAY.
        Bug fix: Previously only checked bid, but executed at ask price.
        """
        applicable = []

        for rule in self.trading_rules:
            # Check series
            if rule.series != series:
                continue

            # Check time window (with safety buffer)
            min_minutes = max(rule.min_minutes, self.config.min_seconds_to_close / 60)
            if not (min_minutes <= minutes_to_close <= rule.max_minutes):
                continue

            # Check minimum criteria
            if rule.expected_ev < self.config.min_expected_ev:
                continue
            if rule.win_rate < self.config.min_win_rate:
                continue
            if rule.samples < self.config.min_samples:
                continue
            if rule.confidence not in self.config.required_confidence:
                continue

            # Check if odds meet threshold AND are within our safe range
            # CRITICAL: Check BOTH bid AND ask against max_odds
            # The ask is what we'll actually pay - it must also be within range!
            if rule.side == "YES" and yes_bid:
                # Use ask price if available (what we'll pay), otherwise bid
                effective_price = yes_ask if yes_ask else yes_bid

                # Check profit ratio (profit_if_win / cost)
                profit_if_win = 100 - effective_price
                profit_ratio = profit_if_win / effective_price if effective_price > 0 else 0

                if (yes_bid >= rule.min_odds and
                    yes_bid >= self.config.min_odds and
                    effective_price <= self.config.max_odds and  # Check ask/effective price!
                    profit_ratio >= self.config.min_profit_ratio):  # Ensure good risk/reward
                    applicable.append(rule)
            elif rule.side == "NO" and no_bid:
                # Use ask price if available (what we'll pay), otherwise bid
                effective_price = no_ask if no_ask else no_bid

                # Check profit ratio (profit_if_win / cost)
                profit_if_win = 100 - effective_price
                profit_ratio = profit_if_win / effective_price if effective_price > 0 else 0

                if (no_bid >= rule.min_odds and
                    no_bid >= self.config.min_odds and
                    effective_price <= self.config.max_odds and  # Check ask/effective price!
                    profit_ratio >= self.config.min_profit_ratio):  # Ensure good risk/reward
                    applicable.append(rule)

        # Sort by expected value
        applicable.sort(key=lambda r: r.expected_ev, reverse=True)
        return applicable

    def scan_markets(self) -> list[dict]:
        """Scan crypto markets for trading opportunities."""
        opportunities = []
        now = datetime.now(timezone.utc)

        # Load latest price data and signals
        self.load_current_prices()
        self.load_price_signals()

        for series in CRYPTO_SERIES:
            try:
                response = client.get("/markets", params={
                    "series_ticker": series,
                    "status": "open",
                    "limit": 10
                })
                markets = response.get("markets", [])

                for market in markets:
                    ticker = market.get("ticker", "")

                    # Skip if already traded this cycle
                    if ticker in self.traded_this_cycle:
                        continue

                    # Parse close time
                    close_time_str = market.get("close_time", "")
                    if not close_time_str:
                        continue

                    try:
                        close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
                    except ValueError:
                        continue

                    # Calculate time to close
                    seconds_to_close = (close_time - now).total_seconds()
                    if seconds_to_close <= 0:
                        continue

                    minutes_to_close = seconds_to_close / 60

                    # Skip if outside our trading window
                    if minutes_to_close > self.config.max_minutes_to_close:
                        continue
                    if seconds_to_close < self.config.min_seconds_to_close:
                        continue

                    # Get prices
                    yes_bid = market.get("yes_bid")
                    yes_ask = market.get("yes_ask")
                    no_bid = market.get("no_bid")
                    no_ask = market.get("no_ask")

                    # Find applicable rules (pass both bid AND ask for proper price checking)
                    rules = self.get_applicable_rules(series, minutes_to_close, yes_bid, no_bid, yes_ask, no_ask)

                    if rules:
                        best_rule = rules[0]

                        # Check price signal for this trade
                        price_signal = self.get_price_signal_for_trade(series, best_rule.side)

                        # Skip if price signal says AVOID with high confidence
                        if price_signal and price_signal.get("action") == "AVOID":
                            confidence = price_signal.get("confidence", 0)
                            if confidence >= 0.5:  # Only skip if signal has decent confidence
                                continue

                        # Get current momentum for display
                        symbol = SERIES_TO_SYMBOL.get(series, "")
                        momentum = self.price_momentum.get(symbol, "unknown")

                        opportunities.append({
                            "ticker": ticker,
                            "series": series,
                            "title": market.get("title", ""),
                            "close_time": close_time,
                            "seconds_to_close": seconds_to_close,
                            "minutes_to_close": minutes_to_close,
                            "yes_bid": yes_bid,
                            "yes_ask": yes_ask,
                            "no_bid": no_bid,
                            "no_ask": no_ask,
                            "rule": best_rule,
                            "price_signal": price_signal,
                            "momentum": momentum,
                        })

            except KalshiAPIError as e:
                console.print(f"[red]API error for {series}: {e.message}[/red]")
            except Exception as e:
                console.print(f"[red]Error scanning {series}: {e}[/red]")

        return opportunities

    def calculate_position_size(self, opportunity: dict) -> int:
        """Calculate number of contracts to buy."""
        rule = opportunity["rule"]

        # Get price based on side
        if rule.side == "YES":
            price = opportunity["yes_ask"] or opportunity["yes_bid"]
        else:
            price = opportunity["no_ask"] or opportunity["no_bid"]

        if not price:
            return 0

        # Budget allocation
        budget = simulator.portfolio.balance * self.config.budget_per_position
        price_dollars = price / 100

        # Calculate quantity
        quantity = int(budget / price_dollars)

        return max(0, min(quantity, 100))  # Cap at 100 contracts

    def execute_trade(self, opportunity: dict, quantity: int) -> bool:
        """Execute a simulated trade."""
        rule = opportunity["rule"]
        ticker = opportunity["ticker"]

        # Get the right price
        if rule.side == "YES":
            price = opportunity["yes_ask"] or opportunity["yes_bid"]
        else:
            price = opportunity["no_ask"] or opportunity["no_bid"]

        if not price:
            return False

        # SAFETY CHECK: Never execute above max_odds no matter what
        if price > self.config.max_odds:
            console.print(f"[red]BLOCKED: Price {price}c exceeds max_odds {self.config.max_odds}c[/red]")
            return False

        # SAFETY CHECK: Ensure profit ratio is acceptable
        profit_if_win = 100 - price
        profit_ratio = profit_if_win / price if price > 0 else 0
        if profit_ratio < self.config.min_profit_ratio:
            console.print(f"[red]BLOCKED: Profit ratio {profit_ratio:.2f} below minimum {self.config.min_profit_ratio}[/red]")
            return False

        # Execute
        success = simulator.buy(
            ticker=ticker,
            side=rule.side.lower(),
            quantity=quantity,
            price=price
        )

        if success:
            # Record the trade
            self.traded_this_cycle.add(ticker)
            self.all_traded["traded"].append({
                "ticker": ticker,
                "series": rule.series,
                "side": rule.side,
                "quantity": quantity,
                "price": price,
                "rule_ev": rule.expected_ev,
                "rule_win_rate": rule.win_rate,
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "close_time": opportunity["close_time"].isoformat(),
            })
            self._save_traded()

        return success

    def display_opportunities(self, opportunities: list[dict]):
        """Display found opportunities."""
        if not opportunities:
            return

        # Show current price momentum if available
        if self.price_momentum:
            momentum_str = " | ".join(f"{s}: {m}" for s, m in self.price_momentum.items())
            console.print(f"[dim]Price momentum: {momentum_str}[/dim]")

        table = Table(title="Trading Opportunities", show_header=True, header_style="bold cyan")
        table.add_column("Series", width=10)
        table.add_column("Side", width=5)
        table.add_column("Odds", justify="right", width=6)
        table.add_column("Time", justify="right", width=6)
        table.add_column("EV", justify="right", width=7)
        table.add_column("Win%", justify="right", width=6)
        table.add_column("Momentum", width=8)
        table.add_column("Signal", width=8)

        for opp in opportunities:
            rule = opp["rule"]
            odds = opp["yes_bid"] if rule.side == "YES" else opp["no_bid"]

            ev_style = "green" if rule.expected_ev > 5 else "yellow"

            # Momentum display
            momentum = opp.get("momentum", "?")
            mom_style = "green" if momentum == "positive" else "red" if momentum == "negative" else "dim"

            # Price signal display
            signal = opp.get("price_signal")
            if signal:
                action = signal.get("action", "")
                if action == "BOOST":
                    signal_str = f"[green]BOOST[/green]"
                elif action == "AVOID":
                    signal_str = f"[red]AVOID[/red]"
                else:
                    signal_str = "[dim]-[/dim]"
            else:
                signal_str = "[dim]-[/dim]"

            table.add_row(
                rule.series,
                rule.side,
                f"{odds}%",
                f"{opp['seconds_to_close']:.0f}s",
                f"[{ev_style}]+{rule.expected_ev}c[/{ev_style}]",
                f"{rule.win_rate:.0f}%",
                f"[{mom_style}]{momentum}[/{mom_style}]",
                signal_str,
            )

        console.print(table)

    def display_status(self):
        """Display current status."""
        text = Text()
        text.append("Crypto Auto-Trader Status\n\n", style="bold")

        # Rules loaded
        text.append("Trading rules: ", style="dim")
        if self.trading_rules:
            text.append(f"{len(self.trading_rules)} rules loaded\n", style="green")
        else:
            text.append("No rules loaded\n", style="red")

        # Price signals
        text.append("Price signals: ", style="dim")
        if self.price_signals:
            text.append(f"{len(self.price_signals)} signals loaded\n", style="green")
        else:
            text.append("No signals yet (collecting data)\n", style="yellow")

        # Current momentum
        if self.price_momentum:
            text.append("Current momentum: ", style="dim")
            for symbol, momentum in self.price_momentum.items():
                style = "green" if momentum == "positive" else "red" if momentum == "negative" else "dim"
                text.append(f"{symbol}:", style="dim")
                text.append(f"{momentum} ", style=style)
            text.append("\n")

        # Analysis age
        if self.last_analysis_time:
            age = datetime.now(timezone.utc) - self.last_analysis_time
            age_str = f"{age.total_seconds() / 60:.0f} minutes ago"
            text.append("Analysis: ", style="dim")
            text.append(f"{age_str}\n")

        # Config
        text.append("\nConfiguration:\n", style="bold")
        text.append(f"  Safety buffer: {self.config.min_seconds_to_close}s before close\n")
        text.append(f"  Max time: {self.config.max_minutes_to_close} minutes to close\n")
        text.append(f"  Min EV: +{self.config.min_expected_ev}c per trade\n")
        text.append(f"  Min win rate: {self.config.min_win_rate}%\n")
        text.append(f"  Min samples: {self.config.min_samples}\n")
        text.append(f"  Odds range: {self.config.min_odds}%-{self.config.max_odds}%\n", style="green")
        text.append(f"  Min profit ratio: {self.config.min_profit_ratio:.0%} (win/risk)\n", style="green")
        text.append(f"  Budget per position: {self.config.budget_per_position * 100:.0f}%\n")
        text.append(f"  Max positions/cycle: {self.config.max_positions_per_cycle} (anti-correlation)\n", style="yellow")

        # Trades today
        trades_today = [t for t in self.all_traded.get("traded", [])
                       if t.get("executed_at", "").startswith(datetime.now().strftime("%Y-%m-%d"))]
        text.append(f"\nTrades today: {len(trades_today)}\n")

        console.print(Panel(text, title="Status", border_style="blue"))

    def display_rules(self, top_n: int = 10):
        """Display loaded trading rules."""
        if not self.trading_rules:
            console.print("[yellow]No trading rules loaded[/yellow]")
            return

        table = Table(title=f"Top {top_n} Trading Rules", show_header=True, header_style="bold cyan")
        table.add_column("Series", width=10)
        table.add_column("Side", width=5)
        table.add_column("Min Odds", justify="right", width=9)
        table.add_column("Time Window", width=12)
        table.add_column("EV", justify="right", width=8)
        table.add_column("Win%", justify="right", width=7)
        table.add_column("Samples", justify="right", width=8)
        table.add_column("Conf", width=6)

        for rule in self.trading_rules[:top_n]:
            ev_style = "green" if rule.expected_ev > 5 else "yellow"
            conf_style = "green" if rule.confidence == "high" else "yellow"

            table.add_row(
                rule.series,
                rule.side,
                f"{rule.min_odds}%",
                f"{rule.min_minutes:.1f}-{rule.max_minutes:.1f}m",
                f"[{ev_style}]+{rule.expected_ev}c[/{ev_style}]",
                f"{rule.win_rate:.0f}%",
                str(rule.samples),
                f"[{conf_style}]{rule.confidence}[/{conf_style}]",
            )

        console.print(table)

    def run_single_scan(self, auto_execute: bool = False) -> int:
        """Run a single scan and optionally execute trades."""
        # Reload rules
        if not self.load_trading_rules():
            return 0

        console.print(f"\n[dim]Scanning {len(CRYPTO_SERIES)} crypto series...[/dim]")
        opportunities = self.scan_markets()

        if not opportunities:
            console.print("[dim]No opportunities matching rules[/dim]")
            return 0

        self.display_opportunities(opportunities)

        if not auto_execute:
            return 0

        # Execute trades (up to max per cycle)
        executed = 0
        for opp in opportunities[:self.config.max_positions_per_cycle]:
            quantity = self.calculate_position_size(opp)
            if quantity > 0:
                rule = opp["rule"]
                console.print(f"\n[bold]Executing: {rule.side} {quantity}x {opp['ticker']}[/bold]")

                if self.execute_trade(opp, quantity):
                    console.print(f"[green]Trade successful![/green]")
                    executed += 1
                else:
                    console.print(f"[red]Trade failed[/red]")

        return executed

    def run_continuous(self):
        """Run continuous trading loop."""
        console.print(Panel(
            "[bold]Crypto Auto-Trader Started[/bold]\n\n"
            f"Scan interval: {self.config.scan_interval_seconds} seconds\n"
            f"Safety buffer: {self.config.min_seconds_to_close}s before close\n"
            f"Re-analyze every: {self.config.reanalyze_interval_minutes} minutes\n"
            f"Min expected value: +{self.config.min_expected_ev}c\n"
            f"Min win rate: {self.config.min_win_rate}%\n"
            f"[green]Odds range: {self.config.min_odds}%-{self.config.max_odds}% (optimized risk/reward)[/green]\n"
            f"[green]Min profit ratio: {self.config.min_profit_ratio:.0%} per dollar risked[/green]\n"
            f"[yellow]Max 1 position/cycle (avoids correlated losses)[/yellow]\n"
            f"[yellow]Budget: {self.config.budget_per_position*100:.0f}% per trade (Kelly-inspired)[/yellow]\n\n"
            "[bold cyan]Risk Management:[/bold cyan]\n"
            "  - Bid AND ask prices checked against limits\n"
            "  - Hard blocks on execution above max odds\n"
            "  - Profit ratio enforced at trade time\n"
            "  - Price correlation signals (real crypto prices)\n\n"
            "[dim]Press Ctrl+C to stop[/dim]",
            title="Data-Driven Trading (Risk-Optimized)",
            border_style="green"
        ))

        # Initial analysis
        if not self.trading_rules:
            self.reanalyze_data()

        if not self.trading_rules:
            console.print("[red]No trading rules available. Cannot start auto-trader.[/red]")
            console.print("[yellow]Run crypto_collector.py first to gather data, then crypto_analyzer.py[/yellow]")
            return

        # Load price signals (if available)
        self.load_price_signals()
        self.load_current_prices()

        console.print(f"\n[green]Loaded {len(self.trading_rules)} trading rules[/green]")
        if self.price_signals:
            console.print(f"[green]Loaded {len(self.price_signals)} price signals[/green]")
        else:
            console.print("[dim]No price signals yet - they'll appear as data accumulates[/dim]")
        self.display_rules(5)

        scan_count = 0
        total_trades = 0
        last_reanalyze = datetime.now()
        current_cycle = None  # Track 15-minute cycle

        try:
            while True:
                scan_count += 1
                now = datetime.now()

                # Determine current 15-minute cycle
                cycle_marker = now.strftime("%Y%m%d%H") + str(now.minute // 15)
                if cycle_marker != current_cycle:
                    # New cycle - reset traded set
                    current_cycle = cycle_marker
                    self.traded_this_cycle.clear()
                    console.print(f"\n[cyan]═══ New 15-min cycle: {now.strftime('%H:%M')} ═══[/cyan]")

                # Re-analyze periodically
                if (now - last_reanalyze).total_seconds() > self.config.reanalyze_interval_minutes * 60:
                    self.reanalyze_data()
                    last_reanalyze = now

                # Settle any completed positions first
                settled = simulator.settle_positions(verbose=True)
                if settled > 0:
                    console.print(f"[bold green]Settled {settled} position(s)[/bold green]")

                # Scan and trade
                console.print(f"\n[dim][{now.strftime('%H:%M:%S')}] Scan #{scan_count}[/dim]")
                trades = self.run_single_scan(auto_execute=True)
                total_trades += trades

                if trades > 0:
                    console.print(f"[green]Executed {trades} trade(s) this scan[/green]")

                # Wait for next scan
                time.sleep(self.config.scan_interval_seconds)

        except KeyboardInterrupt:
            # Final settlement check
            console.print(f"\n\n[yellow]Auto-trader stopped. Final settlement check...[/yellow]")
            simulator.settle_positions(verbose=True)

            console.print(f"\nTotal scans: {scan_count}")
            console.print(f"Total trades: {total_trades}")

            # Show settlement summary
            if simulator.portfolio.settlement_history:
                wins = sum(1 for s in simulator.portfolio.settlement_history if s.won)
                losses = len(simulator.portfolio.settlement_history) - wins
                total_pnl = sum(s.profit_loss for s in simulator.portfolio.settlement_history)
                console.print(f"\n[bold]Session Results:[/bold]")
                console.print(f"  Wins: [green]{wins}[/green], Losses: [red]{losses}[/red]")
                console.print(f"  Total P&L: [{'green' if total_pnl >= 0 else 'red'}]${total_pnl:+.2f}[/]")
            console.print(f"\n[bold]Final Balance: ${simulator.portfolio.balance:.2f}[/bold]")

    def clear_traded_history(self):
        """Clear trading history."""
        self.all_traded = {"traded": [], "results": {}}
        self.traded_this_cycle.clear()
        self._save_traded()
        console.print("[green]Traded history cleared[/green]")


# Global instance
crypto_autotrader = CryptoAutoTrader()
