#!/usr/bin/env python3
"""
SQLite Adapter for Priceline Negotiator

Provides a simple interface for the Python collectors to write data
to the shared SQLite database used by the Node.js dashboard.

Usage:
    from sqlite_adapter import PricelineDB

    db = PricelineDB()
    db.insert_market_observation({...})
    db.insert_price_observation({...})
    db.insert_market_result({...})
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager

# Database path - matches the Node.js configuration
DEFAULT_DB_PATH = Path("/home/maestro/priceline-negotiator/database/priceline.db")


class PricelineDB:
    """SQLite adapter for Priceline Negotiator database."""

    def __init__(self, db_path: Path = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._ensure_db_exists()

    def _ensure_db_exists(self):
        """Ensure the database directory exists."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def get_connection(self):
        """Get a database connection with WAL mode."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def insert_market_observation(self, obs: dict) -> bool:
        """
        Insert a market observation.

        Args:
            obs: dict with keys:
                - observation_id: unique ID
                - timestamp: ISO timestamp
                - series_ticker: e.g. 'KXBTC15M'
                - market_ticker: full market ticker
                - title: market title
                - close_time: ISO timestamp
                - seconds_to_close: float
                - minutes_to_close: float
                - yes_bid, yes_ask, no_bid, no_ask: int (0-100)
                - spread: int
                - volume: int
                - open_interest: int
                - last_price: int
                - result: 'yes', 'no', or None
                - result_updated_at: ISO timestamp or None

        Returns:
            True if inserted, False if already exists
        """
        sql = """
            INSERT OR IGNORE INTO market_observations
            (observation_id, timestamp, series_ticker, market_ticker, title, close_time,
             seconds_to_close, minutes_to_close, yes_bid, yes_ask, no_bid, no_ask,
             spread, volume, open_interest, last_price, result, result_updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        with self.get_connection() as conn:
            cursor = conn.execute(sql, (
                obs.get('observation_id'),
                obs.get('timestamp'),
                obs.get('series_ticker'),
                obs.get('market_ticker'),
                obs.get('title', '')[:200] if obs.get('title') else None,
                obs.get('close_time'),
                obs.get('seconds_to_close'),
                obs.get('minutes_to_close'),
                obs.get('yes_bid'),
                obs.get('yes_ask'),
                obs.get('no_bid'),
                obs.get('no_ask'),
                obs.get('spread'),
                obs.get('volume'),
                obs.get('open_interest'),
                obs.get('last_price'),
                obs.get('result'),
                obs.get('result_updated_at')
            ))
            return cursor.rowcount > 0

    def insert_market_observations_batch(self, observations: list) -> int:
        """Insert multiple market observations efficiently."""
        sql = """
            INSERT OR IGNORE INTO market_observations
            (observation_id, timestamp, series_ticker, market_ticker, title, close_time,
             seconds_to_close, minutes_to_close, yes_bid, yes_ask, no_bid, no_ask,
             spread, volume, open_interest, last_price, result, result_updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        inserted = 0
        with self.get_connection() as conn:
            for obs in observations:
                cursor = conn.execute(sql, (
                    obs.get('observation_id'),
                    obs.get('timestamp'),
                    obs.get('series_ticker'),
                    obs.get('market_ticker'),
                    obs.get('title', '')[:200] if obs.get('title') else None,
                    obs.get('close_time'),
                    obs.get('seconds_to_close'),
                    obs.get('minutes_to_close'),
                    obs.get('yes_bid'),
                    obs.get('yes_ask'),
                    obs.get('no_bid'),
                    obs.get('no_ask'),
                    obs.get('spread'),
                    obs.get('volume'),
                    obs.get('open_interest'),
                    obs.get('last_price'),
                    obs.get('result'),
                    obs.get('result_updated_at')
                ))
                inserted += cursor.rowcount
        return inserted

    def insert_price_observation(self, obs: dict) -> bool:
        """
        Insert a price observation.

        Args:
            obs: dict with keys:
                - timestamp: ISO timestamp
                - unix_timestamp: int
                - btc_usd: float
                - btc_24h_change: float
                - eth_usd: float
                - eth_24h_change: float
                - sol_usd: float
                - sol_24h_change: float

        Returns:
            True if inserted
        """
        sql = """
            INSERT INTO price_observations
            (timestamp, unix_timestamp, btc_usd, btc_24h_change, eth_usd, eth_24h_change, sol_usd, sol_24h_change)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """

        with self.get_connection() as conn:
            cursor = conn.execute(sql, (
                obs.get('timestamp'),
                obs.get('unix_timestamp'),
                obs.get('btc_usd'),
                obs.get('btc_24h_change'),
                obs.get('eth_usd'),
                obs.get('eth_24h_change'),
                obs.get('sol_usd'),
                obs.get('sol_24h_change')
            ))
            return cursor.rowcount > 0

    def insert_market_result(self, result: dict) -> bool:
        """
        Insert or update a market result.

        Args:
            result: dict with keys:
                - market_ticker: full market ticker
                - series_ticker: e.g. 'KXBTC15M'
                - result: 'yes' or 'no'
                - close_time: ISO timestamp
                - settled_at: ISO timestamp

        Returns:
            True if inserted/updated
        """
        sql = """
            INSERT OR REPLACE INTO market_results
            (market_ticker, series_ticker, result, close_time, settled_at)
            VALUES (?, ?, ?, ?, ?)
        """

        with self.get_connection() as conn:
            cursor = conn.execute(sql, (
                result.get('market_ticker'),
                result.get('series_ticker'),
                result.get('result'),
                result.get('close_time'),
                result.get('settled_at')
            ))
            return cursor.rowcount > 0

    def update_observation_result(self, market_ticker: str, result: str) -> int:
        """
        Update all observations for a market with its result.

        Args:
            market_ticker: the market ticker
            result: 'yes' or 'no'

        Returns:
            Number of rows updated
        """
        now = datetime.now(timezone.utc).isoformat()

        sql = """
            UPDATE market_observations
            SET result = ?, result_updated_at = ?
            WHERE market_ticker = ? AND result IS NULL
        """

        with self.get_connection() as conn:
            cursor = conn.execute(sql, (result, now, market_ticker))
            return cursor.rowcount

    def get_latest_observation_id(self) -> int:
        """Get the highest observation ID in the database."""
        sql = "SELECT MAX(id) as max_id FROM market_observations"

        with self.get_connection() as conn:
            row = conn.execute(sql).fetchone()
            return row['max_id'] if row and row['max_id'] else 0

    def get_pending_market_count(self) -> int:
        """Get count of markets without results."""
        sql = """
            SELECT COUNT(DISTINCT market_ticker) as count
            FROM market_observations
            WHERE result IS NULL
        """

        with self.get_connection() as conn:
            row = conn.execute(sql).fetchone()
            return row['count'] if row else 0

    def get_result_stats(self) -> dict:
        """Get statistics about market results."""
        sql = """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN result = 'yes' THEN 1 ELSE 0 END) as yes_wins,
                SUM(CASE WHEN result = 'no' THEN 1 ELSE 0 END) as no_wins
            FROM market_results
        """

        with self.get_connection() as conn:
            row = conn.execute(sql).fetchone()
            return {
                'total': row['total'] if row else 0,
                'yes_wins': row['yes_wins'] if row else 0,
                'no_wins': row['no_wins'] if row else 0
            }


# Convenience instance
_db = None

def get_db() -> PricelineDB:
    """Get or create the database instance."""
    global _db
    if _db is None:
        _db = PricelineDB()
    return _db


if __name__ == "__main__":
    # Test the adapter
    db = get_db()
    print(f"Database path: {db.db_path}")
    print(f"Latest observation ID: {db.get_latest_observation_id()}")
    print(f"Pending markets: {db.get_pending_market_count()}")
    print(f"Result stats: {db.get_result_stats()}")
