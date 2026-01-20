#!/usr/bin/env python3
"""Kalshi Trading Console - Main entry point."""

import sys
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt, IntPrompt, FloatPrompt, Confirm

from config import config
from kalshi_client import client, KalshiAPIError
from markets import (
    fetch_markets,
    display_markets_table,
    display_market_detail,
    search_markets,
    MarketSummary
)
from simulator import simulator
from autotrader import autotrader
from crypto_autotrader import crypto_autotrader

console = Console()


def print_header():
    """Print application header."""
    header = Text()
    header.append("Kalshi Trading Console\n", style="bold blue")
    header.append(f"Environment: ", style="dim")
    header.append(f"{config.environment.upper()}\n", style="yellow" if config.environment == "demo" else "red")
    header.append(f"Balance: ", style="dim")
    header.append(f"${simulator.portfolio.balance:.2f}", style="green")
    console.print(Panel(header, border_style="blue"))


def print_menu():
    """Print main menu."""
    menu = """
[bold]Main Menu[/bold]
  [cyan]1[/cyan] - Browse Markets
  [cyan]2[/cyan] - Search Markets
  [cyan]3[/cyan] - View Market Details
  [cyan]4[/cyan] - Place Simulated Trade
  [cyan]5[/cyan] - View Portfolio
  [cyan]6[/cyan] - Trade History
  [cyan]7[/cyan] - [bold yellow]Settle Positions & View Settlements[/bold yellow]
  [cyan]8[/cyan] - Check API Connection
  [cyan]9[/cyan] - Reset Portfolio
  [cyan]A[/cyan] - Auto-Trader (General)
  [cyan]C[/cyan] - [bold green]Crypto Auto-Trader (Data-Driven)[/bold green]
  [cyan]0[/cyan] - Exit
"""
    console.print(menu)


def browse_markets():
    """Browse available markets with pagination."""
    cursor = None
    page = 1

    while True:
        try:
            console.print("\n[dim]Fetching markets...[/dim]")
            markets, next_cursor = fetch_markets(status="open", limit=15, cursor=cursor)

            if not markets:
                console.print("[yellow]No markets found[/yellow]")
                return

            display_markets_table(markets, page)

            # Navigation options
            nav = Text()
            nav.append("\n[N]ext page  " if next_cursor else "", style="cyan")
            nav.append("[P]revious page  " if page > 1 else "", style="cyan")
            nav.append("[#] View market by number  ", style="cyan")
            nav.append("[Q]uit to menu", style="cyan")

            console.print(nav)
            choice = Prompt.ask("Choice", default="q").lower().strip()

            if choice == "q":
                return
            elif choice == "n" and next_cursor:
                cursor = next_cursor
                page += 1
            elif choice == "p" and page > 1:
                # Note: Kalshi API doesn't support backward pagination easily
                # For simplicity, restart from beginning
                cursor = None
                page = 1
            elif choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(markets):
                    display_market_detail(markets[idx].ticker)
                    Prompt.ask("\nPress Enter to continue")

        except KalshiAPIError as e:
            console.print(f"[red]API Error: {e.message}[/red]")
            return


def search_markets_menu():
    """Search markets by keyword."""
    query = Prompt.ask("Search query")
    if not query:
        return

    try:
        console.print("[dim]Searching...[/dim]")
        results = search_markets(query)

        if not results:
            console.print(f"[yellow]No markets found matching '{query}'[/yellow]")
            return

        display_markets_table(results[:20])

        if len(results) > 20:
            console.print(f"[dim]Showing 20 of {len(results)} results[/dim]")

        # Option to view details
        choice = Prompt.ask("\nEnter number to view details (or press Enter to skip)", default="")
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(results):
                display_market_detail(results[idx].ticker)

    except KalshiAPIError as e:
        console.print(f"[red]API Error: {e.message}[/red]")


def view_market_menu():
    """View specific market by ticker."""
    ticker = Prompt.ask("Enter market ticker").upper().strip()
    if not ticker:
        return

    display_market_detail(ticker)
    Prompt.ask("\nPress Enter to continue")


def place_trade_menu():
    """Place a simulated trade."""
    console.print("\n[bold]Place Simulated Trade[/bold]")
    console.print(f"[dim]Available balance: ${simulator.portfolio.balance:.2f}[/dim]\n")

    # Get ticker
    ticker = Prompt.ask("Market ticker").upper().strip()
    if not ticker:
        return

    # Show current prices
    try:
        display_market_detail(ticker)
    except KalshiAPIError as e:
        console.print(f"[red]Could not fetch market: {e.message}[/red]")
        return

    console.print()

    # Get trade details
    side = Prompt.ask("Side", choices=["yes", "no"], default="yes")
    action = Prompt.ask("Action", choices=["buy", "sell"], default="buy")
    quantity = IntPrompt.ask("Quantity (contracts)", default=1)
    price = FloatPrompt.ask("Price (cents, 1-99)")

    if price < 1 or price > 99:
        console.print("[red]Price must be between 1 and 99 cents[/red]")
        return

    # Calculate and confirm
    total = (quantity * price) / 100
    console.print(f"\n[bold]Trade Summary:[/bold]")
    console.print(f"  {action.upper()} {quantity} {side.upper()} @ {price}c")
    console.print(f"  Total: ${total:.2f}")

    if not Confirm.ask("Confirm trade?"):
        console.print("[yellow]Trade cancelled[/yellow]")
        return

    # Execute
    if action == "buy":
        simulator.buy(ticker, side, quantity, price)
    else:
        simulator.sell(ticker, side, quantity, price)


def check_connection():
    """Test API connection."""
    console.print("\n[dim]Testing API connection...[/dim]")

    try:
        # Try to fetch balance (requires auth)
        balance = client.get_balance()
        console.print("[green]Connection successful![/green]")
        console.print(f"API Balance: ${balance.get('balance', 0) / 100:.2f}")
    except KalshiAPIError as e:
        console.print(f"[red]Connection failed: {e.message}[/red]")
        if e.status_code == 401:
            console.print("[yellow]Check your API credentials in .env file[/yellow]")


def reset_portfolio_menu():
    """Reset portfolio to initial state."""
    console.print(f"\n[bold]Current balance: ${simulator.portfolio.balance:.2f}[/bold]")
    console.print(f"Starting balance was: ${simulator.portfolio.starting_balance:.2f}")

    if simulator.portfolio.positions:
        console.print(f"[yellow]Warning: You have {len(simulator.portfolio.positions)} open positions[/yellow]")

    if not Confirm.ask("Reset portfolio?", default=False):
        console.print("[yellow]Cancelled[/yellow]")
        return

    new_balance = FloatPrompt.ask("New starting balance", default=100.0)
    simulator.reset_portfolio(new_balance)


def auto_trader_menu():
    """Auto-trader submenu."""
    while True:
        submenu = """
[bold]Auto-Trader Menu[/bold] (General - any market)
  [cyan]1[/cyan] - Scan Once (Preview Only)
  [cyan]2[/cyan] - Scan & Execute Once
  [cyan]3[/cyan] - Start Continuous Mode (5 min intervals)
  [cyan]4[/cyan] - View Status & Config
  [cyan]5[/cyan] - Clear Traded History (allow re-trading)
  [cyan]0[/cyan] - Back to Main Menu
"""
        console.print(submenu)

        choice = Prompt.ask("Select option", default="0")

        if choice == "0":
            return
        elif choice == "1":
            autotrader.run_single_scan(auto_execute=False)
            Prompt.ask("\nPress Enter to continue")
        elif choice == "2":
            autotrader.run_single_scan(auto_execute=False)
            if Confirm.ask("\nExecute these trades?", default=False):
                candidates = autotrader.scan_expiring_markets()
                positions = autotrader.calculate_positions(candidates)
                executed = autotrader.execute_trades(positions)
                console.print(f"[green]Executed {executed} trades[/green]")
            Prompt.ask("\nPress Enter to continue")
        elif choice == "3":
            console.print("\n[yellow]Starting continuous auto-trading mode...[/yellow]")
            console.print("[dim]This will scan every 5 minutes and auto-execute trades.[/dim]")
            if Confirm.ask("Continue?", default=False):
                autotrader.run_continuous()
            Prompt.ask("\nPress Enter to continue")
        elif choice == "4":
            autotrader.display_status()
            Prompt.ask("\nPress Enter to continue")
        elif choice == "5":
            if Confirm.ask("Clear traded positions history?", default=False):
                autotrader.clear_traded_history()
            Prompt.ask("\nPress Enter to continue")
        else:
            console.print("[yellow]Invalid option[/yellow]")


def crypto_auto_trader_menu():
    """Crypto auto-trader submenu - uses data-driven strategies."""
    while True:
        submenu = """
[bold green]Crypto Auto-Trader Menu[/bold green] (Data-Driven)
  [cyan]1[/cyan] - View Trading Rules (from analysis)
  [cyan]2[/cyan] - Scan Once (Preview Only)
  [cyan]3[/cyan] - Scan & Execute Once
  [cyan]4[/cyan] - Start Continuous Mode (10 sec intervals)
  [cyan]5[/cyan] - Re-run Analysis (update rules)
  [cyan]6[/cyan] - View Status
  [cyan]7[/cyan] - Clear Traded History
  [cyan]0[/cyan] - Back to Main Menu
"""
        console.print(submenu)

        choice = Prompt.ask("Select option", default="0")

        if choice == "0":
            return
        elif choice == "1":
            crypto_autotrader.load_trading_rules()
            crypto_autotrader.display_rules(20)
            Prompt.ask("\nPress Enter to continue")
        elif choice == "2":
            crypto_autotrader.run_single_scan(auto_execute=False)
            Prompt.ask("\nPress Enter to continue")
        elif choice == "3":
            crypto_autotrader.run_single_scan(auto_execute=False)
            if Confirm.ask("\nExecute these trades?", default=False):
                executed = crypto_autotrader.run_single_scan(auto_execute=True)
                console.print(f"[green]Executed {executed} trades[/green]")
            Prompt.ask("\nPress Enter to continue")
        elif choice == "4":
            console.print("\n[bold green]Starting DATA-DRIVEN crypto auto-trading...[/bold green]")
            console.print("[dim]Uses strategies learned from historical data![/dim]")
            console.print("[dim]Scans every 10 seconds, re-analyzes every 15 minutes.[/dim]")
            if Confirm.ask("Continue?", default=False):
                crypto_autotrader.run_continuous()
            Prompt.ask("\nPress Enter to continue")
        elif choice == "5":
            crypto_autotrader.reanalyze_data()
            Prompt.ask("\nPress Enter to continue")
        elif choice == "6":
            crypto_autotrader.display_status()
            Prompt.ask("\nPress Enter to continue")
        elif choice == "7":
            if Confirm.ask("Clear traded history?", default=False):
                crypto_autotrader.clear_traded_history()
            Prompt.ask("\nPress Enter to continue")
        else:
            console.print("[yellow]Invalid option[/yellow]")


def main():
    """Main application loop."""
    # Validate configuration
    is_valid, errors = config.validate()
    if not is_valid:
        console.print("[bold red]Configuration Error[/bold red]")
        for error in errors:
            console.print(f"  [red]- {error}[/red]")
        console.print("\n[yellow]Please configure your .env file. See .env.example for template.[/yellow]")
        console.print("[yellow]1. Copy .env.example to .env[/yellow]")
        console.print("[yellow]2. Set your API key ID and private key path[/yellow]")
        sys.exit(1)

    console.clear()
    print_header()

    while True:
        print_menu()

        try:
            choice = Prompt.ask("Select option", default="0")

            if choice == "0":
                console.print("[blue]Goodbye![/blue]")
                break
            elif choice == "1":
                browse_markets()
            elif choice == "2":
                search_markets_menu()
            elif choice == "3":
                view_market_menu()
            elif choice == "4":
                place_trade_menu()
            elif choice == "5":
                simulator.display_portfolio()
                Prompt.ask("\nPress Enter to continue")
            elif choice == "6":
                simulator.display_trade_history()
                Prompt.ask("\nPress Enter to continue")
            elif choice == "7":
                # Settle positions and show history
                console.print("\n[bold]Checking for settled positions...[/bold]")
                settled = simulator.settle_positions()
                if settled == 0:
                    console.print("[dim]No positions were settled[/dim]")
                console.print()
                simulator.display_settlement_history()
                Prompt.ask("\nPress Enter to continue")
            elif choice == "8":
                check_connection()
                Prompt.ask("\nPress Enter to continue")
            elif choice == "9":
                reset_portfolio_menu()
            elif choice.lower() == "a":
                auto_trader_menu()
            elif choice.lower() == "c":
                crypto_auto_trader_menu()
            else:
                console.print("[yellow]Invalid option[/yellow]")

        except KeyboardInterrupt:
            console.print("\n[blue]Goodbye![/blue]")
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


if __name__ == "__main__":
    main()
