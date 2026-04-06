"""CryptoPanic news feed — crypto-specific news aggregator."""

from __future__ import annotations

import logging
import os

import httpx

from feeds.base_feed import BaseFeed
from feeds.models import FeedItem, FeedSource

logger = logging.getLogger(__name__)

CRYPTOPANIC_API = "https://cryptopanic.com/api/free/v1/posts/"


class CryptoPanicFeed(BaseFeed):
    def __init__(self, api_key: str = ""):
        source = FeedSource(
            name="cryptopanic",
            display_name="CryptoPanic",
            description="Crypto news aggregator with community sentiment voting.",
            tags=["news", "sentiment"],
            refresh_interval=120,
            requires_api_key=True,
            config_key="CRYPTOPANIC_API_KEY",
        )
        super().__init__(source)
        self.api_key = api_key or os.getenv("CRYPTOPANIC_API_KEY", "")

    async def fetch(self) -> list[FeedItem]:
        if not self.api_key:
            logger.debug("CryptoPanic: no API key configured")
            return []

        items = []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(CRYPTOPANIC_API, params={
                    "auth_token": self.api_key,
                    "filter": "important",
                    "public": "true",
                })
                resp.raise_for_status()
                data = resp.json()

            for post in data.get("results", [])[:20]:
                votes = post.get("votes", {})
                positive = votes.get("positive", 0)
                negative = votes.get("negative", 0)
                total_votes = positive + negative
                sentiment = ((positive - negative) / max(total_votes, 1)) if total_votes > 0 else None

                # Map CryptoPanic kind to priority
                kind = post.get("kind", "news")
                priority = "high" if kind == "news" else "medium"

                currencies = [c.get("code", "") for c in post.get("currencies", [])]
                symbol = currencies[0] if currencies else None

                items.append(FeedItem(
                    source="cryptopanic",
                    title=post.get("title", ""),
                    content=post.get("title", ""),
                    url=post.get("url", ""),
                    timestamp=post.get("published_at", ""),
                    tags=["news", "sentiment"],
                    priority=priority,
                    sentiment_score=sentiment,
                    symbol=symbol,
                    metadata={
                        "votes_positive": positive,
                        "votes_negative": negative,
                        "kind": kind,
                        "currencies": currencies,
                    },
                ))
        except Exception as e:
            logger.warning(f"CryptoPanic fetch failed: {e}")

        return items
