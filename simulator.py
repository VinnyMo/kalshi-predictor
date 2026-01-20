"""Trade simulation logic with portfolio tracking and automatic settlement."""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from config import config
from kalshi_client import client, KalshiAPIError
from markets import fetch_market_detail, MarketSummary

console = Console()


@dataclass
class Position:
    """A position in a market."""
    ticker: str
    title: str
    side: str  # 'yes' or 'no'
    quantity: int
    avg_price: float  # in cents
    timestamp: str

    @property
    def cost_basis(self) -> float:
        """Total cost in dollars."""
        return (self.quantity * self.avg_price) / 100

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        return cls(**data)


@dataclass
class Trade:
    """Record of a simulated trade."""
    ticker: str
    title: str
    side: str  # 'yes' or 'no'
    action: str  # 'buy' or 'sell'
    quantity: int
    price: float  # in cents
    total: float  # in dollars
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Trade":
        return cls(**data)


@dataclass
class Settlement:
    """Record of a position settlement."""
    ticker: str
    title: str
    side: str  # 'yes' or 'no' - our side
    quantity: int
    avg_price: float  # what we paid in cents
    result: str  # 'yes' or 'no' - market result
    won: bool  # did we win?
    cost_basis: float  # what we paid in dollars
    payout: float  # what we received in dollars ($1 per contract if won, $0 if lost)
    profit_loss: float  # payout - cost_basis
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Settlement":
        return cls(**data)


@dataclass
class Portfolio:
    """Simulated portfolio state."""
    balance: float  # in dollars
    starting_balance: float
    positions: dict[str, Position] = field(default_factory=dict)  # ticker -> Position
    trade_history: list[Trade] = field(default_factory=list)
    settlement_history: list[Settlement] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "balance": self.balance,
            "starting_balance": self.starting_balance,
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "trade_history": [t.to_dict() for t in self.trade_history],
            "settlement_history": [s.to_dict() for s in self.settlement_history],
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Portfolio":
        positions = {k: Position.from_dict(v) for k, v in data.get("positions", {}).items()}
        trade_history = [Trade.from_dict(t) for t in data.get("trade_history", [])]
        settlement_history = [Settlement.from_dict(s) for s in data.get("settlement_history", [])]
        return cls(
            balance=data["balance"],
            starting_balance=data["starting_balance"],
            positions=positions,
            trade_history=trade_history,
            settlement_history=settlement_history,
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", "")
        )


class TradingSimulator:
    """Simulates trading with a dummy balance."""

    def __init__(self):
        config.ensure_data_dir()
        self.portfolio = self._load_portfolio()

    def _load_portfolio(self) -> Portfolio:
        """Load portfolio from file or create new one."""
        if config.portfolio_file.exists():
            try:
                with open(config.portfolio_file, "r") as f:
                    data = json.load(f)
                    return Portfolio.from_dict(data)
            except (json.JSONDecodeError, KeyError) as e:
                console.print(f"[yellow]Warning: Could not load portfolio, creating new one: {e}[/yellow]")

        return Portfolio(
            balance=config.starting_balance,
            starting_balance=config.starting_balance
        )

    def _save_portfolio(self):
        """Save portfolio to file."""
        self.portfolio.updated_at = datetime.now().isoformat()
        with open(config.portfolio_file, "w") as f:
            json.dump(self.portfolio.to_dict(), f, indent=2)

    def reset_portfolio(self, new_balance: float = None):
        """Reset portfolio to initial state."""
        balance = new_balance if new_balance is not None else config.starting_balance
        self.portfolio = Portfolio(
            balance=balance,
            starting_balance=balance
        )
        self._save_portfolio()
        console.print(f"[green]Portfolio reset. Starting balance: ${balance:.2f}[/green]")

    def buy(self, ticker: str, side: str, quantity: int, price: float) -> bool:
        """
        Execute a simulated buy order.

        Args:
            ticker: Market ticker
            side: 'yes' or 'no'
            quantity: Number of contracts
            price: Price in cents (1-99)

        Returns:
            True if successful, False otherwise
        """
        # Calculate total cost
        total_cost = (quantity * price) / 100  # Convert cents to dollars

        if total_cost > self.portfolio.balance:
            console.print(f"[red]Insufficient balance. Need ${total_cost:.2f}, have ${self.portfolio.balance:.2f}[/red]")
            return False

        # Get market title
        try:
            market_data = fetch_market_detail(ticker)
            title = market_data.get("market", {}).get("title", ticker)
        except:
            title = ticker

        # Update balance
        self.portfolio.balance -= total_cost

        # Update or create position
        position_key = f"{ticker}_{side}"
        if position_key in self.portfolio.positions:
            pos = self.portfolio.positions[position_key]
            # Calculate new average price
            total_qty = pos.quantity + quantity
            pos.avg_price = ((pos.quantity * pos.avg_price) + (quantity * price)) / total_qty
            pos.quantity = total_qty
        else:
            self.portfolio.positions[position_key] = Position(
                ticker=ticker,
                title=title,
                side=side,
                quantity=quantity,
                avg_price=price,
                timestamp=datetime.now().isoformat()
            )

        # Record trade
        trade = Trade(
            ticker=ticker,
            title=title,
            side=side,
            action="buy",
            quantity=quantity,
            price=price,
            total=total_cost,
            timestamp=datetime.now().isoformat()
        )
        self.portfolio.trade_history.append(trade)

        self._save_portfolio()
        console.print(f"[green]Bought {quantity} {side.upper()} contracts of {ticker} @ {price}c (${total_cost:.2f})[/green]")
        return True

    def sell(self, ticker: str, side: str, quantity: int, price: float) -> bool:
        """
        Execute a simulated sell order.

        Args:
            ticker: Market ticker
            side: 'yes' or 'no'
            quantity: Number of contracts to sell
            price: Price in cents (1-99)

        Returns:
            True if successful, False otherwise
        """
        position_key = f"{ticker}_{side}"

        if position_key not in self.portfolio.positions:
            console.print(f"[red]No position found for {ticker} ({side})[/red]")
            return False

        pos = self.portfolio.positions[position_key]
        if pos.quantity < quantity:
            console.print(f"[red]Insufficient position. Have {pos.quantity}, trying to sell {quantity}[/red]")
            return False

        # Calculate proceeds
        proceeds = (quantity * price) / 100

        # Update position
        pos.quantity -= quantity
        if pos.quantity == 0:
            del self.portfolio.positions[position_key]

        # Update balance
        self.portfolio.balance += proceeds

        # Record trade
        trade = Trade(
            ticker=pos.ticker,
            title=pos.title,
            side=side,
            action="sell",
            quantity=quantity,
            price=price,
            total=proceeds,
            timestamp=datetime.now().isoformat()
        )
        self.portfolio.trade_history.append(trade)

        self._save_portfolio()
        console.print(f"[green]Sold {quantity} {side.upper()} contracts of {ticker} @ {price}c (+${proceeds:.2f})[/green]")
        return True

    def settle_positions(self, verbose: bool = True) -> int:
        """
        Check all open positions and settle any that have finalized.

        For settled markets:
        - If we won (our side matches result): credit $1 per contract
        - If we lost: position expires worthless (we already paid)

        Returns:
            Number of positions settled
        """
        if not self.portfolio.positions:
            if verbose:
                console.print("[dim]No open positions to settle[/dim]")
            return 0

        settled_count = 0
        positions_to_remove = []

        for position_key, position in self.portfolio.positions.items():
            try:
                # Query the market status from API
                response = client.get(f"/markets/{position.ticker}")
                market = response.get("market", {})

                status = market.get("status", "").lower()

                # Check if market is settled/finalized
                if status not in ["settled", "finalized"]:
                    continue

                result = market.get("result", "").lower()
                if result not in ["yes", "no"]:
                    continue

                # Determine if we won
                won = (position.side == result)

                # Calculate payout
                cost_basis = (position.quantity * position.avg_price) / 100
                if won:
                    # We get $1 per contract
                    payout = position.quantity * 1.0
                else:
                    # Position expires worthless
                    payout = 0.0

                profit_loss = payout - cost_basis

                # Credit the payout to balance
                self.portfolio.balance += payout

                # Record the settlement
                settlement = Settlement(
                    ticker=position.ticker,
                    title=position.title,
                    side=position.side,
                    quantity=position.quantity,
                    avg_price=position.avg_price,
                    result=result,
                    won=won,
                    cost_basis=cost_basis,
                    payout=payout,
                    profit_loss=profit_loss,
                    timestamp=datetime.now().isoformat()
                )
                self.portfolio.settlement_history.append(settlement)

                positions_to_remove.append(position_key)
                settled_count += 1

                if verbose:
                    if won:
                        console.print(
                            f"[green]SETTLED: {position.ticker} - {position.side.upper()} WON! "
                            f"+${payout:.2f} payout, P&L: ${profit_loss:+.2f}[/green]"
                        )
                    else:
                        console.print(
                            f"[red]SETTLED: {position.ticker} - {position.side.upper()} LOST. "
                            f"Result was {result.upper()}, P&L: ${profit_loss:+.2f}[/red]"
                        )

            except KalshiAPIError as e:
                if verbose:
                    console.print(f"[yellow]Could not check {position.ticker}: {e.message}[/yellow]")
            except Exception as e:
                if verbose:
                    console.print(f"[yellow]Error checking {position.ticker}: {e}[/yellow]")

        # Remove settled positions
        for key in positions_to_remove:
            del self.portfolio.positions[key]

        if settled_count > 0:
            self._save_portfolio()
            if verbose:
                console.print(f"\n[bold]Settled {settled_count} position(s). New balance: ${self.portfolio.balance:.2f}[/bold]")

        return settled_count

    def get_position_value(self, position: Position) -> tuple[float, float]:
        """
        Calculate current value and P&L for a position.

        Returns:
            Tuple of (current_value, unrealized_pnl) in dollars
        """
        try:
            market_data = fetch_market_detail(position.ticker)
            market = market_data.get("market", {})

            # Get current price based on side
            if position.side == "yes":
                current_price = market.get("yes_bid", position.avg_price)
            else:
                current_price = market.get("no_bid", position.avg_price)

            if current_price is None:
                current_price = position.avg_price

            current_value = (position.quantity * current_price) / 100
            cost_basis = (position.quantity * position.avg_price) / 100
            unrealized_pnl = current_value - cost_basis

            return current_value, unrealized_pnl

        except Exception:
            # If we can't fetch, use cost basis
            cost_basis = (position.quantity * position.avg_price) / 100
            return cost_basis, 0.0

    def display_portfolio(self):
        """Display portfolio summary."""
        # Balance info
        balance_text = Text()
        balance_text.append("Cash Balance: ", style="bold")
        balance_text.append(f"${self.portfolio.balance:.2f}\n", style="green" if self.portfolio.balance > 0 else "red")
        balance_text.append("Starting Balance: ", style="bold")
        balance_text.append(f"${self.portfolio.starting_balance:.2f}\n")

        console.print(Panel(balance_text, title="Portfolio Summary", border_style="blue"))

        # Positions table
        if self.portfolio.positions:
            table = Table(title="Open Positions", show_header=True, header_style="bold cyan")
            table.add_column("Ticker", style="green", width=20)
            table.add_column("Side", width=6)
            table.add_column("Qty", justify="right", width=6)
            table.add_column("Avg Price", justify="right", width=10)
            table.add_column("Cost Basis", justify="right", width=12)
            table.add_column("Current", justify="right", width=12)
            table.add_column("P&L", justify="right", width=12)

            total_value = 0.0
            total_pnl = 0.0

            for position in self.portfolio.positions.values():
                current_value, pnl = self.get_position_value(position)
                total_value += current_value
                total_pnl += pnl

                pnl_style = "green" if pnl >= 0 else "red"
                pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

                table.add_row(
                    position.ticker,
                    position.side.upper(),
                    str(position.quantity),
                    f"{position.avg_price:.1f}c",
                    f"${position.cost_basis:.2f}",
                    f"${current_value:.2f}",
                    Text(pnl_str, style=pnl_style)
                )

            console.print(table)

            # Total summary
            total_portfolio = self.portfolio.balance + total_value
            overall_pnl = total_portfolio - self.portfolio.starting_balance
            pnl_style = "green" if overall_pnl >= 0 else "red"

            summary = Text()
            summary.append("\nPosition Value: ", style="bold")
            summary.append(f"${total_value:.2f}\n")
            summary.append("Total Portfolio: ", style="bold")
            summary.append(f"${total_portfolio:.2f}\n")
            summary.append("Overall P&L: ", style="bold")
            pnl_str = f"+${overall_pnl:.2f}" if overall_pnl >= 0 else f"-${abs(overall_pnl):.2f}"
            summary.append(f"{pnl_str} ({(overall_pnl/self.portfolio.starting_balance)*100:+.1f}%)\n", style=pnl_style)

            console.print(Panel(summary, border_style="blue"))
        else:
            console.print("[dim]No open positions[/dim]")

    def display_trade_history(self, limit: int = 20):
        """Display recent trade history."""
        if not self.portfolio.trade_history:
            console.print("[dim]No trade history[/dim]")
            return

        table = Table(title=f"Trade History (Last {limit})", show_header=True, header_style="bold cyan")
        table.add_column("Time", width=20)
        table.add_column("Ticker", style="green", width=18)
        table.add_column("Action", width=8)
        table.add_column("Side", width=6)
        table.add_column("Qty", justify="right", width=6)
        table.add_column("Price", justify="right", width=8)
        table.add_column("Total", justify="right", width=10)

        for trade in reversed(self.portfolio.trade_history[-limit:]):
            action_style = "green" if trade.action == "buy" else "yellow"
            timestamp = trade.timestamp[:19].replace("T", " ")

            table.add_row(
                timestamp,
                trade.ticker,
                Text(trade.action.upper(), style=action_style),
                trade.side.upper(),
                str(trade.quantity),
                f"{trade.price:.1f}c",
                f"${trade.total:.2f}"
            )

        console.print(table)

    def display_settlement_history(self, limit: int = 20):
        """Display recent settlement history."""
        if not self.portfolio.settlement_history:
            console.print("[dim]No settlement history[/dim]")
            return

        # Calculate totals
        total_wins = sum(1 for s in self.portfolio.settlement_history if s.won)
        total_losses = len(self.portfolio.settlement_history) - total_wins
        total_pnl = sum(s.profit_loss for s in self.portfolio.settlement_history)

        # Summary
        summary = Text()
        summary.append("Settlement Summary\n\n", style="bold")
        summary.append(f"Total Settled: {len(self.portfolio.settlement_history)}\n")
        summary.append(f"Wins: ", style="bold")
        summary.append(f"{total_wins}\n", style="green")
        summary.append(f"Losses: ", style="bold")
        summary.append(f"{total_losses}\n", style="red")
        summary.append(f"Win Rate: ", style="bold")
        win_rate = (total_wins / len(self.portfolio.settlement_history) * 100) if self.portfolio.settlement_history else 0
        summary.append(f"{win_rate:.1f}%\n")
        summary.append(f"Total P&L: ", style="bold")
        pnl_style = "green" if total_pnl >= 0 else "red"
        summary.append(f"${total_pnl:+.2f}\n", style=pnl_style)

        console.print(Panel(summary, border_style="blue"))

        # Table
        table = Table(title=f"Settlement History (Last {limit})", show_header=True, header_style="bold cyan")
        table.add_column("Time", width=20)
        table.add_column("Ticker", width=24)
        table.add_column("Side", width=6)
        table.add_column("Result", width=7)
        table.add_column("Outcome", width=8)
        table.add_column("Qty", justify="right", width=5)
        table.add_column("Cost", justify="right", width=8)
        table.add_column("Payout", justify="right", width=8)
        table.add_column("P&L", justify="right", width=10)

        for settlement in reversed(self.portfolio.settlement_history[-limit:]):
            timestamp = settlement.timestamp[:19].replace("T", " ")
            outcome_style = "green" if settlement.won else "red"
            outcome_text = "WON" if settlement.won else "LOST"
            pnl_str = f"${settlement.profit_loss:+.2f}"

            table.add_row(
                timestamp,
                settlement.ticker[:22] + ".." if len(settlement.ticker) > 24 else settlement.ticker,
                settlement.side.upper(),
                settlement.result.upper(),
                Text(outcome_text, style=outcome_style),
                str(settlement.quantity),
                f"${settlement.cost_basis:.2f}",
                f"${settlement.payout:.2f}",
                Text(pnl_str, style=outcome_style)
            )

        console.print(table)


# Global simulator instance
simulator = TradingSimulator()
