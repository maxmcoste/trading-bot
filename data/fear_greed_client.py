"""Fear & Greed Index client with in-memory cache (1h TTL)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

FNG_URL = "https://api.alternative.me/fng/?limit=7"
CACHE_TTL = 3600  # 1 hour


def _classify(value: int) -> str:
    if value <= 24:
        return "Extreme Fear"
    elif value <= 44:
        return "Fear"
    elif value <= 55:
        return "Neutral"
    elif value <= 74:
        return "Greed"
    return "Extreme Greed"


@dataclass
class FearGreedData:
    value: int
    classification: str
    timestamp: str
    history: list[dict] = field(default_factory=list)


class FearGreedClient:
    def __init__(self):
        self._cache: FearGreedData | None = None
        self._cache_time: float = 0.0

    async def fetch(self) -> FearGreedData | None:
        if self._cache and (time.time() - self._cache_time) < CACHE_TTL:
            return self._cache

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(FNG_URL)
                resp.raise_for_status()
                data = resp.json()

            entries = data.get("data", [])
            if not entries:
                logger.warning("Fear & Greed API returned no data")
                return self._cache

            latest = entries[0]
            value = int(latest["value"])

            history = []
            for e in entries:
                v = int(e["value"])
                history.append({
                    "value": v,
                    "classification": _classify(v),
                    "timestamp": e.get("timestamp", ""),
                })

            result = FearGreedData(
                value=value,
                classification=_classify(value),
                timestamp=latest.get("timestamp", ""),
                history=history,
            )
            self._cache = result
            self._cache_time = time.time()
            return result

        except Exception as e:
            logger.warning(f"Fear & Greed fetch failed: {e}")
            return self._cache


if __name__ == "__main__":
    async def main():
        c = FearGreedClient()
        data = await c.fetch()
        if data:
            print(f"Fear & Greed Index: {data.value} ({data.classification})")
            print(f"History (7 days):")
            for h in data.history:
                print(f"  {h['value']} - {h['classification']}")
        else:
            print("Failed to fetch data")

    asyncio.run(main())
