"""Registry of all built-in feed sources."""

from __future__ import annotations

from feeds.base_feed import BaseFeed
from feeds.cryptopanic_feed import CryptoPanicFeed
from feeds.coingecko_feed import CoinGeckoFeed
from feeds.binance_funding_feed import BinanceFundingFeed
from feeds.reddit_feed import RedditFeed
from feeds.google_trends_feed import GoogleTrendsFeed
from feeds.glassnode_feed import GlassnodeFeed
from feeds.rss_feed import RSSFeed, create_coindesk_feed, create_cointelegraph_feed


def get_builtin_feeds(config=None) -> dict[str, BaseFeed]:
    """Return all built-in feed instances, keyed by name."""
    feeds: dict[str, BaseFeed] = {}

    # API-key feeds — pass keys from config if available
    cryptopanic_key = getattr(config, "CRYPTOPANIC_API_KEY", "") if config else ""
    glassnode_key = getattr(config, "GLASSNODE_API_KEY", "") if config else ""

    feeds["cryptopanic"] = CryptoPanicFeed(api_key=cryptopanic_key)
    feeds["coingecko"] = CoinGeckoFeed()
    feeds["binance_funding"] = BinanceFundingFeed()
    feeds["reddit"] = RedditFeed()
    feeds["google_trends"] = GoogleTrendsFeed()
    feeds["glassnode"] = GlassnodeFeed(api_key=glassnode_key)
    feeds["coindesk_rss"] = create_coindesk_feed()
    feeds["cointelegraph_rss"] = create_cointelegraph_feed()

    return feeds
