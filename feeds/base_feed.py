"""Abstract base class for all feed sources."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from feeds.models import FeedItem, FeedSource

logger = logging.getLogger(__name__)


class BaseFeed(ABC):
    """Base class all feed adapters must extend."""

    def __init__(self, source: FeedSource):
        self.source = source
        self._cache: list[FeedItem] = []
        self._last_fetch: float = 0.0

    @property
    def name(self) -> str:
        return self.source.name

    @property
    def is_stale(self) -> bool:
        return (time.time() - self._last_fetch) >= self.source.refresh_interval

    @abstractmethod
    async def fetch(self) -> list[FeedItem]:
        """Fetch new items from this source. Implementations must return a list of FeedItem."""
        ...

    async def get_items(self, force: bool = False) -> list[FeedItem]:
        """Return cached items or fetch if stale."""
        if force or self.is_stale:
            try:
                self._cache = await self.fetch()
                self._last_fetch = time.time()
            except Exception as e:
                logger.warning(f"Feed {self.name} fetch failed: {e}")
        return self._cache
