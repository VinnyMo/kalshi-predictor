"""Market data fetching and display utilities."""

from dataclasses import dataclass
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from kalshi_client import client, KalshiAPIError

console = Console()


@dataclass
class MarketSummary:
    """Summary of a market's key information."""
    ticker: str
    title: str
    subtitle: str
    status: str
    yes_bid: Optional[int]  # cents
    yes_ask: Optional[int]  # cents
    no_bid: Optional[int]   # cents
    no_ask: Optional[int]   # cents
    volume: int
    open_interest: int
    result: Optional[str]

    @property
    def yes_bid_pct(self) -> str:
        """Yes bid as percentage string."""
        return f"{self.yes_bid}%" if self.yes_bid else "-"

    @property
    def yes_ask_pct(self) -> str:
        """Yes ask as percentage string."""
        return f"{self.yes_ask}%" if self.yes_ask else "-"

    @property
    def spread(self) -> Optional[int]:
        """Bid-ask spread in cents."""
        if self.yes_bid and self.yes_ask:
            return self.yes_ask - self.yes_bid
        return None

    @property
    def spread_str(self) -> str:
        """Spread as string."""
        return f"{self.spread}c" if self.spread else "-"

    @classmethod
    def from_api(cls, data: dict) -> "MarketSummary":
        """Create MarketSummary from API response."""
        return cls(
            ticker=data.get("ticker", ""),
            title=data.get("title", ""),
            subtitle=data.get("subtitle", ""),
            status=data.get("status", "unknown"),
            yes_bid=data.get("yes_bid"),
            yes_ask=data.get("yes_ask"),
            no_bid=data.get("no_bid"),
            no_ask=data.get("no_ask"),
            volume=data.get("volume", 0),
            open_interest=data.get("open_interest", 0),
            result=data.get("result")
        )


def fetch_markets(
    status: str = "open",
    limit: int = 20,
    cursor: str = None
) -> tuple[list[MarketSummary], Optional[str]]:
    """
    Fetch markets from the API.

    Returns:
        Tuple of (list of MarketSummary, next cursor or None)
    """
    response = client.get_markets(status=status, limit=limit, cursor=cursor)
    markets = [MarketSummary.from_api(m) for m in response.get("markets", [])]
    next_cursor = response.get("cursor")
    return markets, next_cursor


def fetch_market_detail(ticker: str) -> dict:
    """Fetch detailed market information."""
    return client.get_market(ticker)


def fetch_orderbook(ticker: str) -> dict:
    """Fetch orderbook for a market."""
    return client.get_orderbook(ticker)


def display_markets_table(markets: list[MarketSummary], page: int = 1):
    """Display markets in a formatted table."""
    table = Table(title=f"Available Markets (Page {page})", show_header=True, header_style="bold cyan")

    table.add_column("#", style="dim", width=4)
    table.add_column("Ticker", style="green", width=20)
    table.add_column("Title", width=40)
    table.add_column("Yes Bid", justify="right", width=8)
    table.add_column("Yes Ask", justify="right", width=8)
    table.add_column("Spread", justify="right", width=8)
    table.add_column("Volume", justify="right", width=10)
    table.add_column("Status", width=8)

    for i, market in enumerate(markets, 1):
        # Color code based on spread
        spread_style = "green" if market.spread and market.spread <= 5 else "yellow" if market.spread else "dim"

        table.add_row(
            str(i),
            market.ticker,
            market.title[:38] + ".." if len(market.title) > 40 else market.title,
            market.yes_bid_pct,
            market.yes_ask_pct,
            Text(market.spread_str, style=spread_style),
            f"{market.volume:,}",
            market.status
        )

    console.print(table)


def display_market_detail(ticker: str):
    """Display detailed information for a single market."""
    try:
        market_data = fetch_market_detail(ticker)
        market = market_data.get("market", {})

        # Basic info panel
        info_text = Text()
        info_text.append("Ticker: ", style="bold")
        info_text.append(f"{market.get('ticker', 'N/A')}\n")
        info_text.append("Title: ", style="bold")
        info_text.append(f"{market.get('title', 'N/A')}\n")
        info_text.append("Subtitle: ", style="bold")
        info_text.append(f"{market.get('subtitle', 'N/A')}\n")
        info_text.append("Status: ", style="bold")
        info_text.append(f"{market.get('status', 'N/A')}\n")
        info_text.append("Category: ", style="bold")
        info_text.append(f"{market.get('category', 'N/A')}\n\n")

        info_text.append("Yes Bid: ", style="bold green")
        yes_bid = market.get('yes_bid')
        info_text.append(f"{yes_bid}c ({yes_bid}%)\n" if yes_bid else "N/A\n")
        info_text.append("Yes Ask: ", style="bold green")
        yes_ask = market.get('yes_ask')
        info_text.append(f"{yes_ask}c ({yes_ask}%)\n" if yes_ask else "N/A\n")
        info_text.append("No Bid: ", style="bold red")
        no_bid = market.get('no_bid')
        info_text.append(f"{no_bid}c ({no_bid}%)\n" if no_bid else "N/A\n")
        info_text.append("No Ask: ", style="bold red")
        no_ask = market.get('no_ask')
        info_text.append(f"{no_ask}c ({no_ask}%)\n\n" if no_ask else "N/A\n\n")

        info_text.append("Volume: ", style="bold")
        info_text.append(f"{market.get('volume', 0):,}\n")
        info_text.append("Open Interest: ", style="bold")
        info_text.append(f"{market.get('open_interest', 0):,}\n")

        console.print(Panel(info_text, title=f"Market Details: {ticker}", border_style="blue"))

        # Try to get orderbook
        try:
            orderbook = fetch_orderbook(ticker)
            display_orderbook(ticker, orderbook)
        except KalshiAPIError as e:
            console.print(f"[yellow]Could not fetch orderbook: {e.message}[/yellow]")

    except KalshiAPIError as e:
        console.print(f"[red]Error fetching market: {e.message}[/red]")


def display_orderbook(ticker: str, orderbook: dict):
    """Display the orderbook for a market."""
    ob = orderbook.get("orderbook", {})

    yes_bids = ob.get("yes", [])  # List of [price, quantity]
    no_bids = ob.get("no", [])

    table = Table(title=f"Order Book: {ticker}", show_header=True, header_style="bold cyan")
    table.add_column("Yes Bids", justify="right", style="green", width=20)
    table.add_column("Price", justify="center", width=10)
    table.add_column("No Bids", justify="left", style="red", width=20)

    # Combine and sort prices
    all_prices = set()
    yes_dict = {}
    no_dict = {}

    for price, qty in yes_bids:
        all_prices.add(price)
        yes_dict[price] = qty

    for price, qty in no_bids:
        # No price at X is equivalent to yes at (100-X)
        equivalent_yes = 100 - price
        all_prices.add(equivalent_yes)
        no_dict[equivalent_yes] = qty

    for price in sorted(all_prices, reverse=True)[:10]:  # Top 10 price levels
        yes_qty = yes_dict.get(price, "")
        no_qty = no_dict.get(price, "")
        table.add_row(
            str(yes_qty) if yes_qty else "",
            f"{price}c",
            str(no_qty) if no_qty else ""
        )

    console.print(table)


def search_markets(query: str, status: str = "open") -> list[MarketSummary]:
    """Search markets by title (client-side filtering)."""
    markets, _ = fetch_markets(status=status, limit=200)
    query_lower = query.lower()
    return [m for m in markets if query_lower in m.title.lower() or query_lower in m.ticker.lower()]
