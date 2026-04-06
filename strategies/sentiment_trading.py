"""Sentiment Trading strategy based on news sentiment and Fear & Greed Index."""

from strategies.base_strategy import BaseStrategy, StrategySignal


class SentimentStrategy(BaseStrategy):
    docs_url = "https://www.investopedia.com/terms/m/marketsentiment.asp"
    config_key = "STRATEGY_SENTIMENT_ENABLED"

    def analyze(self, data: dict) -> StrategySignal:
        sentiment_score = data.get("sentiment_score", 0.0)
        headlines = data.get("headlines", [])
        asset_type = data.get("asset_type", "stock")
        fear_greed_value = data.get("fear_greed_value")

        if sentiment_score is None:
            sentiment_score = 0.0

        effective_score = sentiment_score
        reasons = [f"News sentiment {sentiment_score:+.2f}"]

        # Consistency bonus: if last 3 headlines all same sign
        if len(headlines) >= 3:
            recent_sentiments = []
            for h in headlines[:3]:
                hs = h.get("sentiment", 0.0) if isinstance(h, dict) else 0.0
                recent_sentiments.append(hs)
            if all(s > 0 for s in recent_sentiments):
                effective_score = min(effective_score + 0.15, 1.0)
                reasons.append("3 consecutive positive headlines")
            elif all(s < 0 for s in recent_sentiments):
                effective_score = max(effective_score - 0.15, -1.0)
                reasons.append("3 consecutive negative headlines")

        # For crypto: blend in Fear & Greed
        if asset_type == "crypto" and fear_greed_value is not None:
            fg_normalized = (fear_greed_value - 50) / 50.0  # -1 to +1
            # Contrarian: invert (fear = positive signal)
            fg_contrarian = -fg_normalized * 0.3
            effective_score = max(-1.0, min(1.0, effective_score + fg_contrarian))
            reasons.append(f"F&G index {fear_greed_value} (contrarian adj {fg_contrarian:+.2f})")

        # Determine signal
        if effective_score > 0.3:
            signal = "bullish"
        elif effective_score < -0.3:
            signal = "bearish"
        else:
            signal = "neutral"

        strength = min(abs(effective_score), 1.0)

        return StrategySignal("sentiment", signal, strength,
                              "; ".join(reasons), True)


if __name__ == "__main__":
    s = SentimentStrategy()

    bullish = s.analyze({
        "sentiment_score": 0.5,
        "headlines": [
            {"sentiment": 0.6}, {"sentiment": 0.3}, {"sentiment": 0.4}
        ],
        "asset_type": "stock",
    })
    print(f"Bullish: {bullish}")

    crypto = s.analyze({
        "sentiment_score": 0.1,
        "headlines": [],
        "asset_type": "crypto",
        "fear_greed_value": 15,  # extreme fear -> contrarian bullish
    })
    print(f"Crypto (fear): {crypto}")

    bearish = s.analyze({
        "sentiment_score": -0.5,
        "headlines": [
            {"sentiment": -0.4}, {"sentiment": -0.6}, {"sentiment": -0.3}
        ],
        "asset_type": "stock",
    })
    print(f"Bearish: {bearish}")
