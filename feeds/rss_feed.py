"""RSS feed adapter — supports CoinDesk, Cointelegraph, and custom RSS URLs."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from feeds.base_feed import BaseFeed
from feeds.models import FeedItem, FeedSource

logger = logging.getLogger(__name__)

# Reuse sentiment scoring from news_client
POSITIVE_WORDS = {
    "surge", "rally", "bullish", "growth", "beat", "profit", "upgrade",
    "partnership", "launch", "record", "breakthrough", "strong", "buy",
    "soar", "gain", "outperform", "boom", "recovery", "optimistic",
    "milestone", "innovation", "revenue", "dividend", "approval",
    "expansion", "momentum", "positive", "exceed", "success", "upbeat",
}

NEGATIVE_WORDS = {
    "crash", "bearish", "loss", "decline", "drop", "downgrade", "risk",
    "investigation", "lawsuit", "miss", "weak", "sell", "concern",
    "plunge", "fall", "warning", "bankruptcy", "fraud", "recession",
    "volatile", "debt", "layoff", "penalty", "scandal", "default",
    "inflation", "crisis", "delay", "failure", "negative",
}


def _score_text(text: str) -> float:
    words = text.lower().split()
    if not words:
        return 0.0
    pos = sum(1 for w in words if w.strip(".,!?;:'\"") in POSITIVE_WORDS)
    neg = sum(1 for w in words if w.strip(".,!?;:'\"") in NEGATIVE_WORDS)
    raw = (pos - neg) / max(len(words), 1)
    return max(-1.0, min(1.0, math.tanh(raw * 20)))


# ── Built-in RSS Sources ─────────────────────────────────────────

BUILTIN_RSS = {
    "coindesk_rss": {
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "display_name": "CoinDesk RSS",
        "description": "Latest news from CoinDesk.",
        "tags": ["news", "rss"],
    },
    "cointelegraph_rss": {
        "url": "https://cointelegraph.com/rss",
        "display_name": "Cointelegraph RSS",
        "description": "Latest news from Cointelegraph.",
        "tags": ["news", "rss"],
    },
}


class RSSFeed(BaseFeed):
    """Generic RSS feed adapter using feedparser."""

    def __init__(self, name: str, display_name: str, description: str,
                 url: str, tags: list[str] | None = None, refresh_interval: int = 300):
        source = FeedSource(
            name=name,
            display_name=display_name,
            description=description,
            tags=tags or ["news", "rss"],
            refresh_interval=refresh_interval,
            requires_api_key=False,
        )
        super().__init__(source)
        self.url = url

    async def fetch(self) -> list[FeedItem]:
        items = []
        try:
            import feedparser
        except ImportError:
            logger.debug("feedparser not installed — RSS feeds disabled")
            return []

        try:
            feed = feedparser.parse(self.url)

            for entry in feed.entries[:15]:
                title = entry.get("title", "")
                summary = entry.get("summary", "") or entry.get("description", "")
                link = entry.get("link", "")
                published = entry.get("published", "")

                # Score headline + summary
                text = f"{title} {summary}"
                sentiment = _score_text(text)

                items.append(FeedItem(
                    source=self.name,
                    title=title,
                    content=summary[:500],
                    url=link,
                    timestamp=published,
                    tags=self.source.tags,
                    priority="medium",
                    sentiment_score=sentiment,
                    metadata={"feed_url": self.url},
                ))

        except Exception as e:
            logger.warning(f"RSS feed {self.name} fetch failed: {e}")

        return items


def create_coindesk_feed() -> RSSFeed:
    info = BUILTIN_RSS["coindesk_rss"]
    return RSSFeed("coindesk_rss", info["display_name"], info["description"],
                   info["url"], info["tags"])


def create_cointelegraph_feed() -> RSSFeed:
    info = BUILTIN_RSS["cointelegraph_rss"]
    return RSSFeed("cointelegraph_rss", info["display_name"], info["description"],
                   info["url"], info["tags"])
