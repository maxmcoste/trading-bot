"""Risk manager — validates trade decisions against risk parameters."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from ai.claude_agent import TradeDecision
from config import Settings
from strategies.session_momentum import get_current_session

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
CET = ZoneInfo("Europe/Berlin")


class RiskCheckResult:
    def __init__(self, approved: bool, reason: str = "", decision: TradeDecision | None = None):
        self.approved = approved
        self.reason = reason
        self.decision = decision


class RiskManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.daily_pnl: float = 0.0
        self.daily_deployed: float = 0.0
        self.budget_paused: bool = False
        self.open_positions_stock: int = 0
        self.open_positions_crypto: int = 0
        self.position_symbols: set[str] = set()
        self.trades_today: int = 0

    @property
    def daily_budget(self) -> float:
        return self.settings.TRADING_BUDGET

    @property
    def daily_loss_limit(self) -> float:
        """Max loss in EUR before circuit breaker triggers."""
        return self.daily_budget * self.settings.MAX_DAILY_LOSS_PCT / 100.0

    @property
    def budget_remaining(self) -> float:
        return max(0, self.daily_budget - self.daily_deployed)

    def _is_nyse_open(self) -> bool:
        now_et = datetime.now(ET)
        if now_et.weekday() >= 5:
            return False
        t = now_et.time()
        from datetime import time as dtime
        return dtime(9, 30) <= t <= dtime(16, 0)

    # ── Daily Budget Checks ───────────────────────────────────

    def can_deploy(self, cost: float) -> tuple[bool, str]:
        """Check if a new deployment of `cost` EUR is allowed."""
        if self.budget_paused:
            return False, f"Trading paused — daily loss limit ({self.settings.MAX_DAILY_LOSS_PCT}%) hit"

        if self.daily_deployed + cost > self.daily_budget:
            remaining = self.budget_remaining
            return False, f"Daily budget exhausted: {self.daily_deployed:.2f}/{self.daily_budget:.2f} deployed, only {remaining:.2f} remaining"

        return True, "OK"

    def register_deployment(self, cost: float):
        """Record capital deployed in a new BUY trade."""
        self.daily_deployed += cost
        self.trades_today += 1

    def check_circuit_breaker(self, unrealized_pnl: float = 0.0) -> bool:
        """Check if total losses (realized + unrealized) exceed the daily loss limit.
        Returns True if the circuit breaker has tripped."""
        total_loss = self.daily_pnl + unrealized_pnl
        if total_loss <= -self.daily_loss_limit and self.daily_loss_limit > 0:
            if not self.budget_paused:
                self.budget_paused = True
                logger.warning(
                    f"CIRCUIT BREAKER: Daily loss {total_loss:.2f} exceeds "
                    f"limit -{self.daily_loss_limit:.2f}. Trading paused."
                )
            return True
        # Auto-resume if losses recover above threshold
        if self.budget_paused and total_loss > -self.daily_loss_limit:
            self.budget_paused = False
            logger.info("Circuit breaker reset — losses recovered above threshold")
        return self.budget_paused

    def get_budget_status(self, unrealized_pnl: float = 0.0) -> dict:
        """Return full budget status for the dashboard."""
        total_pnl = self.daily_pnl + unrealized_pnl
        loss_limit = self.daily_loss_limit
        loss_pct = (abs(total_pnl) / loss_limit * 100) if loss_limit > 0 and total_pnl < 0 else 0

        return {
            "daily_limit": self.daily_budget,
            "deployed": round(self.daily_deployed, 2),
            "deployed_pct": round(self.daily_deployed / max(self.daily_budget, 1) * 100, 1),
            "remaining": round(self.budget_remaining, 2),
            "daily_pnl": round(total_pnl, 2),
            "loss_limit": round(-loss_limit, 2),
            "loss_pct": round(min(loss_pct, 100), 1),
            "paused": self.budget_paused,
            "trades_today": self.trades_today,
        }

    # ── Trade Risk Checks ─────────────────────────────────────

    def check(self, decision: TradeDecision, symbol: str,
              btc_change_1h: float | None = None) -> RiskCheckResult:
        """Run all risk checks. Returns approved/rejected with reason."""

        if decision.action == "HOLD":
            return RiskCheckResult(True, "HOLD — no action needed", decision)

        # Budget circuit breaker — applies to all asset types
        if self.budget_paused and decision.action == "BUY":
            return RiskCheckResult(False,
                f"Trading paused — daily loss limit ({self.settings.MAX_DAILY_LOSS_PCT}%) exceeded")

        s = self.settings

        # --- STOCK CHECKS ---
        if decision.asset_type == "stock":
            if decision.confidence < s.CONFIDENCE_THRESHOLD:
                return RiskCheckResult(False,
                    f"Confidence {decision.confidence:.2f} < threshold {s.CONFIDENCE_THRESHOLD}")

            if self.open_positions_stock >= s.MAX_OPEN_POSITIONS:
                return RiskCheckResult(False,
                    f"Max stock positions reached ({s.MAX_OPEN_POSITIONS})")

            if symbol in self.position_symbols:
                return RiskCheckResult(False, f"Already in position for {symbol}")

            if not self._is_nyse_open():
                return RiskCheckResult(False, "NYSE is closed")

            decision.position_size_pct = min(decision.position_size_pct,
                                             s.MAX_POSITION_SIZE_PCT)
            decision.stop_loss_pct = max(0.5, min(decision.stop_loss_pct, 3.0))
            decision.take_profit_pct = max(1.0, min(decision.take_profit_pct, 6.0))

        # --- CRYPTO CHECKS ---
        elif decision.asset_type == "crypto":
            if decision.confidence < s.CRYPTO_CONFIDENCE_THRESHOLD:
                return RiskCheckResult(False,
                    f"Confidence {decision.confidence:.2f} < crypto threshold {s.CRYPTO_CONFIDENCE_THRESHOLD}")

            if self.open_positions_crypto >= s.CRYPTO_MAX_OPEN_POSITIONS:
                return RiskCheckResult(False,
                    f"Max crypto positions reached ({s.CRYPTO_MAX_OPEN_POSITIONS})")

            session = get_current_session()
            if session == "dead" and decision.action == "BUY":
                return RiskCheckResult(False, "Dead session — no new crypto positions")

            if (btc_change_1h is not None and btc_change_1h < -3.0
                    and s.STRATEGY_BTC_CORRELATION_ENABLED):
                return RiskCheckResult(False,
                    f"BTC 1h change {btc_change_1h:+.1f}% — market stress")

            if symbol in self.position_symbols:
                return RiskCheckResult(False, f"Already in position for {symbol}")

            decision.position_size_pct = min(decision.position_size_pct,
                                             s.CRYPTO_MAX_POSITION_SIZE_PCT)
            decision.stop_loss_pct = max(1.5, min(decision.stop_loss_pct, 5.0))
            decision.take_profit_pct = max(3.0, min(decision.take_profit_pct, 10.0))

        return RiskCheckResult(True, "Approved", decision)

    def register_position(self, symbol: str, asset_type: str):
        self.position_symbols.add(symbol)
        if asset_type == "stock":
            self.open_positions_stock += 1
        else:
            self.open_positions_crypto += 1

    def close_position(self, symbol: str, asset_type: str, pnl: float):
        self.position_symbols.discard(symbol)
        if asset_type == "stock":
            self.open_positions_stock = max(0, self.open_positions_stock - 1)
        else:
            self.open_positions_crypto = max(0, self.open_positions_crypto - 1)
        self.daily_pnl += pnl

    def reset_daily(self):
        self.daily_pnl = 0.0
        self.daily_deployed = 0.0
        self.budget_paused = False
        self.trades_today = 0


if __name__ == "__main__":
    from config import Settings
    from ai.claude_agent import TradeDecision

    s = Settings()
    rm = RiskManager(s)

    print(f"Daily budget: {rm.daily_budget}, loss limit: {rm.daily_loss_limit}")

    # Test deployment
    ok, reason = rm.can_deploy(50.0)
    print(f"Can deploy 50: {ok} — {reason}")
    rm.register_deployment(50.0)
    print(f"Deployed: {rm.daily_deployed}, remaining: {rm.budget_remaining}")

    ok, reason = rm.can_deploy(60.0)
    print(f"Can deploy 60 more: {ok} — {reason}")

    # Test circuit breaker
    rm.daily_pnl = -2.5
    tripped = rm.check_circuit_breaker(unrealized_pnl=-1.0)
    print(f"Circuit breaker (pnl=-2.5, unreal=-1.0): tripped={tripped}")

    status = rm.get_budget_status(unrealized_pnl=-1.0)
    print(f"Budget status: {status}")
