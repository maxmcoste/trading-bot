"""Data models for the feed system."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class FeedTag(str, Enum):
    NEWS = "news"
    SENTIMENT = "sentiment"
    ON_CHAIN = "on-chain"
    MACRO = "macro"
    SOCIAL = "social"
    TECHNICAL = "technical"
    FUNDING = "funding"
    TRENDS = "trends"
    RSS = "rss"
    CUSTOM = "custom"


class FeedPriority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class FeedItem:
    source: str
    title: str
    content: str = ""
    url: str = ""
    timestamp: str = ""
    tags: list[str] = field(default_factory=list)
    priority: str = "medium"
    sentiment_score: float | None = None
    symbol: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "title": self.title,
            "content": self.content,
            "url": self.url,
            "timestamp": self.timestamp,
            "tags": self.tags,
            "priority": self.priority,
            "sentiment_score": self.sentiment_score,
            "symbol": self.symbol,
            "metadata": self.metadata,
        }


@dataclass
class FeedSource:
    name: str
    display_name: str
    description: str
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    refresh_interval: int = 300  # seconds
    requires_api_key: bool = False
    config_key: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "tags": self.tags,
            "enabled": self.enabled,
            "refresh_interval": self.refresh_interval,
            "requires_api_key": self.requires_api_key,
            "config_key": self.config_key,
        }


@dataclass
class FeedConfig:
    source_name: str
    enabled: bool = True
    refresh_interval: int = 300
    custom_url: str = ""
    api_key: str = ""
