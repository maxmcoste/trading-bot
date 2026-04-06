"""Feed manager — orchestrates all feed sources, caching, and DB persistence."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from feeds.base_feed import BaseFeed
from feeds.feed_registry import get_builtin_feeds
from feeds.models import FeedItem, FeedSource
from feeds.rss_feed import RSSFeed

logger = logging.getLogger(__name__)

CET = ZoneInfo("Europe/Berlin")


class FeedManager:
    """Central orchestrator for all feed sources."""

    def __init__(self, config=None, db=None):
        self.config = config
        self.db = db
        self._feeds: dict[str, BaseFeed] = {}
        self._all_items: list[FeedItem] = []
        self._running = False
        self._last_full_refresh: float = 0.0

    def init(self):
        """Initialize all built-in feeds."""
        self._feeds = get_builtin_feeds(self.config)

        # Check enabled state from config
        feeds_enabled = getattr(self.config, "FEEDS_ENABLED", True) if self.config else True
        if not feeds_enabled:
            logger.info("Feed system disabled via config")
            return

        # Load custom RSS feeds from DB
        if self.db:
            self._load_custom_feeds()

        logger.info(f"Feed manager initialized with {len(self._feeds)} sources")

    def _load_custom_feeds(self):
        """Load user-added custom RSS feeds from DB."""
        try:
            configs = self.db.get_feed_configs()
            for cfg in configs:
                if cfg.get("source_name", "").startswith("custom_rss_") and cfg.get("custom_url"):
                    name = cfg["source_name"]
                    if name not in self._feeds:
                        self._feeds[name] = RSSFeed(
                            name=name,
                            display_name=cfg.get("display_name", name),
                            description=f"Custom RSS: {cfg['custom_url']}",
                            url=cfg["custom_url"],
                            tags=["rss", "custom"],
                            refresh_interval=cfg.get("refresh_interval", 300),
                        )
        except Exception as e:
            logger.debug(f"Could not load custom feeds from DB: {e}")

    def add_custom_rss(self, name: str, display_name: str, url: str,
                       refresh_interval: int = 300) -> str:
        """Add a custom RSS feed at runtime."""
        feed_name = f"custom_rss_{name}"
        self._feeds[feed_name] = RSSFeed(
            name=feed_name,
            display_name=display_name,
            description=f"Custom RSS: {url}",
            url=url,
            tags=["rss", "custom"],
            refresh_interval=refresh_interval,
        )
        # Persist to DB
        if self.db:
            self.db.save_feed_config(
                source_name=feed_name,
                enabled=True,
                refresh_interval=refresh_interval,
                custom_url=url,
                display_name=display_name,
            )
        return feed_name

    def remove_custom_rss(self, feed_name: str) -> bool:
        """Remove a custom RSS feed."""
        if feed_name in self._feeds and feed_name.startswith("custom_rss_"):
            del self._feeds[feed_name]
            if self.db:
                self.db.delete_feed_config(feed_name)
            return True
        return False

    def get_sources(self) -> list[dict]:
        """Return metadata for all registered sources."""
        return [f.source.to_dict() for f in self._feeds.values()]

    def get_source(self, name: str) -> BaseFeed | None:
        return self._feeds.get(name)

    async def fetch_source(self, name: str, force: bool = False) -> list[FeedItem]:
        """Fetch items from a single source."""
        feed = self._feeds.get(name)
        if not feed:
            return []
        return await feed.get_items(force=force)

    async def fetch_all(self, force: bool = False) -> list[FeedItem]:
        """Fetch from all enabled sources concurrently."""
        tasks = []
        for feed in self._feeds.values():
            if feed.source.enabled:
                tasks.append(feed.get_items(force=force))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_items = []
        for result in results:
            if isinstance(result, list):
                all_items.extend(result)
            elif isinstance(result, Exception):
                logger.debug(f"Feed fetch error: {result}")

        # Sort by priority then timestamp (newest first)
        priority_order = {"high": 0, "medium": 1, "low": 2}
        all_items.sort(key=lambda x: (priority_order.get(x.priority, 1), ""), reverse=False)

        self._all_items = all_items
        self._last_full_refresh = time.time()

        # Persist to DB
        if self.db:
            for item in all_items:
                try:
                    self.db.log_feed_item(item)
                except Exception:
                    pass

        return all_items

    def get_cached_items(self, tag: str | None = None, source: str | None = None,
                         limit: int = 50) -> list[FeedItem]:
        """Return cached items, optionally filtered by tag or source."""
        items = self._all_items
        if tag:
            items = [i for i in items if tag in i.tags]
        if source:
            items = [i for i in items if i.source == source]
        return items[:limit]

    def get_feed_summary_for_ai(self, symbol: str | None = None, limit: int = 15) -> list[dict]:
        """Return a condensed feed summary suitable for the Claude prompt."""
        items = self._all_items

        # Filter high-priority or symbol-relevant items
        relevant = []
        for item in items:
            if item.priority == "high":
                relevant.append(item)
            elif symbol and item.symbol and symbol.upper().startswith(item.symbol.upper()):
                relevant.append(item)
            elif item.sentiment_score is not None and abs(item.sentiment_score) > 0.2:
                relevant.append(item)

        # Deduplicate by title
        seen = set()
        unique = []
        for item in relevant:
            key = item.title[:60]
            if key not in seen:
                seen.add(key)
                unique.append(item)

        return [
            {
                "source": i.source,
                "title": i.title[:120],
                "sentiment": i.sentiment_score,
                "priority": i.priority,
                "tags": i.tags,
                "symbol": i.symbol,
            }
            for i in unique[:limit]
        ]

    async def run_background_loop(self, interval: int = 120):
        """Background loop that refreshes all feeds periodically."""
        self._running = True
        logger.info(f"Feed background loop started (interval={interval}s)")
        while self._running:
            try:
                await self.fetch_all()
                logger.debug(f"Feeds refreshed: {len(self._all_items)} total items")
            except Exception as e:
                logger.warning(f"Feed refresh cycle failed: {e}")
            await asyncio.sleep(interval)

    def stop(self):
        self._running = False
