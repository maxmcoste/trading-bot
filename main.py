"""Main entry point — orchestrates the trading bot."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import signal
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import uvicorn

from config import settings
from data.indicators import calculate_indicators, latest_signals, calculate_btc_correlation
from data.news_client import NewsClient
from data.fear_greed_client import FearGreedClient
from strategies.mean_reversion import MeanReversionStrategy
from strategies.sentiment_trading import SentimentStrategy
from strategies.btc_correlation_filter import BTCCorrelationStrategy
from strategies.fear_greed_contrarian import FearGreedStrategy
from strategies.session_momentum import SessionMomentumStrategy, get_current_session
from strategies.multi_signal import MultiSignalStrategy
from ai.prompt_builder import build_prompt
from ai.claude_agent import ClaudeAgent
from risk.risk_manager import RiskManager
from risk.position_sizer import calculate_position_size
from execution.paper_mode import PaperExecutor
from execution.coinbase_executor import CoinbaseExecutor
from monitoring.logger import DBLogger
from monitoring.notifier import Notifier
from monitoring.dashboard import app as dashboard_app, bot_state
from feeds.feed_manager import FeedManager

from logging.handlers import RotatingFileHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler("logs/trading_bot.log", maxBytes=8*1024*1024, backupCount=10),
    ],
)
logger = logging.getLogger("main")

ET = ZoneInfo("America/New_York")
CET = ZoneInfo("Europe/Berlin")


class TradingBot:
    def __init__(self):
        self.db = DBLogger()
        self.news = NewsClient(api_key=settings.NEWSAPI_KEY)
        self.fear_greed = FearGreedClient()
        self.claude = ClaudeAgent(api_key=settings.ANTHROPIC_API_KEY)
        self.risk = RiskManager(settings)
        self.notifier = Notifier()
        # Paper executor gets real balance later in init_brokers()
        self.paper = PaperExecutor(initial_cash=settings.PAPER_INITIAL_CASH or 10000.0)

        # Strategies
        self.strategies = {
            "mean_reversion": MeanReversionStrategy(),
            "sentiment": SentimentStrategy(),
            "btc_correlation": BTCCorrelationStrategy(),
            "fear_greed": FearGreedStrategy(),
            "session_momentum": SessionMomentumStrategy(),
            "multi_signal": MultiSignalStrategy(),
        }

        # Broker clients (lazy init)
        self.ibkr = None
        self.coinbase = None
        self.coinbase_executor = CoinbaseExecutor(
            api_key=settings.COINBASE_API_KEY,
            api_secret=settings.COINBASE_API_SECRET,
        )

        # Feed manager
        self.feed_manager = FeedManager(config=settings, db=self.db)

        self._running = True
        self._fg_cache = None

    async def init_brokers(self):
        coinbase_value = 0.0
        ibkr_value = 0.0

        if settings.IBKR_ENABLED:
            try:
                from data.ibkr_client import IBKRClient
                self.ibkr = IBKRClient(settings.IBKR_HOST, settings.IBKR_PORT, settings.IBKR_CLIENT_ID)
                await self.ibkr.connect()
                if self.ibkr.connected:
                    ibkr_value = await self.ibkr.get_portfolio_value()
                    logger.info(f"IBKR portfolio: ${ibkr_value:.2f}")
            except Exception as e:
                logger.warning(f"IBKR init failed: {e}")

        if settings.COINBASE_ENABLED:
            try:
                from data.coinbase_client import CoinbaseClient
                self.coinbase = CoinbaseClient(settings.COINBASE_API_KEY, settings.COINBASE_API_SECRET,
                                               quote_currency=settings.DEFAULT_CURRENCY)
                breakdown = await self.coinbase.get_portfolio_breakdown()
                coinbase_value = breakdown["total_usd"]
                logger.info(f"Coinbase portfolio: ${coinbase_value:.2f} "
                            f"({breakdown['num_assets']} assets)")
                # Store breakdown for dashboard
                bot_state["coinbase_portfolio"] = breakdown
                # Auto-populate crypto watchlist from Coinbase holdings,
                # but only for assets that are actually tradable as X-<quote>
                # and have a meaningful balance. Skip stablecoins & dust.
                qc = settings.DEFAULT_CURRENCY
                stablecoins = {"USDC", "USDT", "DAI", "EUR", "USD", "GBP", "PYUSD"}
                await self.coinbase_executor._ensure_valid_products()
                tradable = self.coinbase_executor._valid_products or {}

                # Prune existing watchlist of non-tradable / delisted pairs
                # AND pairs whose quote currency doesn't match ours
                # (e.g. remove ETH-USD when DEFAULT_CURRENCY=EUR).
                # Cross-pairs with crypto quotes (e.g. SOL-BTC) are allowed.
                fiats = {"EUR", "USD", "GBP", "CAD", "SGD", "AUD", "JPY"}
                def _pair_allowed(pair: str) -> bool:
                    if pair not in tradable:
                        return False
                    parts = pair.split("-")
                    if len(parts) != 2:
                        return False
                    quote = parts[1]
                    # Only allow fiat-quoted pairs if they match our currency
                    if quote in fiats and quote != qc:
                        return False
                    return True

                cleaned_existing = [p for p in settings.WATCHLIST_CRYPTO if _pair_allowed(p)]
                removed = set(settings.WATCHLIST_CRYPTO) - set(cleaned_existing)
                if removed:
                    logger.info(f"Pruned invalid pairs from watchlist: {removed}")

                held_pairs = set()
                for h in breakdown.get("holdings", []):
                    if h.get("type") != "crypto":
                        continue
                    if h.get("value", 0) < 1.0:  # skip dust < €1
                        continue
                    cur = h.get("currency", "")
                    if cur in stablecoins:
                        continue
                    pair = f"{cur}-{qc}"
                    if pair in tradable:
                        held_pairs.add(pair)

                existing = set(cleaned_existing)
                new_pairs = held_pairs - existing
                if new_pairs or removed:
                    settings.WATCHLIST_CRYPTO = list(existing | held_pairs)
                    settings.save_to_env()
                    if new_pairs:
                        logger.info(f"Watchlist updated with Coinbase assets: +{new_pairs}")
            except Exception as e:
                logger.warning(f"Coinbase init failed: {e}")

        # Fix 2: Validate trading budget against real available fiat balance.
        # If TRADING_BUDGET vastly exceeds cash on Coinbase, BUYs will fail.
        try:
            cb_state = bot_state.get("coinbase_portfolio", {}) or {}
            coinbase_cash = cb_state.get("cash", cb_state.get("cash_usd", 0)) or 0
            if settings.TRADING_BUDGET > 0 and 0 < coinbase_cash < settings.TRADING_BUDGET * 0.5:
                logger.warning(
                    f"WARNING: TRADING_BUDGET ({settings.TRADING_BUDGET} {settings.DEFAULT_CURRENCY}) "
                    f"exceeds 200% of available fiat balance ({coinbase_cash:.2f} {settings.DEFAULT_CURRENCY}). "
                    f"Consider reducing TRADING_BUDGET or depositing funds."
                )
                if settings.TRADING_BUDGET > coinbase_cash * 2:
                    effective_budget = coinbase_cash
                    logger.warning(
                        f"Budget auto-capped to {effective_budget:.2f} "
                        f"(available fiat) for this session"
                    )
                    self.risk.daily_budget = min(
                        self.risk.daily_budget,
                        effective_budget * settings.MAX_DAILY_DEPLOY_PCT / 100,
                    )
        except Exception as e:
            logger.debug(f"Budget validation skipped: {e}")

        # Fix 12: Paper mode starting cash — use full real balance, NOT capped to budget.
        # Budget is an operational limit, not the available capital.
        total_real = ibkr_value + coinbase_value
        budget = settings.TRADING_BUDGET
        if settings.PAPER_INITIAL_CASH <= 0 and total_real > 0:
            paper_cash = total_real
            self.paper.portfolio.cash = paper_cash
            logger.info(
                f"Paper mode cash set to real portfolio balance: {paper_cash:.2f} "
                f"(operational budget: {budget:.2f})"
            )
        elif settings.PAPER_INITIAL_CASH > 0:
            self.paper.portfolio.cash = settings.PAPER_INITIAL_CASH
            logger.info(f"Paper mode cash (manual): {settings.PAPER_INITIAL_CASH:.2f}")

        # Store real balances for dashboard
        bot_state["real_balances"] = {
            "ibkr_usd": ibkr_value,
            "coinbase_usd": coinbase_value,
            "total_usd": total_real,
        }

    def _is_nyse_open(self) -> bool:
        now = datetime.now(ET)
        if now.weekday() >= 5:
            return False
        from datetime import time as dtime
        return dtime(9, 30) <= now.time() <= dtime(16, 0)

    def _has_open_position(self, symbol: str) -> bool:
        """Check if there's already an open BUY for this symbol."""
        # Paper positions: O(1) lookup
        if settings.PAPER_MODE and symbol in self.paper.portfolio.positions:
            return True
        # Query ALL unclosed BUYs for this symbol (not just the latest)
        open_trades = self.db.get_open_trades(symbol=symbol)
        return len(open_trades) > 0

    async def analyze_symbol(self, symbol: str, broker: str, asset_type: str):
        """Full analysis cycle for a single symbol."""
        try:
            self.db.log("INFO", "analyze", f"Analyzing {symbol} ({broker}/{asset_type})")

            # a. Fetch OHLCV
            is_cross_pair = (broker == "coinbase" and self.coinbase
                             and self.coinbase._is_crypto_quote(symbol))
            df = None
            if broker == "ibkr" and self.ibkr:
                df = await self.ibkr.get_ohlcv(symbol)
            elif broker == "coinbase" and self.coinbase:
                if is_cross_pair:
                    df = await self.coinbase.get_cross_rate_ohlcv(symbol)
                else:
                    df = await self.coinbase.get_ohlcv(symbol)

            if df is None or df.empty:
                self.db.log("WARNING", "analyze", f"No OHLCV data for {symbol}")
                return

            # b. Calculate indicators
            df = calculate_indicators(df)
            tech = latest_signals(df)
            price = float(df.iloc[-1]["close"])

            # c. Fetch sentiment
            try:
                sentiment = await self.news.fetch_sentiment(symbol)
            except Exception:
                from data.news_client import SentimentData
                sentiment = SentimentData(score=0.0, news_count=0)

            # d. Crypto-specific: Fear & Greed, BTC data
            fg_data = None
            btc_change_1h = None
            btc_change_4h = None
            session = None
            btc_df = None
            btc_tech = None
            btc_pair = f"BTC-{settings.DEFAULT_CURRENCY}"

            if asset_type == "crypto":
                try:
                    fg_data = await self.fear_greed.fetch()
                    self._fg_cache = fg_data
                except Exception:
                    fg_data = self._fg_cache

                session = get_current_session()

                # BTC correlation data — fetch BTC OHLCV once per altcoin analysis
                if self.coinbase and symbol != btc_pair:
                    btc_df = await self.coinbase.get_ohlcv(btc_pair)
                    if btc_df is None or btc_df.empty:
                        logger.warning(
                            f"[{symbol}] BTC OHLCV unavailable — "
                            f"BTC Correlation filter disabled for this cycle"
                        )
                        self.db.log("WARNING", "analyze",
                                    f"{symbol}: BTC data unavailable, correlation filter skipped")
                        btc_df = None
                    else:
                        btc_df = calculate_indicators(btc_df)
                        btc_tech = latest_signals(btc_df)
                        if len(btc_df) >= 12:
                            btc_change_1h = ((btc_df.iloc[-1]["close"] - btc_df.iloc[-12]["close"])
                                             / btc_df.iloc[-12]["close"] * 100)
                        if len(btc_df) >= 48:
                            btc_change_4h = ((btc_df.iloc[-1]["close"] - btc_df.iloc[-48]["close"])
                                             / btc_df.iloc[-48]["close"] * 100)

            # e. Run all enabled strategies
            strategy_data = {
                "z_score": tech.z_score,
                "rsi": tech.rsi,
                "bb_position": tech.bb_position,
                "sentiment_score": sentiment.score,
                "headlines": sentiment.headlines,
                "asset_type": asset_type,
                "fear_greed_value": fg_data.value if fg_data else None,
                "fear_greed_history": fg_data.history if fg_data else [],
                # Fix 5: 12 candles = 1h on 5-min candles (aligned with SessionMomentum)
                "momentum_pct": ((df.iloc[-1]["close"] - df.iloc[-12]["close"]) / df.iloc[-12]["close"] * 100) if len(df) >= 12 else 0,
                # 6 candles = 30min, used by SessionMomentum in USA session
                "momentum_pct_30min": ((df.iloc[-1]["close"] - df.iloc[-6]["close"]) / df.iloc[-6]["close"] * 100) if len(df) >= 6 else 0,
                "volume_ratio": tech.volume_ratio,
                "symbol": symbol,
                "btc_change_1h": btc_change_1h,
                "btc_change_4h": btc_change_4h,
                # Fix 4: for altcoins use BTC's own EMA; for BTC itself use local tech
                "btc_ema20": (btc_tech.ema_20 if btc_tech is not None and symbol != btc_pair
                              else (tech.ema_20 if symbol == btc_pair else None)),
                "btc_ema50": (btc_tech.ema_50 if btc_tech is not None and symbol != btc_pair
                              else (tech.ema_50 if symbol == btc_pair else None)),
                # Fix 3: real rolling Pearson correlation vs BTC (0.5 = neutral fallback)
                "btc_correlation_24h": calculate_btc_correlation(df, btc_df) if btc_df is not None else 0.5,
            }

            signals = []
            for name, strat in self.strategies.items():
                if name == "multi_signal":
                    continue
                config_key = strat.config_key
                enabled = getattr(settings, config_key, False)
                if not enabled:
                    continue
                try:
                    sig = strat.analyze(strategy_data)
                    sig.enabled = enabled
                    signals.append(sig)
                    self.db.log_signal(symbol, sig.name, sig.signal, sig.strength, sig.reason, enabled, price=price)
                except Exception as e:
                    logger.error(f"Strategy {name} failed for {symbol}: {e}")

            # Run multi-signal fusion
            if settings.STRATEGY_MULTI_SIGNAL_ENABLED:
                multi_data = {
                    "strategy_signals": signals,
                    "weight_technical": settings.STRATEGY_WEIGHT_TECHNICAL,
                    "weight_sentiment": settings.STRATEGY_WEIGHT_SENTIMENT,
                    "weight_macro": settings.STRATEGY_WEIGHT_MACRO,
                }
                multi_sig = self.strategies["multi_signal"].analyze(multi_data)
                signals.append(multi_sig)

            # f. Build prompt (with feed intelligence)
            feed_summary = self.feed_manager.get_feed_summary_for_ai(symbol=symbol)

            prompt = build_prompt(
                symbol=symbol, asset_type=asset_type, price=price,
                technical=tech, signals=signals,
                sentiment_score=sentiment.score, news_count=sentiment.news_count,
                fear_greed_value=fg_data.value if fg_data else None,
                session=session, btc_change_1h=btc_change_1h, btc_change_4h=btc_change_4h,
                feed_items=feed_summary if feed_summary else None,
            )

            # g. Call Claude
            decision = await self.claude.analyze(symbol, prompt)
            if decision is None:
                self.db.log("WARNING", "analyze", f"Claude returned no decision for {symbol}")
                return

            # h. Risk check
            risk_result = self.risk.check(decision, symbol, btc_change_1h)

            if not risk_result.approved:
                self.db.log("INFO", "risk", f"{symbol}: REJECTED — {risk_result.reason}")
                decision.action = "HOLD"
                decision.warnings.append(f"Risk rejected: {risk_result.reason}")

            # Skip BUY if already holding an open position for this symbol
            if decision.action == "BUY" and self._has_open_position(symbol):
                decision.action = "HOLD"
                decision.warnings.append("Already holding an open position")
                self.db.log("INFO", "risk", f"{symbol}: skipped BUY — open position exists")

            # Update latest signals for dashboard
            bot_state["latest_signals"] = [
                s for s in bot_state.get("latest_signals", []) if s.get("symbol") != symbol
            ] + [{"symbol": symbol, "action": decision.action, "confidence": decision.confidence}]

            # i. Execute if approved
            executed = False
            actual_qty = 0.0
            actual_cost = 0.0
            sell_pnl = None  # set on SELL for close_trade

            if decision.action in ("BUY", "SELL") and risk_result.approved:
                if settings.PAPER_MODE:
                    # Size position against remaining daily budget
                    budget = min(self.paper.portfolio.total_value, self.risk.budget_remaining)
                    pos_info = calculate_position_size(
                        budget,
                        decision.position_size_pct, price, tech.atr,
                    )
                    estimated_cost = pos_info["position_usd"]

                    # Daily budget gate — check before executing BUY
                    if decision.action == "BUY":
                        can_buy, budget_reason = self.risk.can_deploy(estimated_cost)
                        if not can_buy:
                            decision.action = "HOLD"
                            decision.warnings.append(f"Budget rejected: {budget_reason}")
                            self.db.log("INFO", "risk", f"{symbol}: {budget_reason}")
                            executed = False
                            # Fall through to log trade as HOLD

                    if decision.action in ("BUY", "SELL"):
                        result = self.paper.execute_trade(
                            symbol, broker, asset_type, decision.action,
                            price, pos_info["quantity"],
                            decision.stop_loss_pct, decision.take_profit_pct,
                        )
                        executed = result.get("executed", False)
                        if executed and decision.action == "BUY":
                            actual_qty = result.get("quantity", 0)
                            actual_cost = result.get("cost", 0)
                            self.risk.register_position(symbol, asset_type)
                            self.risk.register_deployment(actual_cost)
                            self._persist_risk_counters()
                        elif executed and decision.action == "SELL":
                            actual_qty = result.get("quantity", 0)
                            sell_pnl = {
                                "pnl_usd": result.get("pnl_usd", 0),
                                "pnl_pct": result.get("pnl_pct", 0),
                            }
                            self.risk.close_position(symbol, asset_type, sell_pnl["pnl_usd"])
                            self._persist_risk_counters()

                else:
                    # ── LIVE execution ──────────────────────────────
                    cb_portfolio = bot_state.get("coinbase_portfolio", {})
                    quote_currency = symbol.split("-")[1] if "-" in symbol else settings.DEFAULT_CURRENCY
                    fiat_currencies = {"EUR", "USD", "GBP", "USDC", "USDT", "DAI"}

                    if quote_currency in fiat_currencies:
                        available_balance = cb_portfolio.get("cash", 0)
                    else:
                        # Crypto quote currency — look up its balance
                        available_balance = 0.0
                        for h in cb_portfolio.get("holdings", []):
                            if h.get("currency") == quote_currency:
                                available_balance = h.get("balance", 0)
                                break

                        # Enforce reserve: keep minimum BTC balance untouchable
                        if quote_currency == "BTC" and settings.BTC_RESERVE_EUR > 0:
                            btc_eur_price = 0.0
                            for h in cb_portfolio.get("holdings", []):
                                if h.get("currency") == "BTC" and h.get("price"):
                                    btc_eur_price = h["price"]
                                    break
                            if btc_eur_price > 0:
                                reserve_btc = settings.BTC_RESERVE_EUR / btc_eur_price
                                available_balance = max(0.0, available_balance - reserve_btc)

                    budget = min(available_balance, self.risk.budget_remaining)

                    if decision.action == "BUY" and budget < (0.00001 if quote_currency not in fiat_currencies else 1.0):
                        decision.action = "HOLD"
                        decision.warnings.append(
                            f"Insufficient {quote_currency} balance ({available_balance:.6f})")
                        self.db.log("INFO", "risk",
                                    f"{symbol}: insufficient {quote_currency} "
                                    f"({available_balance:.6f})")
                        executed = False

                    if decision.action == "BUY":
                        pos_info = calculate_position_size(
                            budget,
                            decision.position_size_pct, price, tech.atr,
                        )
                        estimated_cost = pos_info["position_usd"]
                        # Convert to EUR for daily budget gate if quote is crypto
                        cost_eur = estimated_cost
                        if is_cross_pair:
                            quote_eur_price = 0
                            for h in cb_portfolio.get("holdings", []):
                                if h.get("currency") == quote_currency and h.get("price"):
                                    quote_eur_price = h["price"]
                                    break
                            cost_eur = estimated_cost * quote_eur_price if quote_eur_price else 0
                        can_buy, budget_reason = self.risk.can_deploy(cost_eur)
                        if not can_buy:
                            decision.action = "HOLD"
                            decision.warnings.append(f"Budget rejected: {budget_reason}")
                            self.db.log("INFO", "risk", f"{symbol}: {budget_reason}")
                            executed = False
                    elif decision.action == "SELL":
                        # For SELL, size is the held amount of the asset
                        portfolio_value = bot_state.get("real_balances", {}).get("total_usd", 0)
                        pos_info = calculate_position_size(
                            portfolio_value,
                            decision.position_size_pct, price, tech.atr,
                        )
                        estimated_cost = pos_info["quantity"]  # base amount to sell

                    if decision.action in ("BUY", "SELL") and broker == "coinbase":
                        result = await self.coinbase_executor.execute(
                            symbol, decision.action, estimated_cost, price,
                        )
                        executed = result.get("executed", False)
                        if executed:
                            actual_qty = pos_info["quantity"]
                            actual_cost = estimated_cost
                            if decision.action == "BUY":
                                self.risk.register_position(symbol, asset_type)
                                self.risk.register_deployment(cost_eur if is_cross_pair else actual_cost)
                                self._persist_risk_counters()
                            logger.info(f"[LIVE] {decision.action} {actual_qty:.6f} {symbol} "
                                        f"@ {price:.2f} = {actual_cost:.2f}")
                        else:
                            reason = result.get("reason", "execution failed")
                            decision.warnings.append(f"Live execution failed: {reason}")
                            decision.action = "HOLD"
                            self.db.log("ERROR", "execute", f"{symbol}: {reason}")

            # j. Log trade (skip HOLD — only log BUY/SELL)
            if decision.action == "HOLD":
                self.db.log("INFO", "analyze",
                            f"{symbol}: HOLD conf={decision.confidence:.2f}")
                return

            tech_snapshot = json.dumps(
                {k: round(v, 6) for k, v in dataclasses.asdict(tech).items() if v is not None}
            )
            trade_id = self.db.log_trade(
                symbol=symbol, broker=broker, asset_type=asset_type,
                action=decision.action, confidence=decision.confidence,
                strategy="multi_signal", dominant_strategy=decision.dominant_strategy,
                price=price, quantity=actual_qty, position_size_usd=actual_cost,
                stop_loss_pct=decision.stop_loss_pct,
                take_profit_pct=decision.take_profit_pct,
                reasoning=decision.reasoning,
                strategy_signals={s.name: {"signal": s.signal, "strength": s.strength} for s in signals},
                warnings=decision.warnings,
                fear_greed_value=fg_data.value if fg_data else None,
                session=session,
                paper_mode=int(settings.PAPER_MODE),
                executed=int(executed),
                technical_snapshot=tech_snapshot,
                **({"pnl_usd": sell_pnl["pnl_usd"], "pnl_pct": sell_pnl["pnl_pct"],
                    "close_price": price} if sell_pnl else {}),
            )

            # Store trade_id on paper position for future close_trade() calls
            if executed and decision.action == "BUY" and symbol in self.paper.portfolio.positions:
                self.paper.portfolio.positions[symbol].trade_id = trade_id

            # If SELL, close the original BUY trade in DB
            if executed and sell_pnl:
                buy_trade_id = result.get("trade_id", 0)
                if buy_trade_id:
                    self.db.close_trade(buy_trade_id, price,
                                        sell_pnl["pnl_usd"], sell_pnl["pnl_pct"])

            # k. Notify
            if executed and decision.action != "HOLD":
                self.notifier.notify_trade(symbol, decision.action, decision.confidence,
                                           price, decision.reasoning)

            self.db.log("INFO", "analyze",
                        f"{symbol}: {decision.action} conf={decision.confidence:.2f} "
                        f"exec={executed}")

        except Exception as e:
            logger.exception(f"Error analyzing {symbol}: {e}")
            self.db.log_error("analyze", type(e).__name__, str(e))
            self.notifier.notify_error("analyze", str(e))

    async def _monitor_open_positions(self):
        """Monitor ALL open positions (paper + live) for stop-loss / take-profit."""
        try:
            # Get current prices from cached portfolio
            cb_portfolio = bot_state.get("coinbase_portfolio", {})
            price_map = {}
            for h in cb_portfolio.get("holdings", []):
                if h.get("currency") and h.get("price"):
                    price_map[h["currency"]] = h["price"]

            if not price_map:
                return

            # Check paper positions
            if settings.PAPER_MODE:
                closed = self.paper.check_stops(
                    {sym: price_map.get(sym.split("-")[0], 0)
                     for sym in self.paper.portfolio.positions})
                for c in closed:
                    symbol = c.get("symbol", "")
                    self.db.log("INFO", "stops",
                                f"[PAPER] {c.get('trigger', 'stop')} triggered for {symbol}: "
                                f"P&L {c.get('pnl_usd', 0):.2f}")
                    # Close trade in DB
                    trade_id = c.get("trade_id", 0)
                    if trade_id:
                        self.db.close_trade(trade_id, c.get("exit_price", 0),
                                            c.get("pnl_usd", 0), c.get("pnl_pct", 0))
                    self.risk.close_position(symbol, "crypto", c.get("pnl_usd", 0))
                    self._persist_risk_counters()

            # Check ALL unclosed DB trades (covers live trades even in paper mode)
            open_trades = self.db.get_trades(limit=100, action="BUY")
            for t in open_trades:
                if t.get("closed_at") or not t.get("executed"):
                    continue
                symbol = t["symbol"]
                entry_price = t.get("price", 0)
                quantity = t.get("quantity", 0)
                sl_pct = t.get("stop_loss_pct", 0)
                tp_pct = t.get("take_profit_pct", 0)
                is_paper = t.get("paper_mode", 1)

                if not entry_price or not quantity:
                    continue

                # Skip paper trades — already handled by paper executor above
                if is_paper and settings.PAPER_MODE:
                    continue

                parts = symbol.split("-") if "-" in symbol else [symbol]
                base = parts[0]
                quote = parts[1] if len(parts) > 1 else settings.DEFAULT_CURRENCY
                fiat_set = {"EUR", "USD", "GBP", "USDC", "USDT", "DAI"}

                if quote not in fiat_set and quote in price_map and price_map[quote] > 0:
                    # Cross pair: compute price in quote crypto
                    base_eur = price_map.get(base, 0)
                    current_price = base_eur / price_map[quote] if base_eur else None
                else:
                    current_price = price_map.get(base)
                if not current_price:
                    continue

                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                trigger = None

                if sl_pct and pnl_pct <= -sl_pct:
                    trigger = "stop_loss"
                elif tp_pct and pnl_pct >= tp_pct:
                    trigger = "take_profit"

                if not trigger:
                    continue

                pnl_usd = (current_price - entry_price) * quantity

                # Execute sell for live trades
                if not is_paper:
                    result = await self.coinbase_executor.execute(
                        symbol, "SELL", quantity, current_price)
                    if not result.get("executed"):
                        reason = result.get("reason", "") or ""
                        # Retry with actual on-chain balance on INSUFFICIENT_FUND
                        if "INSUFFICIENT_FUND" in reason.upper():
                            actual_qty = 0.0
                            for h in cb_portfolio.get("holdings", []):
                                if h.get("currency") == base:
                                    actual_qty = float(h.get("balance", 0) or 0)
                                    break
                            # Dust guard: require at least 50% of expected (and >0)
                            min_qty = max(quantity * 0.5, 0.0)
                            if actual_qty > 0 and actual_qty >= min_qty and actual_qty < quantity:
                                self.db.log("INFO", "stops",
                                            f"{symbol} retry sell with actual balance "
                                            f"{actual_qty} (tracked {quantity})")
                                result = await self.coinbase_executor.execute(
                                    symbol, "SELL", actual_qty, current_price)
                                if result.get("executed"):
                                    quantity = actual_qty
                                    pnl_usd = (current_price - entry_price) * quantity
                        if not result.get("executed"):
                            self.db.log("WARNING", "stops",
                                        f"{trigger} for {symbol} but sell failed: "
                                        f"{result.get('reason', '')}")
                            continue

                self.db.close_trade(t["id"], current_price,
                                    round(pnl_usd, 2), round(pnl_pct, 2))
                self.risk.close_position(symbol, t.get("asset_type", "crypto"), pnl_usd)
                self._persist_risk_counters()
                self.db.log("INFO", "stops",
                            f"[{'PAPER' if is_paper else 'LIVE'}] {trigger} triggered for "
                            f"{symbol}: P&L {pnl_usd:.2f} ({pnl_pct:+.1f}%)")
                logger.info(f"[STOPS] {trigger} {symbol} @ {current_price:.2f}, "
                            f"P&L: {pnl_usd:.2f} ({pnl_pct:+.1f}%)")

        except Exception as e:
            logger.error(f"Position monitor failed: {e}")

    async def _refresh_balances(self):
        """Lightweight balance refresh for live dashboard (every second)."""
        try:
            breakdown = await self.coinbase.get_portfolio_breakdown()
            coinbase_value = breakdown["total"]
            bot_state["coinbase_portfolio"] = breakdown
            ibkr_value = bot_state.get("real_balances", {}).get("ibkr_usd", 0)
            current_total = ibkr_value + coinbase_value
            bot_state["real_balances"] = {
                "ibkr_usd": ibkr_value,
                "coinbase_usd": coinbase_value,
                "total_usd": current_total,
            }

            # Midnight baseline reset (CET)
            today = datetime.now(CET).strftime("%Y-%m-%d")
            if bot_state.get("baseline_date") != today:
                bot_state["baseline_value"] = current_total
                bot_state["baseline_date"] = today
                self.db.kv_set("baseline_value", str(round(current_total, 2)))
                self.db.kv_set("baseline_date", today)
                self.risk.reset_daily()
                self._persist_risk_counters()
                logger.info(f"Midnight reset — new baseline: {current_total:.2f}")

            # Compute P&L attribution
            self._update_attribution(current_total)

        except Exception as e:
            logger.debug(f"Balance refresh failed: {e}")

    def _persist_risk_counters(self):
        """Save daily risk counters to DB so they survive restarts."""
        self.db.kv_set("daily_deployed", str(round(self.risk.daily_deployed, 2)))
        self.db.kv_set("daily_pnl", str(round(self.risk.daily_pnl, 2)))
        self.db.kv_set("trades_today", str(self.risk.trades_today))

    def _update_attribution(self, current_total: float):
        """Compute P&L attribution: realized (closed) vs unrealized (open).

        Total change is always derived from actual portfolio value vs baseline,
        so it's immune to price-lookup mismatches in individual positions.
        """
        baseline = bot_state.get("baseline_value", current_total)
        total_change = current_total - baseline

        # Realized P&L from risk manager (accumulated on each SELL)
        realized = self.risk.daily_pnl

        # Unrealized = total change minus realized (residual)
        unrealized = total_change - realized

        bot_state["attribution"] = {
            "baseline_value": round(baseline, 2),
            "current_value": round(current_total, 2),
            "total_change": round(total_change, 2),
            "bot_realized_pnl": round(realized, 2),
            "bot_unrealized_pnl": round(unrealized, 2),
            "bot_total_pnl": round(total_change, 2),
        }

        # Persist daily stats to DB — throttled to once per minute to avoid
        # contending with other queries on every 1s refresh cycle
        now_ts = time.time()
        if now_ts - getattr(self, "_last_daily_stats_save", 0) >= 60:
            self._last_daily_stats_save = now_ts
            today = datetime.now(CET).strftime("%Y-%m-%d")
            stats = self.db.get_daily_stats()
            wins = round(stats["win_rate"] * stats["trades_today"] / 100) if stats["trades_today"] else 0
            self.db.save_daily_stats(
                date=today,
                total_pnl=round(total_change, 2),
                trades_count=stats["trades_today"],
                wins=wins,
                win_rate=stats["win_rate"],
                portfolio_value=round(current_total, 2),
            )

        # Fix 7: pass TOTAL daily P&L (realized + unrealized) to the circuit breaker
        # so losses already booked today count toward the daily loss limit.
        # Note: risk_manager.check_circuit_breaker internally adds daily_pnl
        # (realized) to this arg, so we pass only the unrealized component to
        # avoid double-counting. The semantic fix is: always call with the
        # actual unrealized slice, which this already does correctly.
        self.risk.check_circuit_breaker(unrealized_pnl=unrealized)

        # Store budget status for dashboard
        bot_state["budget"] = self.risk.get_budget_status(unrealized_pnl=unrealized)

    async def snapshot_portfolio(self):
        """Take a portfolio snapshot with real broker balances."""
        coinbase_value = 0.0
        ibkr_value = 0.0
        holdings = []

        # Fetch real Coinbase balance
        if self.coinbase:
            try:
                breakdown = await self.coinbase.get_portfolio_breakdown()
                coinbase_value = breakdown["total_usd"]
                holdings = breakdown.get("holdings", [])
                bot_state["coinbase_portfolio"] = breakdown
            except Exception as e:
                logger.warning(f"Coinbase balance fetch failed: {e}")
                # Use cached value
                cached = bot_state.get("coinbase_portfolio", {})
                coinbase_value = cached.get("total_usd", 0)

        # Fetch real IBKR balance
        if self.ibkr and self.ibkr.connected:
            try:
                ibkr_value = await self.ibkr.get_portfolio_value()
            except Exception:
                pass

        total = coinbase_value + ibkr_value
        bot_state["real_balances"] = {
            "ibkr_usd": ibkr_value,
            "coinbase_usd": coinbase_value,
            "total_usd": total,
        }

        # Use total P&L (realized + unrealized) from attribution
        attribution = bot_state.get("attribution", {})
        daily_pnl = attribution.get("bot_total_pnl", self.risk.daily_pnl)
        baseline = attribution.get("baseline_value", total)
        daily_pnl_pct = (daily_pnl / baseline * 100) if baseline else 0

        self.db.log_snapshot(
            total_value_usd=total,
            ibkr_value_usd=ibkr_value,
            coinbase_value_usd=coinbase_value,
            cash_usd=0,
            daily_pnl_usd=round(daily_pnl, 2),
            daily_pnl_pct=round(daily_pnl_pct, 2),
            open_positions=holdings,
        )

    async def run_stock_cycle(self):
        """Analyze all stock symbols."""
        if not settings.IBKR_ENABLED or not self._is_nyse_open():
            return
        for symbol in settings.WATCHLIST_STOCKS:
            if not self._running or not bot_state["running"]:
                break
            await self.analyze_symbol(symbol, "ibkr", "stock")

    async def run_crypto_cycle(self):
        """Analyze all crypto symbols."""
        if not settings.COINBASE_ENABLED:
            return
        for symbol in settings.WATCHLIST_CRYPTO:
            if not self._running or not bot_state["running"]:
                break
            await self.analyze_symbol(symbol, "coinbase", "crypto")

    async def run(self):
        """Main bot loop."""
        logger.info(f"Starting TradingBot — broker={settings.BROKER_MODE} paper={settings.PAPER_MODE}")
        self.db.log("INFO", "main", f"Bot started. Mode: {settings.BROKER_MODE}, Paper: {settings.PAPER_MODE}")

        await self.init_brokers()

        # Expose executors and risk manager for dashboard
        bot_state["paper_executor"] = self.paper
        bot_state["risk_manager"] = self.risk
        bot_state["coinbase_executor"] = self.coinbase_executor
        bot_state["coinbase_client"] = self.coinbase

        logger.info(f"Daily budget: {self.risk.daily_budget:.2f} {settings.DEFAULT_CURRENCY}, "
                    f"loss limit: {self.risk.daily_loss_limit:.2f} ({settings.MAX_DAILY_LOSS_PCT}%)")

        # Restore or set baseline portfolio value for P&L attribution
        today = datetime.now(CET).strftime("%Y-%m-%d")
        saved_date = self.db.kv_get("baseline_date")
        saved_baseline = self.db.kv_get("baseline_value")
        current_value = bot_state.get("real_balances", {}).get("total_usd", 0)

        if saved_date == today and saved_baseline is not None:
            baseline = float(saved_baseline)
            logger.info(f"Restored today's baseline from DB: {baseline:.2f} {settings.DEFAULT_CURRENCY}")
        else:
            baseline = current_value
            self.db.kv_set("baseline_value", str(round(baseline, 2)))
            self.db.kv_set("baseline_date", today)
            logger.info(f"New baseline portfolio value: {baseline:.2f} {settings.DEFAULT_CURRENCY}")

        bot_state["baseline_value"] = baseline
        bot_state["baseline_date"] = today

        # Restore daily risk counters from DB (survive restarts)
        if saved_date == today:
            saved_deployed = self.db.kv_get("daily_deployed")
            saved_pnl = self.db.kv_get("daily_pnl")
            saved_trades = self.db.kv_get("trades_today")
            if saved_deployed is not None:
                self.risk.daily_deployed = float(saved_deployed)
            if saved_pnl is not None:
                self.risk.daily_pnl = float(saved_pnl)
            if saved_trades is not None:
                self.risk.trades_today = int(saved_trades)
            logger.info(f"Restored daily counters: deployed={self.risk.daily_deployed:.2f}, "
                        f"pnl={self.risk.daily_pnl:.2f}, trades={self.risk.trades_today}")

        # Initialize feeds
        if settings.FEEDS_ENABLED:
            self.feed_manager.init()
            bot_state["feed_manager"] = self.feed_manager
            asyncio.create_task(self.feed_manager.run_background_loop(
                interval=settings.FEEDS_REFRESH_INTERVAL
            ))
            logger.info("Feed system started")

        last_stock = 0.0
        last_crypto = 0.0
        last_snapshot = 0.0
        last_balance = 0.0
        last_stops = 0.0

        while self._running:
            now = time.time()
            trading_active = bot_state["running"]

            # Stock cycle (only when trading is active)
            if trading_active and now - last_stock >= settings.ANALYSIS_INTERVAL_STOCKS:
                last_stock = now
                await self.run_stock_cycle()

            # Crypto cycle (only when trading is active)
            if trading_active and now - last_crypto >= settings.ANALYSIS_INTERVAL_CRYPTO:
                last_crypto = now
                await self.run_crypto_cycle()

            # Refresh crypto balance every second for live dashboard
            # (always runs, even when trading is paused)
            if self.coinbase and now - last_balance >= 1:
                last_balance = now
                await self._refresh_balances()

            # Monitor open positions for stop-loss / take-profit (every 10s)
            if now - last_stops >= 10:
                last_stops = now
                await self._monitor_open_positions()

            # Portfolio snapshot to DB every 10 min
            if now - last_snapshot >= 600:
                last_snapshot = now
                await self.snapshot_portfolio()

            await asyncio.sleep(1)


def start_dashboard():
    """Run the FastAPI dashboard in a separate thread."""
    uvicorn.run(
        dashboard_app,
        host=settings.DASHBOARD_HOST,
        port=settings.DASHBOARD_PORT,
        log_level="warning",
    )


async def main():
    bot = TradingBot()

    # Start dashboard in background thread
    dash_thread = threading.Thread(target=start_dashboard, daemon=True)
    dash_thread.start()
    logger.info(f"Dashboard running at http://{settings.DASHBOARD_HOST}:{settings.DASHBOARD_PORT}")

    # Graceful shutdown
    def shutdown(sig, frame):
        logger.info("Shutting down...")
        bot._running = False
        bot_state["running"] = False
        bot.feed_manager.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    await bot.run()
    logger.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())
