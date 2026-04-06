"""Mean Reversion strategy based on Z-score, RSI, and Bollinger Bands."""

from strategies.base_strategy import BaseStrategy, StrategySignal


class MeanReversionStrategy(BaseStrategy):
    docs_url = "https://www.investopedia.com/terms/m/meanreversion.asp"
    config_key = "STRATEGY_MEAN_REVERSION_ENABLED"

    def analyze(self, data: dict) -> StrategySignal:
        z_score = data.get("z_score")
        rsi = data.get("rsi")
        bb_position = data.get("bb_position")

        if z_score is None:
            return StrategySignal("mean_reversion", "neutral", 0.0,
                                  "Insufficient data for Z-score", True)

        strength = min(abs(z_score) / 3.0, 1.0)
        reasons = [f"Z-score {z_score:+.2f}"]

        if z_score < -2.0:
            signal = "bullish"
            reasons.append("strong: price far below mean")
        elif z_score < -1.0:
            signal = "bullish"
            reasons.append("moderate: price below mean")
        elif z_score > 2.0:
            signal = "bearish"
            reasons.append("strong: price far above mean")
        elif z_score > 1.0:
            signal = "bearish"
            reasons.append("moderate: price above mean")
        else:
            signal = "neutral"

        # RSI confirmation
        if rsi is not None:
            if rsi < 30 and signal in ("bullish", "neutral"):
                strength = min(strength + 0.15, 1.0)
                signal = "bullish"
                reasons.append(f"RSI {rsi:.0f} oversold")
            elif rsi > 70 and signal in ("bearish", "neutral"):
                strength = min(strength + 0.15, 1.0)
                signal = "bearish"
                reasons.append(f"RSI {rsi:.0f} overbought")

        # Bollinger Band confirmation
        if bb_position is not None:
            if bb_position < 0.1 and signal in ("bullish", "neutral"):
                strength = min(strength + 0.1, 1.0)
                signal = "bullish"
                reasons.append(f"BB position {bb_position:.2f} near lower band")
            elif bb_position > 0.9 and signal in ("bearish", "neutral"):
                strength = min(strength + 0.1, 1.0)
                signal = "bearish"
                reasons.append(f"BB position {bb_position:.2f} near upper band")

        return StrategySignal("mean_reversion", signal, strength,
                              "; ".join(reasons), True)


if __name__ == "__main__":
    s = MeanReversionStrategy()

    bullish = s.analyze({"z_score": -2.5, "rsi": 25, "bb_position": 0.05})
    print(f"Bullish: {bullish}")

    bearish = s.analyze({"z_score": 2.3, "rsi": 78, "bb_position": 0.95})
    print(f"Bearish: {bearish}")

    neutral = s.analyze({"z_score": 0.3, "rsi": 52, "bb_position": 0.55})
    print(f"Neutral: {neutral}")
