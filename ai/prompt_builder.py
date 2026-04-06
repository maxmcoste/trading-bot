"""Build the user prompt for Claude from market data and strategy signals."""

from __future__ import annotations

import json
from dataclasses import asdict

from data.indicators import TechnicalSignals
from strategies.base_strategy import StrategySignal


def build_prompt(
    symbol: str,
    asset_type: str,
    price: float,
    technical: TechnicalSignals,
    signals: list[StrategySignal],
    sentiment_score: float | None = None,
    news_count: int = 0,
    fear_greed_value: int | None = None,
    session: str | None = None,
    btc_change_1h: float | None = None,
    btc_change_4h: float | None = None,
    feed_items: list[dict] | None = None,
) -> str:
    """Build the user message sent to Claude for analysis."""

    data = {
        "symbol": symbol,
        "asset_type": asset_type,
        "current_price": price,
        "technical_indicators": {
            k: v for k, v in asdict(technical).items() if v is not None
        },
        "strategy_signals": {},
        "metadata": {},
    }

    for sig in signals:
        data["strategy_signals"][sig.name] = {
            "signal": sig.signal,
            "strength": sig.strength,
            "reason": sig.reason,
            "enabled": sig.enabled,
        }

    if sentiment_score is not None:
        data["metadata"]["sentiment_score"] = sentiment_score
        data["metadata"]["news_count"] = news_count

    if fear_greed_value is not None:
        data["metadata"]["fear_greed_index"] = fear_greed_value

    if session:
        data["metadata"]["session"] = session

    if btc_change_1h is not None:
        data["metadata"]["btc_change_1h"] = btc_change_1h
    if btc_change_4h is not None:
        data["metadata"]["btc_change_4h"] = btc_change_4h

    if feed_items:
        data["feed_intelligence"] = feed_items

    return (
        f"Analyze {symbol} ({asset_type}) and return your trading decision as JSON.\n\n"
        f"{json.dumps(data, indent=2)}"
    )


if __name__ == "__main__":
    from data.indicators import TechnicalSignals
    from strategies.base_strategy import StrategySignal

    tech = TechnicalSignals(
        rsi=35.0, z_score=-1.8, bb_position=0.12,
        ema_20=150.0, ema_50=148.0, volume_ratio=1.5,
    )
    sigs = [
        StrategySignal("mean_reversion", "bullish", 0.7, "Z-score -1.8", True),
        StrategySignal("sentiment", "bullish", 0.5, "Positive news", True),
    ]
    prompt = build_prompt(
        symbol="AAPL", asset_type="stock", price=150.25,
        technical=tech, signals=sigs,
        sentiment_score=0.4, news_count=5,
    )
    print(prompt)
