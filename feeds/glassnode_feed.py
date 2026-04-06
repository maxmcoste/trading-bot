"""Glassnode feed — on-chain metrics for Bitcoin (free tier)."""

from __future__ import annotations

import logging
import os

import httpx

from feeds.base_feed import BaseFeed
from feeds.models import FeedItem, FeedSource

logger = logging.getLogger(__name__)

GLASSNODE_BASE = "https://api.glassnode.com/v1/metrics"


class GlassnodeFeed(BaseFeed):
    def __init__(self, api_key: str = ""):
        source = FeedSource(
            name="glassnode",
            display_name="Glassnode",
            description="On-chain Bitcoin metrics: active addresses, exchange flows, NUPL.",
            tags=["on-chain", "sentiment"],
            refresh_interval=3600,  # hourly — free tier limits
            requires_api_key=True,
            config_key="GLASSNODE_API_KEY",
        )
        super().__init__(source)
        self.api_key = api_key or os.getenv("GLASSNODE_API_KEY", "")

    async def fetch(self) -> list[FeedItem]:
        if not self.api_key:
            logger.debug("Glassnode: no API key configured")
            return []

        items = []
        metrics = [
            ("addresses/active_count", "Active Addresses", "addresses"),
            ("transactions/count", "Transaction Count", "transactions"),
            ("market/mvrv", "MVRV Ratio", "valuation"),
        ]

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                for endpoint, label, category in metrics:
                    try:
                        resp = await client.get(
                            f"{GLASSNODE_BASE}/{endpoint}",
                            params={
                                "a": "BTC",
                                "api_key": self.api_key,
                                "i": "24h",
                            },
                        )
                        resp.raise_for_status()
                        data = resp.json()

                        if not data:
                            continue

                        latest = data[-1]
                        value = latest.get("v", 0)
                        prev = data[-2].get("v", 0) if len(data) > 1 else value
                        change = ((value - prev) / max(abs(prev), 1)) * 100 if prev else 0

                        # MVRV interpretation
                        sentiment = None
                        priority = "medium"
                        if "mvrv" in endpoint:
                            if value > 3.5:
                                sentiment = -0.5
                                priority = "high"
                            elif value < 1.0:
                                sentiment = 0.5
                                priority = "high"
                            else:
                                sentiment = 0.0

                        if abs(change) > 10:
                            priority = "high"

                        items.append(FeedItem(
                            source="glassnode",
                            title=f"BTC {label}: {value:,.0f} ({change:+.1f}%)",
                            content=f"Bitcoin {label}: {value:,.0f}. 24h change: {change:+.1f}%",
                            tags=["on-chain", "sentiment"],
                            priority=priority,
                            sentiment_score=sentiment,
                            symbol="BTC",
                            metadata={
                                "metric": endpoint,
                                "value": value,
                                "previous": prev,
                                "change_pct": change,
                            },
                        ))
                    except Exception as e:
                        logger.debug(f"Glassnode {endpoint} failed: {e}")

        except Exception as e:
            logger.warning(f"Glassnode fetch failed: {e}")

        return items
