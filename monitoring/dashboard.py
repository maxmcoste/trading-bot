"""FastAPI dashboard with REST API and HTML pages."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import settings
from monitoring.logger import DBLogger

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(title="TradingBot Dashboard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
db = DBLogger()

# Shared state — set by main.py
bot_state = {
    "running": True,
    "started_at": time.time(),
    "latest_signals": [],
}


# ── HTML Pages ───────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def page_dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {
        "refresh_interval": settings.DASHBOARD_REFRESH_INTERVAL,
    })


@app.get("/portfolio", response_class=HTMLResponse)
async def page_portfolio(request: Request):
    return templates.TemplateResponse(request, "portfolio.html")


@app.get("/trades", response_class=HTMLResponse)
async def page_trades(request: Request):
    return templates.TemplateResponse(request, "trades.html")


@app.get("/trades/{trade_id}", response_class=HTMLResponse)
async def page_trade_detail(request: Request, trade_id: int):
    trade = db.get_trade(trade_id)
    if not trade:
        return HTMLResponse("<h1>Trade not found</h1>", status_code=404)
    return templates.TemplateResponse(request, "trade_detail.html", {
        "trade_id": trade_id,
    })


@app.get("/strategies", response_class=HTMLResponse)
async def page_strategies(request: Request):
    return templates.TemplateResponse(request, "strategies.html")


@app.get("/settings", response_class=HTMLResponse)
async def page_settings(request: Request):
    return templates.TemplateResponse(request, "settings.html")


# ── API: Status ──────────────────────────────────────────────

@app.get("/api/status")
async def api_status():
    stats = db.get_daily_stats()

    # Use real balances from broker connections
    real = bot_state.get("real_balances", {})
    pv = real.get("total_usd", 0)

    # Fallback to DB snapshot if no real balance yet
    if pv <= 0:
        snapshots = db.get_snapshots(days=1)
        pv = snapshots[0]["total_value_usd"] if snapshots else 0

    return {
        "running": bot_state["running"],
        "paper_mode": settings.PAPER_MODE,
        "ibkr_enabled": settings.IBKR_ENABLED,
        "coinbase_enabled": settings.COINBASE_ENABLED,
        "broker_mode": settings.BROKER_MODE,
        "currency": settings.DEFAULT_CURRENCY,
        "currency_symbol": {"EUR": "\u20ac", "USD": "$", "GBP": "\u00a3"}.get(settings.DEFAULT_CURRENCY, settings.DEFAULT_CURRENCY),
        "uptime_seconds": int(time.time() - bot_state["started_at"]),
        "stats": {
            "portfolio_value": pv,
            "daily_pnl": stats["daily_pnl"],
            "daily_pnl_pct": round(stats["daily_pnl"] / max(pv, 1) * 100, 2) if pv > 0 else 0,
            "trades_today": stats["trades_today"],
            "holds_today": stats["holds_today"],
            "win_rate": stats["win_rate"],
        },
        "trading_budget": settings.TRADING_BUDGET,
        "budget": bot_state.get("budget", {}),
        "real_balances": real,
        "latest_signals": bot_state.get("latest_signals", []),
        "attribution": bot_state.get("attribution", {}),
        "coinbase_portfolio": {
            "holdings": [
                {"currency": h["currency"], "price": h.get("price", 0)}
                for h in bot_state.get("coinbase_portfolio", {}).get("holdings", [])
                if h.get("price")
            ],
        },
    }


@app.get("/api/health")
async def api_health():
    return {"status": "ok", "running": bot_state["running"]}


# ── API: Portfolio ───────────────────────────────────────────

@app.get("/api/portfolio")
async def api_portfolio():
    # Prefer live data from bot_state
    real = bot_state.get("real_balances", {})
    cb_portfolio = bot_state.get("coinbase_portfolio", {})

    total = real.get("total_usd", 0)
    ibkr = real.get("ibkr_usd", 0)
    cb = real.get("coinbase_usd", 0)

    # Fallback to DB snapshot if no live data
    if total <= 0:
        snapshots = db.get_snapshots(days=1)
        latest = snapshots[0] if snapshots else {}
        total = latest.get("total_value_usd", 0)
        ibkr = latest.get("ibkr_value_usd", 0)
        cb = latest.get("coinbase_value_usd", 0)

    # Coinbase holdings (for allocation chart)
    holdings = cb_portfolio.get("holdings", [])

    # Bot open positions (from paper executor via bot_state)
    paper_positions = []
    paper = bot_state.get("paper_executor")
    if paper:
        for sym, pos in paper.portfolio.positions.items():
            # Look up current price from coinbase holdings
            current_price = 0.0
            for h in holdings:
                pair = f"{h['currency']}-{settings.DEFAULT_CURRENCY}"
                if pair == sym and h.get("price"):
                    current_price = h["price"]
                    break
            pnl_usd = (current_price - pos.entry_price) * pos.quantity if current_price else 0
            pnl_pct = ((current_price - pos.entry_price) / pos.entry_price * 100) if current_price and pos.entry_price else 0
            paper_positions.append({
                "symbol": sym,
                "broker": pos.broker,
                "asset_type": pos.asset_type,
                "entry_price": pos.entry_price,
                "current_price": current_price,
                "quantity": pos.quantity,
                "position_usd": pos.position_usd,
                "pnl_usd": round(pnl_usd, 2),
                "pnl_pct": round(pnl_pct, 2),
                "stop_loss_pct": pos.stop_loss_pct,
                "take_profit_pct": pos.take_profit_pct,
                "opened_at": pos.opened_at,
            })

    return {
        "summary": {
            "total_value": total,
            "ibkr_value": ibkr,
            "coinbase_value": cb,
            "ibkr_pct": round(ibkr / max(total, 1) * 100, 1),
            "coinbase_pct": round(cb / max(total, 1) * 100, 1),
            "cash": cb_portfolio.get("cash", cb_portfolio.get("cash_usd", 0)),
        },
        "currency": settings.DEFAULT_CURRENCY,
        "currency_symbol": {"EUR": "\u20ac", "USD": "$", "GBP": "\u00a3"}.get(settings.DEFAULT_CURRENCY, settings.DEFAULT_CURRENCY),
        "holdings": holdings,
        "positions": paper_positions,
    }


@app.get("/api/portfolio/pnl")
async def api_portfolio_pnl(days: int = 30):
    snapshots = db.get_snapshots(days=days)
    return [{"timestamp": s["timestamp"], "value": s["total_value_usd"],
             "pnl": s.get("daily_pnl_usd", 0)} for s in reversed(snapshots)]


@app.get("/api/portfolio/intraday")
async def api_portfolio_intraday():
    rows = db.get_today_snapshots()
    # Aggregate to 5-minute buckets (keep last value per bucket) to smooth noisy data
    buckets: dict[str, float] = {}
    for r in rows:
        ts = r["timestamp"]  # "YYYY-MM-DD HH:MM:SS"
        hm = ts[11:16]       # "HH:MM"
        h, m = hm.split(":")
        bucket = f"{h}:{int(m) // 5 * 5:02d}"
        buckets[bucket] = r["daily_pnl_usd"] or 0
    return [{"time": k, "pnl": v} for k, v in sorted(buckets.items())]


@app.get("/api/daily-stats")
async def api_daily_stats(days: int = 30):
    return db.get_daily_stats_history(days=days)


# ── API: Trades ──────────────────────────────────────────────

@app.get("/api/trades/recent")
async def api_trades_recent(limit: int = 10):
    return db.get_trades(limit=limit, _exclude_hold=True)


@app.get("/api/trades")
async def api_trades(page: int = 1, limit: int = 20,
                     broker: str = "", action: str = "",
                     symbol: str = "", paper_mode: str = "",
                     open_only: str = ""):
    filters = {}
    if broker:
        filters["broker"] = broker
    if action:
        filters["action"] = action
    else:
        # Exclude HOLD by default (only show BUY/SELL)
        filters["_exclude_hold"] = True
    if symbol:
        filters["symbol"] = symbol.upper()
    if paper_mode != "":
        filters["paper_mode"] = int(paper_mode)
    offset = (page - 1) * limit
    trades = db.get_trades(limit=limit, offset=offset, **filters)
    if open_only == "1":
        trades = [t for t in trades if not t.get("closed_at") and t.get("executed")]
    elif open_only == "0":
        trades = [t for t in trades if t.get("closed_at")]

    # Enrich open BUY trades with unrealized P&L from current prices
    cb_portfolio = bot_state.get("coinbase_portfolio", {})
    price_map = {}
    for h in cb_portfolio.get("holdings", []):
        if h.get("price") and h.get("currency"):
            price_map[h["currency"]] = h["price"]

    fiat_set = {"EUR", "USD", "GBP", "USDC", "USDT", "DAI"}
    for t in trades:
        if t["action"] == "BUY" and not t.get("closed_at") and t.get("price") and t.get("quantity"):
            sym_parts = t["symbol"].split("-") if "-" in t["symbol"] else [t["symbol"]]
            base = sym_parts[0]
            quote = sym_parts[1] if len(sym_parts) > 1 else settings.DEFAULT_CURRENCY
            if quote not in fiat_set and quote in price_map and price_map[quote] > 0:
                current = price_map.get(base, 0) / price_map[quote] if price_map.get(base) else None
            else:
                current = price_map.get(base)
            if current and current > 0:
                t["unrealized_pnl"] = round((current - t["price"]) * t["quantity"], 2)
                t["unrealized_pnl_pct"] = round((current - t["price"]) / t["price"] * 100, 2)
                t["current_price"] = round(current, 6)

    return trades


@app.get("/api/trades/{trade_id}")
async def api_trade_detail(trade_id: int):
    trade = db.get_trade(trade_id)
    if not trade:
        return JSONResponse({"error": "Not found"}, status_code=404)
    # Parse JSON fields for frontend consumption
    for field in ("strategy_signals", "warnings", "technical_snapshot"):
        val = trade.get(field)
        if val and isinstance(val, str):
            try:
                trade[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
    # Attach related strategy signals from the signals table
    trade["related_signals"] = db.get_signals_near_trade(
        trade["symbol"], trade["timestamp"], window_minutes=5
    )
    return trade


@app.patch("/api/trades/{trade_id}")
async def api_trade_update(trade_id: int, request: Request):
    """Update stop_loss_pct and/or take_profit_pct on a single trade."""
    trade = db.get_trade(trade_id)
    if not trade:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if trade.get("closed_at"):
        return JSONResponse({"error": "Trade is already closed"}, status_code=400)
    data = await request.json()
    updates = {}
    for field in ("stop_loss_pct", "take_profit_pct"):
        if field in data and isinstance(data[field], (int, float)) and data[field] > 0:
            updates[field] = float(data[field])
    if not updates:
        return JSONResponse({"error": "No valid fields to update"}, status_code=400)
    set_clause = ", ".join(f"{k}=?" for k in updates)
    with db._conn() as conn:
        conn.execute(f"UPDATE trades SET {set_clause} WHERE id=?",
                     list(updates.values()) + [trade_id])
    db.log("INFO", "dashboard",
           f"Trade #{trade_id} ({trade['symbol']}) updated: "
           + ", ".join(f"{k}={v}%" for k, v in updates.items()))
    return {**updates, "status": "ok"}


@app.post("/api/trades/{trade_id}/close")
async def api_trade_close(trade_id: int):
    """Force-close an open BUY trade at current market price."""
    trade = db.get_trade(trade_id)
    if not trade:
        return JSONResponse({"error": "Trade not found"}, status_code=404)
    if trade.get("action") != "BUY":
        return JSONResponse({"error": "Can only close BUY trades"}, status_code=400)
    if trade.get("closed_at"):
        return JSONResponse({"error": "Trade already closed"}, status_code=400)

    symbol = trade["symbol"]
    entry_price = trade.get("price", 0)
    quantity = trade.get("quantity", 0)
    is_paper = trade.get("paper_mode", 1)

    # Get current price
    current_price = None
    cb_portfolio = bot_state.get("coinbase_portfolio", {})
    parts = symbol.split("-") if "-" in symbol else [symbol]
    base_currency = parts[0]
    quote_currency = parts[1] if len(parts) > 1 else settings.DEFAULT_CURRENCY
    fiat_set = {"EUR", "USD", "GBP", "USDC", "USDT", "DAI"}
    is_cross = quote_currency not in fiat_set

    if is_cross:
        # Cross pair: compute price from EUR prices
        base_eur = quote_eur = 0
        for h in cb_portfolio.get("holdings", []):
            if h.get("currency") == base_currency and h.get("price"):
                base_eur = h["price"]
            if h.get("currency") == quote_currency and h.get("price"):
                quote_eur = h["price"]
        if base_eur and quote_eur:
            current_price = base_eur / quote_eur
    else:
        for h in cb_portfolio.get("holdings", []):
            if h.get("currency") == base_currency and h.get("price"):
                current_price = h["price"]
                break

    # Fallback: fetch from Coinbase API
    if not current_price:
        coinbase_client = bot_state.get("coinbase_client")
        if coinbase_client:
            if is_cross:
                current_price = await coinbase_client.get_cross_rate_price(symbol)
            else:
                current_price = await coinbase_client.get_current_price(symbol)

    if not current_price or current_price <= 0:
        return JSONResponse({"error": f"Cannot get current price for {symbol}"},
                            status_code=500)

    # Calculate P&L
    pnl_usd = (current_price - entry_price) * quantity if entry_price > 0 else 0
    pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

    # Execute sell for live trades
    sell_failed = False
    if not is_paper:
        executor = bot_state.get("coinbase_executor")
        if executor and quantity > 0:
            sell_result = await executor.execute(
                symbol, "SELL", quantity, current_price,
            )
            if not sell_result.get("executed"):
                sell_failed = True
                reason = sell_result.get("reason", "Unknown")
                position_value = quantity * current_price if current_price else 0
                # For tiny positions (< €1), close in DB anyway
                if position_value < 1.0:
                    db.log("WARNING", "dashboard",
                           f"Sell failed for #{trade_id} {symbol} (value {position_value:.4f}), "
                           f"closing in DB only: {reason}")
                else:
                    return JSONResponse(
                        {"error": f"Sell execution failed: {reason}"},
                        status_code=500)

    # Close paper position if exists
    paper = bot_state.get("paper_executor")
    if is_paper and paper and symbol in paper.portfolio.positions:
        paper.execute_trade(
            symbol, trade.get("broker", "coinbase"),
            trade.get("asset_type", "crypto"), "SELL",
            current_price, quantity, 0, 0,
        )

    # Update DB
    db.close_trade(trade_id, current_price, round(pnl_usd, 2), round(pnl_pct, 2))

    # Update risk manager
    risk = bot_state.get("risk_manager")
    if risk:
        risk.close_position(symbol, trade.get("asset_type", "crypto"), pnl_usd)

    db.log("INFO", "dashboard",
           f"Force-closed trade #{trade_id} {symbol} @ {current_price:.2f}, "
           f"P&L: {pnl_usd:.2f} ({pnl_pct:+.1f}%)")

    return {
        "status": "closed", "trade_id": trade_id,
        "close_price": round(current_price, 2),
        "pnl_usd": round(pnl_usd, 2), "pnl_pct": round(pnl_pct, 2),
        "db_only": sell_failed,
    }


@app.get("/api/trades/export")
async def api_trades_export():
    trades = db.get_trades(limit=10000)
    output = io.StringIO()
    if trades:
        writer = csv.DictWriter(output, fieldnames=trades[0].keys())
        writer.writeheader()
        writer.writerows(trades)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trades.csv"},
    )


# ── API: Strategies ──────────────────────────────────────────

STRATEGY_INFO = {
    "mean_reversion": {"display_name": "Mean Reversion", "description": "Identifies assets that have deviated from their historical mean and bets on reversion.", "docs_url": "https://www.investopedia.com/terms/m/meanreversion.asp", "config_key": "STRATEGY_MEAN_REVERSION_ENABLED"},
    "sentiment": {"display_name": "Sentiment Trading", "description": "Trades based on news sentiment analysis and market mood indicators.", "docs_url": "https://www.investopedia.com/terms/m/marketsentiment.asp", "config_key": "STRATEGY_SENTIMENT_ENABLED"},
    "multi_signal": {"display_name": "Multi-Signal Fusion", "description": "Master strategy that aggregates all other signals with configurable weights.", "docs_url": "https://www.investopedia.com/terms/t/technicalanalysis.asp", "config_key": "STRATEGY_MULTI_SIGNAL_ENABLED"},
    "btc_correlation": {"display_name": "BTC Correlation Filter", "description": "Filters crypto signals based on Bitcoin's trend and market dominance.", "docs_url": "https://academy.binance.com/en/articles/bitcoin-dominance-and-its-impact-on-altcoins", "config_key": "STRATEGY_BTC_CORRELATION_ENABLED"},
    "fear_greed": {"display_name": "Fear & Greed Contrarian", "description": "Contrarian strategy: buy during fear, sell during greed.", "docs_url": "https://alternative.me/crypto/fear-and-greed-index/", "config_key": "STRATEGY_FEAR_GREED_ENABLED"},
    "session_momentum": {"display_name": "Session Momentum", "description": "Trades crypto momentum during specific global trading sessions.", "docs_url": "https://www.investopedia.com/terms/m/momentum_investing.asp", "config_key": "STRATEGY_SESSION_MOMENTUM_ENABLED"},
}


@app.get("/api/strategies")
async def api_strategies():
    stats = db.get_strategy_stats(days=30)
    result = []
    for name, info in STRATEGY_INFO.items():
        enabled = getattr(settings, info["config_key"], False)
        recent = db.get_signals(strategy=name, limit=5)
        s = stats.get(name, {})
        result.append({
            "name": name,
            "enabled": enabled,
            "recent_signals": recent,
            "signal_count": s.get("signal_count", 0),
            "accuracy": s.get("accuracy", 0),
            "avg_contribution": s.get("avg_contribution", 0.0),
            **info,
        })
    return {
        "strategies": result,
        "weights": {
            "technical": settings.STRATEGY_WEIGHT_TECHNICAL,
            "sentiment": settings.STRATEGY_WEIGHT_SENTIMENT,
            "macro": settings.STRATEGY_WEIGHT_MACRO,
        },
    }


@app.post("/api/strategies/{name}/toggle")
async def api_strategy_toggle(name: str):
    info = STRATEGY_INFO.get(name)
    if not info:
        return JSONResponse({"error": "Unknown strategy"}, status_code=404)
    key = info["config_key"]
    current = getattr(settings, key, False)
    setattr(settings, key, not current)
    settings.save_to_env()
    db.log("INFO", "dashboard", f"Strategy {name} {'enabled' if not current else 'disabled'}")
    return {"name": name, "enabled": not current}


@app.post("/api/strategies/weights")
async def api_strategy_weights(request: Request):
    data = await request.json()
    settings.STRATEGY_WEIGHT_TECHNICAL = data.get("technical", 0.4)
    settings.STRATEGY_WEIGHT_SENTIMENT = data.get("sentiment", 0.3)
    settings.STRATEGY_WEIGHT_MACRO = data.get("macro", 0.3)
    settings.save_to_env()
    db.log("INFO", "dashboard", f"Strategy weights updated: {data}")
    return {"status": "ok"}


# ── API: Settings ────────────────────────────────────────────

@app.get("/api/settings")
async def api_settings():
    return {k: v for k, v in settings.__dict__.items()
            if not k.startswith("_") and k != "model_config"
            and "SECRET" not in k and "API_KEY" not in k.upper()
            or k in ("COINBASE_API_KEY",)}


@app.post("/api/settings")
async def api_update_settings(request: Request):
    data = await request.json()
    for key, value in data.items():
        if hasattr(settings, key) and "SECRET" not in key:
            setattr(settings, key, value)
    settings.save_to_env()
    db.log("INFO", "dashboard", "Settings updated and saved to .env")
    return {"status": "ok"}


@app.post("/api/trades/apply-sl-tp")
async def api_apply_sl_tp(request: Request):
    """Apply current SL/TP settings to all open positions."""
    data = await request.json()
    asset_type = data.get("asset_type", "crypto")
    if asset_type == "stock":
        sl = settings.STOP_LOSS_DEFAULT_PCT
        tp = settings.TAKE_PROFIT_DEFAULT_PCT
    else:
        sl = settings.CRYPTO_STOP_LOSS_DEFAULT_PCT
        tp = settings.CRYPTO_TAKE_PROFIT_DEFAULT_PCT
    count = db.update_open_trades_sl_tp(asset_type, sl, tp)
    db.log("INFO", "dashboard",
           f"Applied SL={sl}%/TP={tp}% to {count} open {asset_type} positions")
    return {"status": "ok", "updated": count, "stop_loss_pct": sl, "take_profit_pct": tp}


# ── HTML: Logs ───────────────────────────────────────────────

@app.get("/logs", response_class=HTMLResponse)
async def page_logs(request: Request):
    return templates.TemplateResponse(request, "logs.html")


# ── API: Logs ────────────────────────────────────────────────

@app.get("/api/logs")
async def api_logs(limit: int = 50, page: int = 1,
                   level: str = "", component: str = ""):
    filters = {}
    if level:
        filters["level"] = level
    if component:
        filters["component"] = component
    offset = (page - 1) * limit
    return db.get_logs(limit=limit, offset=offset, **filters)


@app.post("/api/logs/purge")
async def api_logs_purge(request: Request):
    data = await request.json()
    days = int(data.get("days", 30))
    if days < 0:
        return {"error": "days must be >= 0"}
    deleted = db.purge_logs(days)

    # If purging everything (days=0), also truncate the on-disk log files
    files_truncated = []
    if days == 0:
        from pathlib import Path
        log_dir = Path(__file__).resolve().parent.parent / "logs"
        for pattern in ("trading_bot.log*", "claude_audit.log*"):
            for f in log_dir.glob(pattern):
                try:
                    # Truncate in place so RotatingFileHandler keeps writing to it
                    with open(f, "w"):
                        pass
                    files_truncated.append(f.name)
                except Exception as e:
                    logger.warning(f"Could not truncate {f}: {e}")

    db.log("WARNING", "dashboard",
           f"Purged {deleted} DB logs older than {days} days, "
           f"truncated {len(files_truncated)} log files")
    return {"deleted": deleted, "days": days, "files_truncated": files_truncated}


@app.get("/api/logs/stream")
async def api_logs_stream():
    async def event_gen():
        last_id = 0
        while True:
            logs = db.get_logs(limit=5)
            for log in reversed(logs):
                if log["id"] > last_id:
                    last_id = log["id"]
                    yield f"data: {json.dumps(log)}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


# ── API: Bot Control ─────────────────────────────────────────

@app.post("/api/bot/stop")
async def api_bot_stop():
    bot_state["running"] = False
    db.log("WARNING", "dashboard", "Bot stopped via dashboard")
    return {"status": "stopped"}


@app.post("/api/bot/start")
async def api_bot_start():
    bot_state["running"] = True
    db.log("INFO", "dashboard", "Bot started via dashboard")
    return {"status": "started"}


@app.post("/api/bot/analyze/{symbol}")
async def api_bot_analyze(symbol: str):
    db.log("INFO", "dashboard", f"Manual analysis requested for {symbol}")
    return {"status": "queued", "symbol": symbol}


# ── API: Fear & Greed ────────────────────────────────────────

# ── HTML: Feeds ──────────────────────────────────────────────

@app.get("/feeds", response_class=HTMLResponse)
async def page_feeds(request: Request):
    return templates.TemplateResponse(request, "feeds.html")


# ── API: Feeds ───────────────────────────────────────────────

@app.get("/api/feeds")
async def api_feeds(source: str = "", tag: str = "", limit: int = 50):
    """Return feed items — optionally filtered by source or tag."""
    fm = bot_state.get("feed_manager")
    if fm:
        items = fm.get_cached_items(tag=tag or None, source=source or None, limit=limit)
        return [i.to_dict() for i in items]
    # Fallback to DB
    return db.get_feed_items(source=source or None, tag=tag or None, limit=limit)


@app.get("/api/feeds/sources")
async def api_feed_sources():
    """Return all registered feed sources with metadata."""
    fm = bot_state.get("feed_manager")
    if fm:
        return fm.get_sources()
    return []


@app.post("/api/feeds/sources/{name}/toggle")
async def api_feed_source_toggle(name: str):
    """Enable/disable a feed source."""
    fm = bot_state.get("feed_manager")
    if not fm:
        return JSONResponse({"error": "Feed manager not initialized"}, status_code=503)
    feed = fm.get_source(name)
    if not feed:
        return JSONResponse({"error": "Unknown source"}, status_code=404)
    feed.source.enabled = not feed.source.enabled
    db.log("INFO", "feeds", f"Feed {name} {'enabled' if feed.source.enabled else 'disabled'}")
    return {"name": name, "enabled": feed.source.enabled}


@app.post("/api/feeds/refresh")
async def api_feeds_refresh():
    """Force refresh all feed sources."""
    fm = bot_state.get("feed_manager")
    if not fm:
        return JSONResponse({"error": "Feed manager not initialized"}, status_code=503)
    items = await fm.fetch_all(force=True)
    return {"status": "ok", "items_count": len(items)}


@app.post("/api/feeds/custom-rss")
async def api_add_custom_rss(request: Request):
    """Add a custom RSS feed."""
    fm = bot_state.get("feed_manager")
    if not fm:
        return JSONResponse({"error": "Feed manager not initialized"}, status_code=503)
    data = await request.json()
    name = data.get("name", "").strip().replace(" ", "_").lower()
    display_name = data.get("display_name", name)
    url = data.get("url", "").strip()
    refresh = data.get("refresh_interval", 300)
    if not name or not url:
        return JSONResponse({"error": "name and url required"}, status_code=400)
    feed_name = fm.add_custom_rss(name, display_name, url, refresh)
    db.log("INFO", "feeds", f"Custom RSS added: {feed_name} -> {url}")
    return {"status": "ok", "feed_name": feed_name}


@app.delete("/api/feeds/custom-rss/{name}")
async def api_remove_custom_rss(name: str):
    """Remove a custom RSS feed."""
    fm = bot_state.get("feed_manager")
    if not fm:
        return JSONResponse({"error": "Feed manager not initialized"}, status_code=503)
    removed = fm.remove_custom_rss(name)
    if not removed:
        return JSONResponse({"error": "Feed not found or not custom"}, status_code=404)
    db.log("INFO", "feeds", f"Custom RSS removed: {name}")
    return {"status": "ok"}


@app.get("/api/feeds/stream")
async def api_feeds_stream():
    """SSE stream for live feed updates."""
    async def event_gen():
        last_count = 0
        while True:
            fm = bot_state.get("feed_manager")
            if fm:
                items = fm.get_cached_items(limit=5)
                current_count = len(fm._all_items)
                if current_count != last_count and items:
                    last_count = current_count
                    for item in items[:3]:
                        yield f"data: {json.dumps(item.to_dict())}\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/api/fear-greed")
async def api_fear_greed():
    try:
        from data.fear_greed_client import FearGreedClient
        client = FearGreedClient()
        data = await client.fetch()
        if data:
            return {"value": data.value, "classification": data.classification,
                    "history": data.history}
    except Exception:
        pass
    return {"value": None, "classification": "N/A", "history": []}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.DASHBOARD_HOST, port=settings.DASHBOARD_PORT)
