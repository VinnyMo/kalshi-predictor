"""Kalshi API client with RSA-PSS authentication."""

import time
from typing import Any, Optional
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
import base64

from config import config


class KalshiAPIError(Exception):
    """Custom exception for Kalshi API errors."""

    def __init__(self, status_code: int, message: str, response: dict = None):
        self.status_code = status_code
        self.message = message
        self.response = response or {}
        super().__init__(f"[{status_code}] {message}")


class KalshiClient:
    """HTTP client for Kalshi API with RSA-PSS request signing."""

    def __init__(self):
        self.base_url = config.base_url
        self.api_key_id = config.api_key_id
        self._private_key = None
        self.session = requests.Session()

    @property
    def private_key(self):
        """Lazy load private key."""
        if self._private_key is None:
            key_data = config.get_private_key()
            self._private_key = serialization.load_pem_private_key(
                key_data,
                password=None,
                backend=default_backend()
            )
        return self._private_key

    def _get_timestamp(self) -> str:
        """Get current timestamp in milliseconds."""
        return str(int(time.time() * 1000))

    def _sign_request(self, timestamp: str, method: str, path: str) -> str:
        """
        Sign a request using RSA-PSS with SHA256.

        The signature is computed over: timestamp + method + path
        """
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

    def _get_auth_headers(self, method: str, path: str) -> dict:
        """Generate authentication headers for a request."""
        timestamp = self._get_timestamp()
        signature = self._sign_request(timestamp, method, path)

        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: dict = None,
        json_data: dict = None
    ) -> dict:
        """Make an authenticated request to the Kalshi API."""
        # Build the path for signing (includes query params for GET)
        path = f"/trade-api/v2{endpoint}"

        headers = self._get_auth_headers(method.upper(), path)
        url = f"{self.base_url}{endpoint}"

        try:
            response = self.session.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=json_data,
                timeout=30
            )

            # Handle rate limiting
            if response.status_code == 429:
                raise KalshiAPIError(
                    429,
                    "Rate limit exceeded. Please wait before making more requests.",
                    response.json() if response.text else {}
                )

            # Handle other errors
            if response.status_code >= 400:
                error_data = response.json() if response.text else {}
                message = error_data.get("message", error_data.get("error", "Unknown error"))
                raise KalshiAPIError(response.status_code, message, error_data)

            return response.json() if response.text else {}

        except requests.RequestException as e:
            raise KalshiAPIError(0, f"Request failed: {str(e)}")

    def get(self, endpoint: str, params: dict = None) -> dict:
        """Make a GET request."""
        return self._make_request("GET", endpoint, params=params)

    def post(self, endpoint: str, json_data: dict = None) -> dict:
        """Make a POST request."""
        return self._make_request("POST", endpoint, json_data=json_data)

    def delete(self, endpoint: str) -> dict:
        """Make a DELETE request."""
        return self._make_request("DELETE", endpoint)

    # ==================== Account Endpoints ====================

    def get_balance(self) -> dict:
        """Get account balance."""
        return self.get("/portfolio/balance")

    def get_positions(self) -> dict:
        """Get current positions."""
        return self.get("/portfolio/positions")

    # ==================== Market Endpoints ====================

    def get_markets(
        self,
        status: str = None,
        series_ticker: str = None,
        event_ticker: str = None,
        limit: int = 100,
        cursor: str = None
    ) -> dict:
        """
        Get list of markets with optional filtering.

        Args:
            status: Filter by status ('open', 'closed', 'settled')
            series_ticker: Filter by series
            event_ticker: Filter by event
            limit: Number of results per page (max 200)
            cursor: Pagination cursor
        """
        params = {"limit": limit}
        if status:
            params["status"] = status
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if cursor:
            params["cursor"] = cursor

        return self.get("/markets", params=params)

    def get_market(self, ticker: str) -> dict:
        """Get details for a specific market."""
        return self.get(f"/markets/{ticker}")

    def get_orderbook(self, ticker: str) -> dict:
        """Get order book for a market."""
        return self.get(f"/markets/{ticker}/orderbook")

    def get_events(self, status: str = None, limit: int = 100, cursor: str = None) -> dict:
        """Get list of events."""
        params = {"limit": limit}
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        return self.get("/events", params=params)

    def get_event(self, event_ticker: str) -> dict:
        """Get details for a specific event."""
        return self.get(f"/events/{event_ticker}")

    # ==================== Trading Endpoints ====================

    def place_order(
        self,
        ticker: str,
        side: str,
        action: str,
        count: int,
        type: str = "limit",
        yes_price: int = None,
        no_price: int = None,
        expiration_ts: int = None
    ) -> dict:
        """
        Place an order.

        Args:
            ticker: Market ticker
            side: 'yes' or 'no'
            action: 'buy' or 'sell'
            count: Number of contracts
            type: Order type ('limit' or 'market')
            yes_price: Price in cents for yes contracts (1-99)
            no_price: Price in cents for no contracts (1-99)
            expiration_ts: Order expiration timestamp (optional)
        """
        order_data = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "type": type
        }

        if yes_price is not None:
            order_data["yes_price"] = yes_price
        if no_price is not None:
            order_data["no_price"] = no_price
        if expiration_ts is not None:
            order_data["expiration_ts"] = expiration_ts

        return self.post("/portfolio/orders", json_data=order_data)

    def get_orders(self, ticker: str = None, status: str = None) -> dict:
        """Get orders, optionally filtered."""
        params = {}
        if ticker:
            params["ticker"] = ticker
        if status:
            params["status"] = status
        return self.get("/portfolio/orders", params=params)

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an order."""
        return self.delete(f"/portfolio/orders/{order_id}")


# Global client instance
client = KalshiClient()
