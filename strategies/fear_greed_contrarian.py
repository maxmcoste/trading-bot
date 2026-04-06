"""Fear & Greed Contrarian strategy — buy fear, sell euphoria."""

from strategies.base_strategy import BaseStrategy, StrategySignal


class FearGreedStrategy(BaseStrategy):
    docs_url = "https://alternative.me/crypto/fear-and-greed-index/"
    config_key = "STRATEGY_FEAR_GREED_ENABLED"

    def analyze(self, data: dict) -> StrategySignal:
        value = data.get("fear_greed_value")
        history = data.get("fear_greed_history", [])

        if value is None:
            return StrategySignal("fear_greed", "neutral", 0.0,
                                  "Fear & Greed data unavailable", True)

        # Determine signal (contrarian)
        if value <= 24:
            signal = "bullish"
            strength = 0.9
            label = "Extreme Fear"
        elif value <= 44:
            signal = "bullish"
            strength = 0.6
            label = "Fear"
        elif value <= 55:
            signal = "neutral"
            strength = 0.0
            label = "Neutral"
        elif value <= 74:
            signal = "bearish"
            strength = 0.6
            label = "Greed"
        else:
            signal = "bearish"
            strength = 0.9
            label = "Extreme Greed"

        reasons = [f"F&G Index {value} ({label})"]

        # Check for 3+ consecutive days of Extreme Fear
        if signal == "bullish" and value <= 24 and len(history) >= 3:
            consecutive_extreme = 0
            for h in history[:3]:
                h_val = h.get("value", 50) if isinstance(h, dict) else 50
                if isinstance(h_val, str):
                    h_val = int(h_val)
                if h_val <= 24:
                    consecutive_extreme += 1
                else:
                    break
            if consecutive_extreme >= 3:
                strength = 0.5
                reasons.append("3+ days Extreme Fear — falling knife risk, reduced strength")

        return StrategySignal("fear_greed", signal, strength,
                              "; ".join(reasons), True)


if __name__ == "__main__":
    s = FearGreedStrategy()

    extreme_fear = s.analyze({"fear_greed_value": 15, "fear_greed_history": []})
    print(f"Extreme Fear: {extreme_fear}")

    falling_knife = s.analyze({
        "fear_greed_value": 12,
        "fear_greed_history": [
            {"value": 10}, {"value": 18}, {"value": 22}
        ],
    })
    print(f"Falling knife: {falling_knife}")

    extreme_greed = s.analyze({"fear_greed_value": 85})
    print(f"Extreme Greed: {extreme_greed}")

    neutral = s.analyze({"fear_greed_value": 50})
    print(f"Neutral: {neutral}")

    no_data = s.analyze({})
    print(f"No data: {no_data}")
