"""Configuration management for Kalshi Trading Console."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Config:
    """Application configuration loaded from environment variables."""

    # API Endpoints
    DEMO_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"
    PROD_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

    def __init__(self):
        self.api_key_id = os.getenv("KALSHI_API_KEY_ID", "")
        self.private_key_raw = os.getenv("KALSHI_PRIVATE_KEY", "")
        self.environment = os.getenv("KALSHI_ENVIRONMENT", "demo").lower()
        self.starting_balance = float(os.getenv("STARTING_BALANCE", "100"))

        # Derived settings
        self.base_url = self.DEMO_BASE_URL if self.environment == "demo" else self.PROD_BASE_URL

        # Data directory
        self.data_dir = Path(__file__).parent / "data"
        self.portfolio_file = self.data_dir / "portfolio.json"

    def validate(self) -> tuple[bool, list[str]]:
        """Validate configuration. Returns (is_valid, list of errors)."""
        errors = []

        if not self.api_key_id:
            errors.append("KALSHI_API_KEY_ID not set in .env file")

        if not self.private_key_raw:
            errors.append("KALSHI_PRIVATE_KEY not set in .env file")
        elif "PRIVATE KEY" not in self.private_key_raw:
            errors.append("KALSHI_PRIVATE_KEY appears invalid (should contain '-----BEGIN ... PRIVATE KEY-----')")

        if self.environment not in ("demo", "production"):
            errors.append(f"Invalid environment: {self.environment} (must be 'demo' or 'production')")

        return len(errors) == 0, errors

    def get_private_key(self) -> bytes:
        """Return the private key as bytes."""
        # Handle escaped newlines from .env file
        key = self.private_key_raw.replace("\\n", "\n")
        return key.encode("utf-8")

    def ensure_data_dir(self):
        """Create data directory if it doesn't exist."""
        self.data_dir.mkdir(exist_ok=True)


# Global config instance
config = Config()
