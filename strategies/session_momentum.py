"""Session Momentum strategy — crypto-specific, based on trading sessions (CET)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from strategies.base_strategy import BaseStrategy, StrategySignal

CET = ZoneInfo("Europe/Berlin")


def get_current_session(now: datetime | None = None) -> str:
    """Return current crypto trading session: asia|europe|usa|dead."""
    if now is None:
        now = datetime.now(CET)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=CET)
    else:
        now = now.astimezone(CET)

    hour = now.hour
    if 1 <= hour < 9:
        return "asia"
    elif 9 <= hour < 14:
        return "europe"
    elif 14 <= hour < 22:
        return "usa"
    else:
        return "dead"


class SessionMomentumStrategy(BaseStrategy):
    docs_url = "https://www.investopedia.com/terms/m/momentum_investing.asp"
    config_key = "STRATEGY_SESSION_MOMENTUM_ENABLED"

    def analyze(self, data: dict) -> StrategySignal:
        volume_ratio = data.get("volume_ratio", 1.0)
        current_time = data.get("current_time")

        session = get_current_session(current_time)
        # USA session uses 30-min momentum; Asia/Europe use the 1h momentum
        if session == "usa":
            momentum_pct = data.get("momentum_pct_30min", data.get("momentum_pct", 0.0))
        else:
            momentum_pct = data.get("momentum_pct", 0.0)
        reasons = [f"Session: {session}"]

        # Dead session: always neutral
        if session == "dead":
            return StrategySignal(
                "session_momentum", "neutral", 0.0,
                "Dead session (22:00-01:00 CET) — no new entries", True
            )

        if momentum_pct is None:
            momentum_pct = 0.0

        # Volume confirmation
        if volume_ratio is not None and volume_ratio < 1.2:
            reasons.append(f"Low volume ratio {volume_ratio:.2f} (<1.2)")
            return StrategySignal(
                "session_momentum", "neutral", 0.0,
                "; ".join(reasons), True
            )

        reasons.append(f"Momentum {momentum_pct:+.2f}%")
        if volume_ratio is not None:
            reasons.append(f"Volume ratio {volume_ratio:.2f}")

        # Session-specific thresholds
        if session == "usa":
            threshold = 1.0
        elif session == "asia":
            threshold = 1.0
        else:  # europe
            threshold = 0.8

        if momentum_pct > threshold:
            signal = "bullish"
        elif momentum_pct < -threshold:
            signal = "bearish"
        else:
            signal = "neutral"

        strength = min(abs(momentum_pct) / 3.0, 1.0) if signal != "neutral" else 0.0

        return StrategySignal("session_momentum", signal, strength,
                              "; ".join(reasons), True)


if __name__ == "__main__":
    from datetime import datetime
    from zoneinfo import ZoneInfo

    s = SessionMomentumStrategy()

    # USA session bullish
    usa_time = datetime(2024, 1, 15, 16, 0, tzinfo=CET)
    r = s.analyze({"momentum_pct": 2.5, "volume_ratio": 1.8, "current_time": usa_time})
    print(f"USA bullish: {r}")

    # Dead session
    dead_time = datetime(2024, 1, 15, 23, 0, tzinfo=CET)
    r = s.analyze({"momentum_pct": 3.0, "volume_ratio": 2.0, "current_time": dead_time})
    print(f"Dead session: {r}")

    # Low volume
    r = s.analyze({"momentum_pct": 2.0, "volume_ratio": 0.8, "current_time": usa_time})
    print(f"Low volume: {r}")

    # Asia bearish
    asia_time = datetime(2024, 1, 15, 5, 0, tzinfo=CET)
    r = s.analyze({"momentum_pct": -1.5, "volume_ratio": 1.5, "current_time": asia_time})
    print(f"Asia bearish: {r}")
