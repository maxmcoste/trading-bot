"""
Configuration module using pydantic-settings.
Loads all settings from environment variables / .env file.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic_settings import BaseSettings
from pydantic import model_validator
from typing import List

ENV_PATH = Path(__file__).resolve().parent / ".env"


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # ── Broker Mode ──────────────────────────────────────────
    BROKER_MODE: str = "both"
    DEFAULT_CURRENCY: str = "EUR"

    # ── Watchlists ───────────────────────────────────────────
    WATCHLIST_STOCKS: List[str] = ["AAPL", "MSFT", "NVDA", "SPY", "QQQ"]
    # SOL-EUR excluded: with budget < 500 EUR slippage erodes profit.
    # Add SOL-EUR from the dashboard Settings when budget > 500 EUR.
    WATCHLIST_CRYPTO: List[str] = ["BTC-EUR", "ETH-EUR"]

    # ── Interactive Brokers ──────────────────────────────────
    IBKR_ENABLED: bool = True
    IBKR_HOST: str = "127.0.0.1"
    IBKR_PORT: int = 7497
    IBKR_CLIENT_ID: int = 1

    # ── Coinbase Advanced Trade ──────────────────────────────
    COINBASE_ENABLED: bool = True
    COINBASE_API_KEY: str = ""
    COINBASE_API_SECRET: str = ""

    # ── Paper Trading ────────────────────────────────────────
    PAPER_MODE: bool = True
    PAPER_INITIAL_CASH: float = 0.0  # 0 = auto-fetch from broker at startup

    # ── Analysis intervals (seconds) ────────────────────────
    ANALYSIS_INTERVAL_STOCKS: int = 300
    # 300s = 5 min — with 3 crypto symbols: ~36 Claude calls/hour ≈ $0.10/hour ≈ $75/month.
    # Lower to 120s only if you want more reactivity and accept higher API cost.
    ANALYSIS_INTERVAL_CRYPTO: int = 300

    # ── Trading Budget ──────────────────────────────────────
    TRADING_BUDGET: float = 100.0  # Max capital the bot can use for trading (EUR)

    # ── Risk Management — stocks ─────────────────────────────
    CONFIDENCE_THRESHOLD: float = 0.68
    MAX_POSITION_SIZE_PCT: float = 8.0
    MAX_DAILY_LOSS_PCT: float = 3.0
    MAX_OPEN_POSITIONS: int = 5
    STOP_LOSS_DEFAULT_PCT: float = 1.5
    TAKE_PROFIT_DEFAULT_PCT: float = 3.0

    # ── Risk Management — crypto ─────────────────────────────
    # Crypto: higher threshold than stocks (0.68) because the market is more volatile
    CRYPTO_CONFIDENCE_THRESHOLD: float = 0.72
    CRYPTO_MAX_POSITION_SIZE_PCT: float = 4.0
    CRYPTO_STOP_LOSS_DEFAULT_PCT: float = 3.0
    CRYPTO_TAKE_PROFIT_DEFAULT_PCT: float = 6.0
    CRYPTO_MAX_OPEN_POSITIONS: int = 3

    # ── Reserve Balances ──────────────────────────────────────
    # EUR amount of BTC the bot must NEVER spend (e.g. long-term BTC holdings).
    # 0 = no reserve, bot can use all available BTC.
    # Example: you hold 0.05 BTC (~€4200) and want to keep it → set BTC_RESERVE_EUR=4200.
    # The bot converts this to a BTC quantity at current price at each cycle.
    BTC_RESERVE_EUR: float = 0.0

    # ── Strategies ───────────────────────────────────────────
    STRATEGY_MEAN_REVERSION_ENABLED: bool = True
    STRATEGY_SENTIMENT_ENABLED: bool = True
    STRATEGY_MULTI_SIGNAL_ENABLED: bool = True
    STRATEGY_BTC_CORRELATION_ENABLED: bool = True
    STRATEGY_FEAR_GREED_ENABLED: bool = True
    STRATEGY_SESSION_MOMENTUM_ENABLED: bool = True

    STRATEGY_WEIGHT_TECHNICAL: float = 0.40
    STRATEGY_WEIGHT_SENTIMENT: float = 0.30
    STRATEGY_WEIGHT_MACRO: float = 0.30

    # ── External APIs ────────────────────────────────────────
    NEWSAPI_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    # ── Feeds ──────────────────────────────────────────────────
    FEEDS_ENABLED: bool = True
    FEEDS_REFRESH_INTERVAL: int = 120  # seconds between background feed refreshes
    CRYPTOPANIC_API_KEY: str = ""
    GLASSNODE_API_KEY: str = ""

    # ── Dashboard ────────────────────────────────────────────
    DASHBOARD_HOST: str = "127.0.0.1"
    DASHBOARD_PORT: int = 8080
    DASHBOARD_REFRESH_INTERVAL: int = 10

    # ── Notifications ────────────────────────────────────────
    NOTIFY_EMAIL: str = ""
    NOTIFY_ON_TRADE: bool = True
    NOTIFY_ON_ERROR: bool = True
    NOTIFY_ON_DAILY_SUMMARY: bool = True

    @model_validator(mode="after")
    def apply_broker_mode(self) -> "Settings":
        if self.BROKER_MODE == "ibkr":
            self.IBKR_ENABLED = True
            self.COINBASE_ENABLED = False
        elif self.BROKER_MODE == "coinbase":
            self.IBKR_ENABLED = False
            self.COINBASE_ENABLED = True
        elif self.BROKER_MODE == "both":
            self.IBKR_ENABLED = True
            self.COINBASE_ENABLED = True
        return self

    def save_to_env(self):
        """Persist current settings to .env file so they survive restarts."""
        # Read existing .env to preserve secrets and comments
        existing: dict[str, str] = {}
        if ENV_PATH.exists():
            for line in ENV_PATH.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    existing[key.strip()] = val.strip()

        # Keys that should be persisted (exclude secrets already in .env)
        PERSIST_KEYS = {
            "BROKER_MODE", "PAPER_MODE", "TRADING_BUDGET", "DEFAULT_CURRENCY",
            "ANALYSIS_INTERVAL_STOCKS", "ANALYSIS_INTERVAL_CRYPTO",
            "CONFIDENCE_THRESHOLD", "MAX_POSITION_SIZE_PCT", "MAX_DAILY_LOSS_PCT",
            "MAX_OPEN_POSITIONS", "STOP_LOSS_DEFAULT_PCT", "TAKE_PROFIT_DEFAULT_PCT",
            "CRYPTO_CONFIDENCE_THRESHOLD", "CRYPTO_MAX_POSITION_SIZE_PCT",
            "CRYPTO_STOP_LOSS_DEFAULT_PCT", "CRYPTO_TAKE_PROFIT_DEFAULT_PCT",
            "CRYPTO_MAX_OPEN_POSITIONS", "BTC_RESERVE_EUR",
            "STRATEGY_MEAN_REVERSION_ENABLED", "STRATEGY_SENTIMENT_ENABLED",
            "STRATEGY_MULTI_SIGNAL_ENABLED", "STRATEGY_BTC_CORRELATION_ENABLED",
            "STRATEGY_FEAR_GREED_ENABLED", "STRATEGY_SESSION_MOMENTUM_ENABLED",
            "STRATEGY_WEIGHT_TECHNICAL", "STRATEGY_WEIGHT_SENTIMENT",
            "STRATEGY_WEIGHT_MACRO",
            "FEEDS_ENABLED", "FEEDS_REFRESH_INTERVAL",
            "DASHBOARD_HOST", "DASHBOARD_PORT", "DASHBOARD_REFRESH_INTERVAL",
            "NOTIFY_EMAIL", "NOTIFY_ON_TRADE", "NOTIFY_ON_ERROR", "NOTIFY_ON_DAILY_SUMMARY",
            "WATCHLIST_STOCKS", "WATCHLIST_CRYPTO",
        }

        # Update existing dict with current values
        for key in PERSIST_KEYS:
            val = getattr(self, key, None)
            if val is None:
                continue
            if isinstance(val, bool):
                existing[key] = str(val).lower()
            elif isinstance(val, list):
                existing[key] = json.dumps(val)
            else:
                existing[key] = str(val)

        # Write back, preserving secret keys already present
        lines = []
        for key, val in existing.items():
            lines.append(f"{key}={val}")

        ENV_PATH.write_text("\n".join(lines) + "\n")


# Singleton
settings = Settings()


if __name__ == "__main__":
    s = Settings()
    print(f"Broker mode: {s.BROKER_MODE}")
    print(f"IBKR enabled: {s.IBKR_ENABLED}")
    print(f"Coinbase enabled: {s.COINBASE_ENABLED}")
    print(f"Paper mode: {s.PAPER_MODE}")
    print(f"Watchlist stocks: {s.WATCHLIST_STOCKS}")
    print(f"Watchlist crypto: {s.WATCHLIST_CRYPTO}")
    print(f"Strategy weights: tech={s.STRATEGY_WEIGHT_TECHNICAL}, "
          f"sent={s.STRATEGY_WEIGHT_SENTIMENT}, macro={s.STRATEGY_WEIGHT_MACRO}")
