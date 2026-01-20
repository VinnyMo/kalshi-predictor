"""Automated trading logic for expiring markets."""

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.spinner import Spinner

from config import config
from kalshi_client import client, KalshiAPIError
from simulator import simulator

console = Console()

# File to track positions we've already bid on
TRADED_POSITIONS_FILE = config.data_dir / "auto_traded.json"


@dataclass
class AutoTraderConfig:
    """Configuration for automated trading."""
    max_expiry_minutes: int = 30  # Only markets expiring within this time
    min_odds_threshold: int = 70  # Minimum odds on yes or no (70 = 70%)
    max_odds_threshold: int = 99  # Maximum odds (exclude 100% - no profit)
    target_odds: int = 75  # Ideal odds to rank candidates by
    budget_per_position: float = 0.10  # 10% of balance per position
    max_positions: int = 10  # Maximum positions per scan
    scan_interval_seconds: int = 300  # 5 minutes
    min_position_size: int = 1  # Minimum contracts per trade


@dataclass
class CandidateMarket:
    """A market that qualifies for auto-trading."""
    ticker: str
    title: str
    event_ticker: str  # Event this market belongs to (for grouping)
    side: str  # 'yes' or 'no' - which side to bet on
    odds: int  # The odds percentage for our chosen side
    price: int  # Price in cents we'd pay
    expires_at: datetime
    minutes_to_expiry: float


class AutoTrader:
    """Automated trading engine."""

    def __init__(self):
        self.config = AutoTraderConfig()
        self.traded_positions: set[str] = self._load_traded_positions()

    def _load_traded_positions(self) -> set[str]:
        """Load set of position keys we've already traded."""
        if TRADED_POSITIONS_FILE.exists():
            try:
                with open(TRADED_POSITIONS_FILE, "r") as f:
                    data = json.load(f)
                    return set(data.get("traded", []))
            except (json.JSONDecodeError, KeyError):
                pass
        return set()

    def _save_traded_positions(self):
        """Save traded positions to file."""
        with open(TRADED_POSITIONS_FILE, "w") as f:
            json.dump({"traded": list(self.traded_positions)}, f, indent=2)

    def _get_position_key(self, ticker: str, side: str) -> str:
        """Generate unique key for a position."""
        return f"{ticker}_{side}"

    def _parse_timestamp(self, ts_str: str) -> Optional[datetime]:
        """Parse ISO timestamp from API."""
        if not ts_str:
            return None
        try:
            # Handle various ISO formats
            ts_str = ts_str.replace("Z", "+00:00")
            return datetime.fromisoformat(ts_str)
        except ValueError:
            return None

    def scan_expiring_markets(self) -> list[CandidateMarket]:
        """Scan for markets expiring soon that meet our criteria."""
        candidates = []
        now = datetime.now(timezone.utc)
        max_expiry = now.timestamp() + (self.config.max_expiry_minutes * 60)

        cursor = None
        scanned = 0

        while True:
            try:
                response = client.get_markets(status="open", limit=200, cursor=cursor)
                markets = response.get("markets", [])

                if not markets:
                    break

                for market in markets:
                    scanned += 1
                    candidate = self._evaluate_market(market, now, max_expiry)
                    if candidate:
                        candidates.append(candidate)

                cursor = response.get("cursor")
                if not cursor:
                    break

            except KalshiAPIError as e:
                console.print(f"[red]API Error during scan: {e.message}[/red]")
                break

        console.print(f"[dim]Scanned {scanned} markets, found {len(candidates)} candidates[/dim]")
        return candidates

    def _evaluate_market(self, market: dict, now: datetime, max_expiry_ts: float) -> Optional[CandidateMarket]:
        """Evaluate if a market qualifies for auto-trading."""
        ticker = market.get("ticker", "")

        # Check if we've already traded this
        yes_key = self._get_position_key(ticker, "yes")
        no_key = self._get_position_key(ticker, "no")
        if yes_key in self.traded_positions or no_key in self.traded_positions:
            return None

        # Check expiration time - try multiple field names
        expiry_str = market.get("close_time") or market.get("expiration_time") or market.get("end_date")
        if not expiry_str:
            return None

        expires_at = self._parse_timestamp(expiry_str)
        if not expires_at:
            return None

        # Check if expiring within our window
        expiry_ts = expires_at.timestamp()
        if expiry_ts > max_expiry_ts or expiry_ts < now.timestamp():
            return None

        minutes_to_expiry = (expiry_ts - now.timestamp()) / 60

        # Get prices - yes_bid is what we'd get selling, yes_ask is what we'd pay buying
        yes_ask = market.get("yes_ask")  # Price to buy yes
        no_ask = market.get("no_ask")    # Price to buy no

        if not yes_ask and not no_ask:
            return None

        # Calculate implied odds
        # If yes_ask is 75c, implied yes odds are ~75%
        # If no_ask is 25c, implied no odds are ~75% (since no at 25 = yes at 75)

        # Determine which side has 70%+ odds
        yes_odds = yes_ask if yes_ask else (100 - no_ask if no_ask else None)
        no_odds = no_ask if no_ask else (100 - yes_ask if yes_ask else None)

        side = None
        odds = None
        price = None

        # We want to BUY the side with high odds (betting on the favorite)
        # But exclude 100% odds (no profit potential)
        if yes_odds and self.config.min_odds_threshold <= yes_odds <= self.config.max_odds_threshold:
            side = "yes"
            odds = yes_odds
            price = yes_ask
        elif no_odds and self.config.min_odds_threshold <= no_odds <= self.config.max_odds_threshold:
            side = "no"
            odds = no_odds
            price = no_ask

        if not side:
            return None

        # Get event ticker for grouping (to avoid betting on contradictory markets)
        event_ticker = market.get("event_ticker", ticker)

        return CandidateMarket(
            ticker=ticker,
            title=market.get("title", ticker),
            event_ticker=event_ticker,
            side=side,
            odds=odds,
            price=price,
            expires_at=expires_at,
            minutes_to_expiry=minutes_to_expiry
        )

    def calculate_positions(self, candidates: list[CandidateMarket]) -> list[tuple[CandidateMarket, int]]:
        """
        Calculate how many contracts to buy for each candidate.

        Groups by event (only one market per event to avoid contradictory bets),
        ranks by closest to target odds (75%), takes top 10, each gets 10% of balance.
        Returns list of (candidate, quantity) tuples.
        """
        if not candidates:
            return []

        available_balance = simulator.portfolio.balance
        budget_per_position = available_balance * self.config.budget_per_position

        if budget_per_position < 0.01:
            console.print("[yellow]Insufficient balance for auto-trading[/yellow]")
            return []

        # Group by event and keep only the best candidate per event
        # (closest to target odds) to avoid betting on contradictory markets
        event_best: dict[str, CandidateMarket] = {}
        for candidate in candidates:
            event = candidate.event_ticker
            if event not in event_best:
                event_best[event] = candidate
            else:
                # Keep the one closest to target odds
                current_distance = abs(event_best[event].odds - self.config.target_odds)
                new_distance = abs(candidate.odds - self.config.target_odds)
                if new_distance < current_distance:
                    event_best[event] = candidate

        deduplicated = list(event_best.values())
        console.print(f"[dim]Deduplicated to {len(deduplicated)} unique events (from {len(candidates)} candidates)[/dim]")

        # Rank by how close they are to target odds (75%)
        ranked = sorted(deduplicated, key=lambda c: abs(c.odds - self.config.target_odds))

        # Take top N candidates
        top_candidates = ranked[:self.config.max_positions]

        console.print(f"[dim]Selected top {len(top_candidates)} candidates (closest to {self.config.target_odds}% odds)[/dim]")

        positions = []
        for candidate in top_candidates:
            # Calculate how many contracts we can buy with 10% of balance
            price_dollars = candidate.price / 100
            quantity = int(budget_per_position / price_dollars)

            if quantity >= self.config.min_position_size:
                positions.append((candidate, quantity))

        return positions

    def execute_trades(self, positions: list[tuple[CandidateMarket, int]]) -> int:
        """Execute simulated trades for calculated positions. Returns count of successful trades."""
        successful = 0

        for candidate, quantity in positions:
            position_key = self._get_position_key(candidate.ticker, candidate.side)

            # Double-check we haven't traded this
            if position_key in self.traded_positions:
                continue

            # Execute the trade
            success = simulator.buy(
                ticker=candidate.ticker,
                side=candidate.side,
                quantity=quantity,
                price=candidate.price
            )

            if success:
                self.traded_positions.add(position_key)
                successful += 1

        self._save_traded_positions()
        return successful

    def display_candidates(self, candidates: list[CandidateMarket]):
        """Display candidate markets in a table."""
        if not candidates:
            console.print("[yellow]No qualifying markets found[/yellow]")
            return

        table = Table(title="Qualifying Markets", show_header=True, header_style="bold cyan")
        table.add_column("Event", style="dim", width=18)
        table.add_column("Ticker", style="green", width=20)
        table.add_column("Title", width=30)
        table.add_column("Side", width=5)
        table.add_column("Odds", justify="right", width=5)
        table.add_column("Price", justify="right", width=6)
        table.add_column("Expires", justify="right", width=8)

        for c in candidates:
            table.add_row(
                c.event_ticker[:16] + ".." if len(c.event_ticker) > 18 else c.event_ticker,
                c.ticker[:18] + ".." if len(c.ticker) > 20 else c.ticker,
                c.title[:28] + ".." if len(c.title) > 30 else c.title,
                c.side.upper(),
                f"{c.odds}%",
                f"{c.price}c",
                f"{c.minutes_to_expiry:.0f}m"
            )

        console.print(table)

    def display_planned_trades(self, positions: list[tuple[CandidateMarket, int]]):
        """Display planned trades before execution."""
        if not positions:
            console.print("[yellow]No trades to execute (insufficient budget or position size)[/yellow]")
            return

        total_cost = 0
        table = Table(title="Planned Trades (1 per event)", show_header=True, header_style="bold cyan")
        table.add_column("Event", style="dim", width=18)
        table.add_column("Ticker", style="green", width=20)
        table.add_column("Side", width=5)
        table.add_column("Odds", justify="right", width=5)
        table.add_column("Qty", justify="right", width=5)
        table.add_column("Price", justify="right", width=6)
        table.add_column("Cost", justify="right", width=8)

        for candidate, quantity in positions:
            cost = (quantity * candidate.price) / 100
            total_cost += cost
            table.add_row(
                candidate.event_ticker[:16] + ".." if len(candidate.event_ticker) > 18 else candidate.event_ticker,
                candidate.ticker[:18] + ".." if len(candidate.ticker) > 20 else candidate.ticker,
                candidate.side.upper(),
                f"{candidate.odds}%",
                str(quantity),
                f"{candidate.price}c",
                f"${cost:.2f}"
            )

        console.print(table)
        console.print(f"\n[bold]Total cost: ${total_cost:.2f}[/bold]")
        console.print(f"[dim]Available balance: ${simulator.portfolio.balance:.2f}[/dim]")

    def run_single_scan(self, auto_execute: bool = False) -> int:
        """
        Run a single scan and optionally execute trades.

        Returns number of trades executed.
        """
        console.print("\n[bold]Scanning for expiring markets...[/bold]")
        console.print(f"[dim]Criteria: Expiring <{self.config.max_expiry_minutes}min, odds {self.config.min_odds_threshold}%-{self.config.max_odds_threshold}%, target {self.config.target_odds}%[/dim]")

        candidates = self.scan_expiring_markets()
        self.display_candidates(candidates)

        if not candidates:
            return 0

        positions = self.calculate_positions(candidates)
        self.display_planned_trades(positions)

        if not positions:
            return 0

        if auto_execute:
            return self.execute_trades(positions)

        return 0

    def run_continuous(self):
        """Run continuous scanning loop until interrupted."""
        console.print(Panel(
            f"[bold]Auto-Trader Started[/bold]\n\n"
            f"Scan interval: {self.config.scan_interval_seconds // 60} minutes\n"
            f"Max expiry: {self.config.max_expiry_minutes} minutes\n"
            f"Odds range: {self.config.min_odds_threshold}%-{self.config.max_odds_threshold}%\n"
            f"Target odds: {self.config.target_odds}%\n"
            f"Budget per position: {self.config.budget_per_position * 100:.0f}%\n"
            f"Max positions: {self.config.max_positions}\n\n"
            f"[dim]Press Ctrl+C to stop[/dim]",
            title="Auto-Trading Mode",
            border_style="green"
        ))

        scan_count = 0
        total_trades = 0

        try:
            while True:
                scan_count += 1
                console.print(f"\n[bold cyan]═══ Scan #{scan_count} @ {datetime.now().strftime('%H:%M:%S')} ═══[/bold cyan]")

                trades = self.run_single_scan(auto_execute=True)
                total_trades += trades

                if trades > 0:
                    console.print(f"[green]Executed {trades} trades[/green]")
                else:
                    console.print("[dim]No trades this scan[/dim]")

                console.print(f"\n[dim]Next scan in {self.config.scan_interval_seconds // 60} minutes... (Ctrl+C to stop)[/dim]")
                time.sleep(self.config.scan_interval_seconds)

        except KeyboardInterrupt:
            console.print(f"\n[yellow]Auto-trader stopped. Total scans: {scan_count}, Total trades: {total_trades}[/yellow]")

    def clear_traded_history(self):
        """Clear the list of traded positions (allows re-trading)."""
        self.traded_positions.clear()
        self._save_traded_positions()
        console.print("[green]Traded positions history cleared[/green]")

    def display_status(self):
        """Display current auto-trader status."""
        text = Text()
        text.append("Auto-Trader Configuration\n\n", style="bold")
        text.append("Max expiry: ", style="dim")
        text.append(f"{self.config.max_expiry_minutes} minutes\n")
        text.append("Odds range: ", style="dim")
        text.append(f"{self.config.min_odds_threshold}% - {self.config.max_odds_threshold}%\n")
        text.append("Target odds: ", style="dim")
        text.append(f"{self.config.target_odds}% (ranks closest first)\n")
        text.append("Budget per position: ", style="dim")
        text.append(f"{self.config.budget_per_position * 100:.0f}% of balance\n")
        text.append("Max positions: ", style="dim")
        text.append(f"{self.config.max_positions}\n")
        text.append("Scan interval: ", style="dim")
        text.append(f"{self.config.scan_interval_seconds // 60} minutes\n\n")
        text.append("Positions already traded: ", style="dim")
        text.append(f"{len(self.traded_positions)}\n")

        if self.traded_positions:
            text.append("\nTraded positions:\n", style="bold")
            for pos in sorted(self.traded_positions):
                text.append(f"  - {pos}\n", style="dim")

        console.print(Panel(text, title="Auto-Trader Status", border_style="blue"))


# Global autotrader instance
autotrader = AutoTrader()
