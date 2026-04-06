"""Multi-Signal Fusion — master strategy aggregating all other signals."""

from strategies.base_strategy import BaseStrategy, StrategySignal

CATEGORY_MAP = {
    "mean_reversion": "technical",
    "session_momentum": "technical",
    "sentiment": "sentiment",
    "btc_correlation": "macro",
    "fear_greed": "macro",
}

WEIGHT_KEY = {
    "technical": "weight_technical",
    "sentiment": "weight_sentiment",
    "macro": "weight_macro",
}


class MultiSignalStrategy(BaseStrategy):
    docs_url = "https://www.investopedia.com/terms/t/technicalanalysis.asp"
    config_key = "STRATEGY_MULTI_SIGNAL_ENABLED"

    def analyze(self, data: dict) -> StrategySignal:
        signals: list[StrategySignal] = data.get("strategy_signals", [])
        w_tech = data.get("weight_technical", 0.40)
        w_sent = data.get("weight_sentiment", 0.30)
        w_macro = data.get("weight_macro", 0.30)
        weights = {"technical": w_tech, "sentiment": w_sent, "macro": w_macro}

        enabled_signals = [s for s in signals if s.enabled and s.name != "multi_signal"]

        if not enabled_signals:
            return StrategySignal("multi_signal", "neutral", 0.0,
                                  "No enabled strategy signals", True)

        # Group signals by category
        categories: dict[str, list[float]] = {"technical": [], "sentiment": [], "macro": []}
        dominant_name = ""
        dominant_abs = 0.0

        for sig in enabled_signals:
            numeric = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}.get(sig.signal, 0.0)
            weighted_val = numeric * sig.strength
            cat = CATEGORY_MAP.get(sig.name, "technical")
            categories[cat].append(weighted_val)

            if abs(weighted_val) > dominant_abs:
                dominant_abs = abs(weighted_val)
                dominant_name = sig.name

        # Composite score: weighted average of category averages
        composite = 0.0
        for cat, vals in categories.items():
            if vals:
                cat_avg = sum(vals) / len(vals)
                composite += cat_avg * weights.get(cat, 0.0)

        if composite > 0.4:
            signal = "bullish"
        elif composite < -0.4:
            signal = "bearish"
        else:
            signal = "neutral"

        strength = min(abs(composite), 1.0)
        reason = (f"Composite score {composite:+.3f} from {len(enabled_signals)} strategies. "
                  f"Dominant: {dominant_name}")

        return StrategySignal("multi_signal", signal, strength, reason, True)


if __name__ == "__main__":
    s = MultiSignalStrategy()

    signals = [
        StrategySignal("mean_reversion", "bullish", 0.8, "Z-score -2.3", True),
        StrategySignal("sentiment", "bullish", 0.6, "Positive news", True),
        StrategySignal("btc_correlation", "bullish", 0.5, "BTC trending up", True),
        StrategySignal("fear_greed", "bullish", 0.9, "Extreme Fear", True),
        StrategySignal("session_momentum", "neutral", 0.0, "Dead session", True),
    ]

    r = s.analyze({
        "strategy_signals": signals,
        "weight_technical": 0.40,
        "weight_sentiment": 0.30,
        "weight_macro": 0.30,
    })
    print(f"Multi-signal result: {r}")

    # Mixed signals
    mixed = [
        StrategySignal("mean_reversion", "bearish", 0.7, "Z-score +2.1", True),
        StrategySignal("sentiment", "bullish", 0.5, "Positive", True),
        StrategySignal("fear_greed", "neutral", 0.0, "Neutral", True),
    ]
    r = s.analyze({"strategy_signals": mixed})
    print(f"Mixed signals: {r}")
