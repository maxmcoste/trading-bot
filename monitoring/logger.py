"""SQLite database logger for trades, signals, portfolio, and bot logs."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "trading_bot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    broker TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL,
    strategy TEXT,
    dominant_strategy TEXT,
    price REAL,
    quantity REAL,
    position_size_usd REAL,
    stop_loss_pct REAL,
    take_profit_pct REAL,
    stop_loss_price REAL,
    take_profit_price REAL,
    reasoning TEXT,
    strategy_signals TEXT,
    warnings TEXT,
    fear_greed_value INTEGER,
    session TEXT,
    paper_mode INTEGER DEFAULT 1,
    executed INTEGER DEFAULT 0,
    closed_at TEXT,
    close_price REAL,
    pnl_usd REAL,
    pnl_pct REAL,
    technical_snapshot TEXT
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    total_value_usd REAL,
    ibkr_value_usd REAL,
    coinbase_value_usd REAL,
    cash_usd REAL,
    daily_pnl_usd REAL,
    daily_pnl_pct REAL,
    open_positions TEXT
);

CREATE TABLE IF NOT EXISTS strategy_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    signal TEXT NOT NULL,
    strength REAL,
    reason TEXT,
    enabled INTEGER,
    price_at_signal REAL
);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    component TEXT,
    error_type TEXT,
    message TEXT,
    resolved INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bot_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    level TEXT NOT NULL,
    component TEXT,
    message TEXT
);

CREATE TABLE IF NOT EXISTS feed_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT,
    url TEXT,
    tags TEXT,
    priority TEXT DEFAULT 'medium',
    sentiment_score REAL,
    symbol TEXT,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS feed_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL UNIQUE,
    enabled INTEGER DEFAULT 1,
    refresh_interval INTEGER DEFAULT 300,
    custom_url TEXT,
    display_name TEXT
);

CREATE TABLE IF NOT EXISTS bot_kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date TEXT PRIMARY KEY,
    total_pnl REAL DEFAULT 0,
    trades_count INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    win_rate REAL DEFAULT 0,
    portfolio_value REAL DEFAULT 0
);
"""


class DBLogger:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or DB_PATH)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)
            # Migration: add technical_snapshot column if missing
            try:
                conn.execute("ALTER TABLE trades ADD COLUMN technical_snapshot TEXT")
            except sqlite3.OperationalError:
                pass

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ts(self) -> str:
        return datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d %H:%M:%S")

    def purge_logs(self, days: int) -> int:
        """Delete bot_logs older than `days` days. Returns rows deleted."""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM bot_logs WHERE timestamp < datetime('now', ?)",
                (f"-{int(days)} days",),
            )
            return cur.rowcount

    # ── Key-Value Store ──────────────────────────────────────
    def kv_get(self, key: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM bot_kv WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    def kv_set(self, key: str, value: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO bot_kv (key, value, updated_at) VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')",
                (key, value),
            )

    # ── Trades ───────────────────────────────────────────────

    def log_trade(self, **kwargs) -> int:
        kwargs.setdefault("timestamp", self._ts())
        if "strategy_signals" in kwargs and isinstance(kwargs["strategy_signals"], dict):
            kwargs["strategy_signals"] = json.dumps(kwargs["strategy_signals"])
        if "warnings" in kwargs and isinstance(kwargs["warnings"], list):
            kwargs["warnings"] = json.dumps(kwargs["warnings"])
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        with self._conn() as conn:
            cur = conn.execute(
                f"INSERT INTO trades ({cols}) VALUES ({placeholders})",
                list(kwargs.values()),
            )
            return cur.lastrowid

    def update_open_trades_sl_tp(self, asset_type: str,
                                  stop_loss_pct: float, take_profit_pct: float) -> int:
        """Update stop_loss_pct and take_profit_pct on all open (unclosed) BUY trades."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE trades SET stop_loss_pct=?, take_profit_pct=? "
                "WHERE action='BUY' AND closed_at IS NULL AND executed=1 AND asset_type=?",
                (stop_loss_pct, take_profit_pct, asset_type),
            )
            return cur.rowcount

    def close_trade(self, trade_id: int, close_price: float, pnl_usd: float, pnl_pct: float):
        with self._conn() as conn:
            conn.execute(
                "UPDATE trades SET closed_at=?, close_price=?, pnl_usd=?, pnl_pct=? WHERE id=?",
                (self._ts(), close_price, pnl_usd, pnl_pct, trade_id),
            )

    def get_trades(self, limit: int = 50, offset: int = 0, **filters) -> list[dict]:
        where_clauses = []
        params = []
        exclude_hold = filters.pop("_exclude_hold", False)
        if exclude_hold:
            where_clauses.append("action != 'HOLD'")
        for k, v in filters.items():
            if v is not None:
                where_clauses.append(f"{k} = ?")
                params.append(v)
        where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM trades {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
            return [dict(r) for r in rows]

    def get_trade(self, trade_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
            return dict(row) if row else None

    def get_signals_near_trade(self, symbol: str, timestamp: str,
                               window_minutes: int = 5) -> list[dict]:
        """Get strategy_signals logged within +/- window_minutes of a timestamp."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM strategy_signals "
                "WHERE symbol = ? "
                "AND timestamp BETWEEN datetime(?, '-' || ? || ' minutes') "
                "AND datetime(?, '+' || ? || ' minutes') "
                "ORDER BY id ASC",
                (symbol, timestamp, window_minutes, timestamp, window_minutes),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Portfolio ────────────────────────────────────────────

    def log_snapshot(self, **kwargs):
        kwargs.setdefault("timestamp", self._ts())
        if "open_positions" in kwargs and isinstance(kwargs["open_positions"], (dict, list)):
            kwargs["open_positions"] = json.dumps(kwargs["open_positions"])
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO portfolio_snapshots ({cols}) VALUES ({placeholders})",
                list(kwargs.values()),
            )

    def get_snapshots(self, days: int = 30) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT ?",
                (days * 144,),  # ~144 snapshots/day at 10min intervals
            ).fetchall()
            return [dict(r) for r in rows]

    def get_today_snapshots(self) -> list[dict]:
        today = time.strftime("%Y-%m-%d")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT timestamp, daily_pnl_usd FROM portfolio_snapshots "
                "WHERE timestamp LIKE ? ORDER BY id ASC",
                (f"{today}%",),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Strategy Signals ─────────────────────────────────────

    def log_signal(self, symbol: str, strategy_name: str, signal: str,
                   strength: float, reason: str, enabled: bool,
                   price: float | None = None):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO strategy_signals (timestamp, symbol, strategy_name, signal, strength, reason, enabled, price_at_signal) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self._ts(), symbol, strategy_name, signal, strength, reason, int(enabled), price),
            )

    def get_signals(self, symbol: str | None = None, strategy: str | None = None,
                    limit: int = 50) -> list[dict]:
        where_clauses = []
        params = []
        if symbol:
            where_clauses.append("symbol = ?")
            params.append(symbol)
        if strategy:
            where_clauses.append("strategy_name = ?")
            params.append(strategy)
        where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM strategy_signals {where} ORDER BY id DESC LIMIT ?",
                params + [limit],
            ).fetchall()
            return [dict(r) for r in rows]

    def get_strategy_stats(self, days: int = 30) -> dict[str, dict]:
        """Compute signal count, signal accuracy, and avg contribution per strategy.

        Signal accuracy: did the signal correctly predict price direction?
        - bullish signal + price went up = correct
        - bearish signal + price went down = correct
        - neutral signals are excluded from accuracy calculation

        Uses price_at_signal from strategy_signals table. For signals without
        stored prices, falls back to trade prices logged at the same time.
        """
        cutoff = (datetime.now(ZoneInfo("Europe/Berlin"))
                  - __import__("datetime").timedelta(days=days)).strftime("%Y-%m-%d")
        stats: dict[str, dict] = {}

        with self._conn() as conn:
            # Signal counts
            rows = conn.execute(
                "SELECT strategy_name, COUNT(*) as cnt "
                "FROM strategy_signals WHERE timestamp >= ? "
                "GROUP BY strategy_name",
                (cutoff,),
            ).fetchall()
            for r in rows:
                stats[r["strategy_name"]] = {
                    "signal_count": r["cnt"], "accuracy": 0, "avg_contribution": 0.0,
                }

            # Fetch all directional signals (bullish/bearish) with prices
            signals = conn.execute(
                "SELECT id, timestamp, symbol, strategy_name, signal, strength, "
                "price_at_signal FROM strategy_signals "
                "WHERE timestamp >= ? AND signal IN ('bullish', 'bearish') "
                "ORDER BY id ASC",
                (cutoff,),
            ).fetchall()
            signals = [dict(s) for s in signals]

            # Build a price lookup: for each (symbol, timestamp) get price
            # from trades table (for signals without price_at_signal)
            trade_prices = {}
            trows = conn.execute(
                "SELECT symbol, timestamp, price FROM trades "
                "WHERE timestamp >= ? AND price > 0 "
                "ORDER BY id ASC",
                (cutoff,),
            ).fetchall()
            for t in trows:
                # Key by (symbol, timestamp prefix) for approximate matching
                trade_prices[(t["symbol"], t["timestamp"][:16])] = t["price"]

            # Contribution strengths from trades
            trades = conn.execute(
                "SELECT strategy_signals FROM trades "
                "WHERE timestamp >= ? AND action <> 'HOLD' AND executed = 1",
                (cutoff,),
            ).fetchall()

        # Build price series per symbol from signals that have prices
        # symbol -> list of (id, price) ordered by id
        symbol_prices: dict[str, list[tuple[int, float]]] = {}
        for s in signals:
            price = s["price_at_signal"]
            if not price:
                # Try matching from trade prices
                price = trade_prices.get(
                    (s["symbol"], s["timestamp"][:16]))
            if price and price > 0:
                symbol_prices.setdefault(s["symbol"], []).append(
                    (s["id"], price))

        # Evaluate each directional signal: compare price at signal vs next
        # price point for the same symbol
        strat_correct: dict[str, int] = {}
        strat_total: dict[str, int] = {}
        for s in signals:
            price = s["price_at_signal"]
            if not price:
                price = trade_prices.get(
                    (s["symbol"], s["timestamp"][:16]))
            if not price or price <= 0:
                continue

            # Find next price for this symbol after this signal
            prices_list = symbol_prices.get(s["symbol"], [])
            next_price = None
            for pid, p in prices_list:
                if pid > s["id"]:
                    next_price = p
                    break

            if next_price is None or next_price == price:
                continue

            sname = s["strategy_name"]
            strat_total[sname] = strat_total.get(sname, 0) + 1
            price_went_up = next_price > price
            if (s["signal"] == "bullish" and price_went_up) or \
               (s["signal"] == "bearish" and not price_went_up):
                strat_correct[sname] = strat_correct.get(sname, 0) + 1

        # Set accuracy
        for sname, total in strat_total.items():
            if sname not in stats:
                stats[sname] = {"signal_count": 0, "accuracy": 0, "avg_contribution": 0.0}
            correct = strat_correct.get(sname, 0)
            stats[sname]["accuracy"] = round(correct / total * 100) if total > 0 else 0

        # Avg contribution from trade strategy_signals JSON
        strat_strengths: dict[str, list[float]] = {}
        for t in trades:
            sigs_raw = t["strategy_signals"]
            if sigs_raw:
                try:
                    sigs = json.loads(sigs_raw)
                    for sname, sdata in sigs.items():
                        if isinstance(sdata, dict):
                            strat_strengths.setdefault(sname, []).append(
                                sdata.get("strength", 0))
                except (json.JSONDecodeError, AttributeError):
                    pass

        for sname, strengths in strat_strengths.items():
            if sname not in stats:
                stats[sname] = {"signal_count": 0, "accuracy": 0, "avg_contribution": 0.0}
            stats[sname]["avg_contribution"] = round(
                sum(strengths) / len(strengths) * 100, 1) if strengths else 0.0

        return stats

    # ── Errors ───────────────────────────────────────────────

    def log_error(self, component: str, error_type: str, message: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO errors (timestamp, component, error_type, message) VALUES (?, ?, ?, ?)",
                (self._ts(), component, error_type, message),
            )

    # ── Bot Logs ─────────────────────────────────────────────

    def log(self, level: str, component: str, message: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO bot_logs (timestamp, level, component, message) VALUES (?, ?, ?, ?)",
                (self._ts(), level, component, message),
            )

    def get_logs(self, limit: int = 50, offset: int = 0, **filters) -> list[dict]:
        where_clauses = []
        params = []
        for k, v in filters.items():
            if v is not None and v != "":
                where_clauses.append(f"{k} = ?")
                params.append(v)
        where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM bot_logs {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Feed Items ─────────────────────────────────────────────

    def log_feed_item(self, item) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO feed_items (timestamp, source, title, content, url, tags, "
                "priority, sentiment_score, symbol, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._ts(), item.source, item.title, item.content, item.url,
                    json.dumps(item.tags), item.priority, item.sentiment_score,
                    item.symbol, json.dumps(item.metadata),
                ),
            )
            return cur.lastrowid

    def get_feed_items(self, source: str | None = None, tag: str | None = None,
                       limit: int = 50) -> list[dict]:
        where_clauses = []
        params = []
        if source:
            where_clauses.append("source = ?")
            params.append(source)
        if tag:
            where_clauses.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM feed_items {where} ORDER BY id DESC LIMIT ?",
                params + [limit],
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Feed Configs ──────────────────────────────────────────

    def save_feed_config(self, source_name: str, enabled: bool = True,
                         refresh_interval: int = 300, custom_url: str = "",
                         display_name: str = ""):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO feed_configs "
                "(source_name, enabled, refresh_interval, custom_url, display_name) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_name, int(enabled), refresh_interval, custom_url, display_name),
            )

    def get_feed_configs(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM feed_configs").fetchall()
            return [dict(r) for r in rows]

    def delete_feed_config(self, source_name: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM feed_configs WHERE source_name = ?", (source_name,))

    # ── Stats ────────────────────────────────────────────────

    def get_daily_stats(self) -> dict:
        today = time.strftime("%Y-%m-%d")
        with self._conn() as conn:
            trades = conn.execute(
                "SELECT * FROM trades WHERE timestamp LIKE ? AND action != 'HOLD'",
                (f"{today}%",),
            ).fetchall()
            holds = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE timestamp LIKE ? AND action = 'HOLD'",
                (f"{today}%",),
            ).fetchone()[0]

            executed = [dict(t) for t in trades]
            total_pnl = sum(t.get("pnl_usd") or 0 for t in executed)
            wins = sum(1 for t in executed if (t.get("pnl_usd") or 0) > 0)
            total = len(executed)

            return {
                "trades_today": total,
                "holds_today": holds,
                "daily_pnl": round(total_pnl, 2),
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            }

    def save_daily_stats(self, date: str, total_pnl: float, trades_count: int,
                         wins: int, win_rate: float, portfolio_value: float):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO daily_stats (date, total_pnl, trades_count, wins, win_rate, portfolio_value) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(date) DO UPDATE SET "
                "total_pnl=excluded.total_pnl, trades_count=excluded.trades_count, "
                "wins=excluded.wins, win_rate=excluded.win_rate, portfolio_value=excluded.portfolio_value",
                (date, total_pnl, trades_count, wins, win_rate, portfolio_value),
            )

    def get_daily_stats_history(self, days: int = 30) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM daily_stats ORDER BY date DESC LIMIT ?", (days,)
            ).fetchall()
            return [dict(r) for r in rows]


if __name__ == "__main__":
    import tempfile

    db = DBLogger(db_path=tempfile.mktemp(suffix=".db"))

    trade_id = db.log_trade(
        symbol="AAPL", broker="ibkr", asset_type="stock", action="BUY",
        confidence=0.85, strategy="multi_signal", dominant_strategy="mean_reversion",
        price=150.0, quantity=10, position_size_usd=1500.0,
        stop_loss_pct=1.5, take_profit_pct=3.0,
        reasoning="Strong bullish signal", paper_mode=1, executed=1,
    )
    print(f"Logged trade #{trade_id}")

    db.log_signal("AAPL", "mean_reversion", "bullish", 0.8, "Z-score -2.3", True)
    db.log("INFO", "main", "Bot started")
    db.log_error("news_client", "APIError", "NewsAPI rate limited")

    trades = db.get_trades(limit=5)
    print(f"Trades: {len(trades)}")

    logs = db.get_logs(limit=5)
    print(f"Logs: {len(logs)}")

    stats = db.get_daily_stats()
    print(f"Daily stats: {stats}")
