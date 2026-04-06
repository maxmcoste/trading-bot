# TradingBot — AI-Powered Algorithmic Micro-Investment Platform

An autonomous Python trading bot powered by **Claude** (Anthropic) that trades
**stocks/ETFs via Interactive Brokers** and **crypto via Coinbase Advanced
Trade**, with a real-time dark-mode web dashboard, pluggable strategy stack,
multi-source alt-data feeds, a full risk engine, and persistent SQLite state.

Runs headless on a home Mac (launchd / `ctl.sh`) and can operate in **paper
mode**, **live mode**, or hybrid (paper crypto + live stocks, etc.).

---

## Table of Contents

1. [Highlights](#highlights)
2. [Architecture at a Glance](#architecture-at-a-glance)
3. [Project Structure](#project-structure)
4. [Quick Start](#quick-start)
5. [Configuration](#configuration)
6. [Trading Strategies](#trading-strategies)
7. [Alt-Data Feeds](#alt-data-feeds)
8. [Risk Management](#risk-management)
9. [AI Decision Layer](#ai-decision-layer)
10. [Web Dashboard](#web-dashboard)
11. [REST API](#rest-api)
12. [Persistence & State](#persistence--state)
13. [Paper vs Live Mode](#paper-vs-live-mode)
14. [Operating the Bot](#operating-the-bot)
15. [Logs & Debugging](#logs--debugging)
16. [Testing](#testing)
17. [Safety Notes](#safety-notes)

---

## Highlights

- **AI-first decisions** — every trade goes through a Claude agent that receives
  price history, technical indicators, strategy votes, news sentiment, Fear &
  Greed, portfolio state and risk caps, and returns `BUY/SELL/HOLD` with a
  confidence score and reasoning.
- **Dual broker** — Coinbase Advanced Trade for crypto, Interactive Brokers for
  equities, in one event loop.
- **Strategy ensemble** — 6 pluggable strategies (mean reversion, momentum,
  sentiment, BTC correlation, Fear & Greed contrarian, multi-signal) that vote
  independently.
- **Alt-data feeds** — Reddit, CryptoPanic, CoinGecko, Glassnode, Binance
  funding, Google Trends, generic RSS, with cached manager.
- **Hard risk engine** — daily deployment budget, daily loss circuit breaker,
  max open positions, per-trade stop-loss/take-profit, BTC wallet reserve for
  cross-pair protection.
- **Live dashboard** — FastAPI + Jinja2 + Alpine.js + Tailwind + Chart.js:
  portfolio, P&L history, budget gauges, trade history with drill-down, live
  log tail, settings editor, strategy toggles, feed manager, logs purger.
- **Paper mode** — full simulation against real-time Coinbase/IBKR prices
  without touching real funds.
- **Persistence** — SQLite (`trading_bot.db`) for trades, signals, daily stats,
  logs, and key-value state so counters survive restarts.

---

## Architecture at a Glance

```
                ┌─────────────────────────────────────────────────┐
                │                    main.py                       │
                │         TradingBot event loop + orchestration    │
                └───────────┬───────────────────────┬──────────────┘
                            │                       │
              ┌─────────────┴─────────┐   ┌─────────┴──────────────┐
              │   Market Data Layer    │   │    Decision Layer       │
              │  data/coinbase_client  │   │  strategies/*           │
              │  data/ibkr_client      │   │  feeds/feed_manager     │
              │  data/indicators       │   │  ai/claude_agent        │
              │  data/news_client      │   │  ai/prompt_builder      │
              │  data/fear_greed_client│   │                         │
              └─────────────┬──────────┘   └──────────┬──────────────┘
                            │                         │
                            └───────────┬─────────────┘
                                        ▼
                         ┌──────────────────────────────┐
                         │       Risk Layer              │
                         │  risk/risk_manager            │
                         │  risk/position_sizer          │
                         └──────────────┬────────────────┘
                                        ▼
                         ┌──────────────────────────────┐
                         │      Execution Layer          │
                         │  execution/coinbase_executor  │
                         │  execution/ibkr_executor      │
                         │  execution/paper_mode         │
                         └──────────────┬────────────────┘
                                        ▼
                         ┌──────────────────────────────┐
                         │     Monitoring & Storage      │
                         │  monitoring/logger (SQLite)   │
                         │  monitoring/dashboard (FastAPI│
                         │  monitoring/notifier          │
                         └──────────────────────────────┘
```

---

## Project Structure

```
trading_bot/
├── main.py                    # Event loop, orchestration, analyze/execute cycles
├── config.py                  # Pydantic Settings (loads from .env, persistable)
├── ctl.sh                     # start/stop/status wrapper
├── setup_mac.sh               # One-shot venv + dependencies installer
├── requirements.txt
├── trading_bot.db             # SQLite (trades, signals, logs, daily_stats, kv)
│
├── ai/
│   ├── claude_agent.py        # Anthropic API client wrapper
│   ├── prompt_builder.py      # Builds the decision prompt for Claude
│   └── system_prompt.py       # System instructions / persona
│
├── data/
│   ├── coinbase_client.py     # Coinbase Advanced Trade: portfolio, candles, products
│   ├── ibkr_client.py         # Interactive Brokers via ib_insync
│   ├── indicators.py          # TA: RSI, MACD, Bollinger, Z-score, ATR, EMA, volume
│   ├── news_client.py         # NewsAPI + substring-matched sentiment
│   └── fear_greed_client.py   # Alternative.me Fear & Greed index
│
├── feeds/                     # Alt-data feeds with a unified base + manager
│   ├── base_feed.py
│   ├── feed_manager.py
│   ├── feed_registry.py
│   ├── models.py
│   ├── coingecko_feed.py
│   ├── cryptopanic_feed.py
│   ├── glassnode_feed.py
│   ├── binance_funding_feed.py
│   ├── google_trends_feed.py
│   ├── reddit_feed.py
│   └── rss_feed.py
│
├── strategies/                # Each strategy returns a StrategySignal (dir + strength + reason)
│   ├── base_strategy.py
│   ├── mean_reversion.py
│   ├── session_momentum.py
│   ├── sentiment_trading.py
│   ├── btc_correlation_filter.py
│   ├── fear_greed_contrarian.py
│   └── multi_signal.py
│
├── execution/
│   ├── coinbase_executor.py   # Market BUY (quote_size) / SELL (base_size)
│   ├── ibkr_executor.py       # IBKR market/limit orders
│   └── paper_mode.py          # Virtual portfolio for simulation
│
├── risk/
│   ├── risk_manager.py        # Daily budget, loss limit, position caps
│   └── position_sizer.py      # Kelly-ish sizing with confidence weighting
│
├── monitoring/
│   ├── logger.py              # SQLite logger + kv store + daily_stats + log purge
│   ├── dashboard.py           # FastAPI app, all routes and JSON APIs
│   └── notifier.py            # Telegram / email notifications
│
├── templates/                 # Jinja2 + Alpine.js pages
│   ├── base.html              # Shared shell: navbar, theme, scripts
│   ├── dashboard.html
│   ├── portfolio.html
│   ├── trades.html
│   ├── trade_detail.html      # Per-trade drill-down with technical snapshot
│   ├── strategies.html
│   ├── feeds.html
│   ├── settings.html
│   ├── logs.html
│   └── components/            # Navbar, live log widget, reused pieces
│
├── static/                    # favicon etc.
├── logs/                      # trading_bot.log, claude_audit.log, launchd stdio
└── tests/
    └── test_news_client.py    # Standalone sentiment test
```

---

## Quick Start

### 1. Clone & setup

```bash
git clone <repo>
cd trading_bot
./setup_mac.sh            # creates venv, installs requirements.txt
source venv/bin/activate
```

### 2. Configure `.env`

Copy the template and fill in the values you plan to use:

```bash
cp .env.example .env      # if present, otherwise create it
```

Minimum to start in **paper mode** trading crypto:

```ini
PAPER_MODE=true
TRADING_BUDGET=1000
DEFAULT_CURRENCY=EUR

# Claude (required — the bot won't take trades without it)
ANTHROPIC_API_KEY=sk-ant-...

# Coinbase (read-only is fine for paper)
COINBASE_API_KEY=organizations/.../apiKeys/...
COINBASE_API_SECRET=-----BEGIN EC PRIVATE KEY-----...

# Optional
NEWSAPI_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

### 3. Run

```bash
./ctl.sh start            # launches main.py as background process
./ctl.sh status
./ctl.sh stop
```

Or run in foreground during development:

```bash
python main.py
```

### 4. Open the dashboard

Navigate to **http://127.0.0.1:8080** — you'll land on the dashboard with
portfolio, budget, P&L chart, and live logs.

---

## Configuration

All config lives in `config.py` (Pydantic Settings) and is loaded from `.env`.
Many fields are also editable at runtime via the **Settings** page, which
persists back to `.env`.

### Key environment variables

| Variable | Purpose | Default |
|---|---|---|
| `PAPER_MODE` | Simulate trades, no real orders | `true` |
| `DEFAULT_CURRENCY` | `EUR` or `USD` — affects watchlist quote | `EUR` |
| `TRADING_BUDGET` | Total capital the bot is allowed to use | `1000` |
| `MAX_DAILY_LOSS_PCT` | Daily circuit breaker (% of budget) | `3.0` |
| `MAX_DAILY_DEPLOY_PCT` | Max % of budget deployed per day | `30.0` |
| `CRYPTO_MAX_OPEN_POSITIONS` | Hard cap on open crypto trades | `5` |
| `CRYPTO_STOP_LOSS_DEFAULT_PCT` | Default SL per crypto trade | `3.0` |
| `CRYPTO_TAKE_PROFIT_DEFAULT_PCT` | Default TP per crypto trade | `6.0` |
| `BTC_RESERVE_EUR` | Min BTC balance (in EUR) the bot will never spend | `2000` |
| `ANALYSIS_INTERVAL_CRYPTO` | Seconds between crypto analysis cycles | `300` |
| `ANALYSIS_INTERVAL_STOCKS` | Seconds between stock analysis cycles | `900` |
| `WATCHLIST_CRYPTO` | Comma-separated product IDs (e.g. `BTC-EUR,ETH-EUR`) | auto |
| `WATCHLIST_STOCKS` | Comma-separated symbols (e.g. `AAPL,MSFT`) | `""` |
| `ANTHROPIC_API_KEY` | Claude API key | required |
| `COINBASE_API_KEY` / `COINBASE_API_SECRET` | Coinbase Advanced Trade | required for crypto |
| `IBKR_HOST` / `IBKR_PORT` / `IBKR_CLIENT_ID` | IB Gateway / TWS connection | required for stocks |
| `NEWSAPI_KEY` | NewsAPI.org sentiment (optional) | — |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Notifications | optional |

### Watchlist auto-population

On startup the bot auto-populates `WATCHLIST_CRYPTO` from your Coinbase
holdings, filtered by:

- Must be a tradable product on your account's region
- Must be quoted in `DEFAULT_CURRENCY` (or a crypto cross-pair)
- Stablecoins and dust positions (< €1) are excluded

Manually curated watchlists in `.env` are still honored and pruned of stale
entries.

---

## Trading Strategies

Each file in `strategies/` subclasses `BaseStrategy` and returns a
`StrategySignal(direction, strength, reason)`. The AI layer receives **all**
signals and weighs them into a final decision.

| Strategy | Logic summary |
|---|---|
| **mean_reversion** | Buys when Z-score < -2 (oversold), sells when Z-score > +2 |
| **session_momentum** | Uses intraday RSI + volume to ride breakouts |
| **sentiment_trading** | Pulls news sentiment score; BUY on strongly positive, SELL on negative |
| **btc_correlation_filter** | Dampens alts when BTC is in a rapid sell-off |
| **fear_greed_contrarian** | Contrarian: BUY at Extreme Fear, SELL at Extreme Greed |
| **multi_signal** | Meta-strategy combining RSI, MACD, Bollinger into a composite vote |

Strategies can be enabled/disabled per asset class from **Settings → Strategies**.

---

## Alt-Data Feeds

The `feeds/` package provides a pluggable alt-data system with a unified
`BaseFeed` interface, a `FeedRegistry`, and a `FeedManager` that caches and
refreshes feeds on configurable intervals.

Current feeds:

- **Reddit** — subreddit mention rate / sentiment
- **CryptoPanic** — aggregated crypto headlines
- **CoinGecko** — trending coins, price data
- **Glassnode** — on-chain metrics (requires API key)
- **Binance funding** — perp funding rates
- **Google Trends** — search interest
- **RSS** — generic RSS with sentiment scoring

The **Feeds** dashboard page shows last-refresh, item counts, and raw payloads
per feed.

---

## Risk Management

Three layers, in order:

1. **Position sizing** (`risk/position_sizer.py`) — scales order size by
   Claude's confidence, capped by `MAX_POSITION_SIZE_PCT` of the budget.
2. **Risk manager** (`risk/risk_manager.py`) — enforces:
   - Daily deployment budget (`MAX_DAILY_DEPLOY_PCT × TRADING_BUDGET`)
   - Daily loss circuit breaker (`MAX_DAILY_LOSS_PCT × TRADING_BUDGET`) — when
     hit, pauses new BUYs for the rest of the day
   - Max open positions per asset class
   - Blocks BUYs that would breach per-trade caps
3. **Runtime checks in `main.py`**:
   - **BTC reserve**: cross-pair BUYs subtract `BTC_RESERVE_EUR / btc_price`
     from usable BTC before sizing
   - **Stop-loss / take-profit monitor**: every cycle, checks all open trades
     against live prices; triggers SELL on breach
   - **INSUFFICIENT_FUND retry**: if a SELL fails for lack of base asset, the
     bot re-reads the actual wallet balance and retries with the real amount
     (dust-guarded at 50%)

All risk counters (`daily_deployed`, `daily_pnl`, `trades_today`) are persisted
to `bot_kv` so they survive restarts; reset at local midnight.

---

## AI Decision Layer

**`ai/claude_agent.py`** — thin wrapper over the Anthropic SDK.

**`ai/prompt_builder.py`** — assembles the decision prompt from:

- Symbol + current price + OHLCV window
- Technical indicators: RSI, MACD (value/signal/histogram), Bollinger bands,
  Z-score, volume ratio, ATR, EMA-20/50
- All enabled strategy signals (name, direction, strength, reason)
- News sentiment score + top headlines
- Fear & Greed value + classification
- Session (Asia/London/NY)
- Open positions in this symbol + portfolio state
- Risk budget remaining
- Feed highlights from the feed manager

**`ai/system_prompt.py`** — the persona: a cautious micro-investor bot with
hard rules (no leverage, respect risk caps, prefer HOLD when uncertain, always
return JSON with `action`, `confidence`, `reasoning`, optional `warnings`).

Claude's full response (prompt + output) is logged to `logs/claude_audit.log`
for post-hoc review.

---

## Web Dashboard

FastAPI app served by `monitoring/dashboard.py` on port **8080**.

| Page | What it shows |
|---|---|
| **/** Dashboard | Portfolio value, daily budget (with deployed + P&L bars, paused badge), Profit Today (realized/unrealized), trades today, win rate today, daily P&L bar chart (30 days), latest signals, recent trades, Fear & Greed widget, live log tail |
| **/portfolio** | Holdings breakdown (crypto + stocks + fiat), allocation pie, open positions with live P&L |
| **/trades** | Paginated trade history with filters by symbol/action/date; click a trade ID for full detail |
| **/trades/{id}** | Trade drill-down: decision context, all strategy votes, Claude's reasoning, technical snapshot, risk params, outcome |
| **/strategies** | Toggle strategies per asset class, view recent signals, per-strategy hit rates |
| **/feeds** | Live alt-data feed status + latest items |
| **/settings** | Edit all config (budget, SL/TP, intervals, Claude model, BTC reserve, watchlists); saves back to `.env` |
| **/logs** | Filterable log viewer (level/component/search), auto-refresh, purge button with configurable days |

**Navbar** has a global **Pause Trading / Start Trading** toggle that flips
`bot_state["running"]` — observability (balances, prices, stop-loss monitor)
continues to run even when trading is paused.

---

## REST API

A partial list — all JSON endpoints under `/api/`:

| Endpoint | Purpose |
|---|---|
| `GET /api/status` | Stats + attribution + budget + latest signals + currency symbol |
| `GET /api/trades/recent?limit=N` | Recent trades |
| `GET /api/trades/{id}` | Full trade detail with parsed JSON fields + related signals |
| `GET /api/daily-stats?days=N` | Daily P&L history for the chart |
| `GET /api/fear-greed` | Current Fear & Greed |
| `GET /api/logs?level=&component=&page=&limit=` | Paginated logs |
| `POST /api/logs/purge` `{days}` | Delete DB logs (and truncate log files if `days=0`) |
| `POST /api/bot/start` | Resume trading |
| `POST /api/bot/stop` | Pause trading |
| `POST /api/settings` | Persist settings to `.env` |
| `GET /api/feeds` | Feed manager state |

---

## Persistence & State

SQLite file: **`trading_bot.db`**

| Table | Contents |
|---|---|
| `trades` | Every BUY/SELL with entry/exit price, SL/TP, strategy signals JSON, warnings, Claude reasoning, technical snapshot JSON, paper flag, closed_at, P&L |
| `strategy_signals` | Per-strategy signals with direction/strength/reason, linked to analysis cycles |
| `bot_logs` | Application logs (level, component, message, timestamp) |
| `daily_stats` | Daily P&L, win count, loss count, trades count — throttled to 1 write/min |
| `bot_kv` | Key-value store for `baseline_value`, `daily_deployed`, `daily_pnl`, `trades_today`, `saved_date` → survives restarts |

Safe `ALTER TABLE` migrations run at startup to add new columns without
losing data.

---

## Paper vs Live Mode

Paper mode is controlled by `PAPER_MODE=true` in `.env`.

- **Paper**: orders go through `execution/paper_mode.py` (virtual portfolio,
  real-time prices from Coinbase/IBKR, full SL/TP simulation).
- **Live**: orders go through `coinbase_executor` / `ibkr_executor`. SELL uses
  `base_size` (amount of base asset to sell), BUY uses `quote_size` (how much
  quote currency to spend).
- **Hybrid**: since paper vs live is per-trade (stored on the trade row), you
  can mark individual trades live while the default is paper.

The dashboard clearly tags paper positions; all trade detail pages show
a PAPER / LIVE badge.

---

## Operating the Bot

**`ctl.sh`** — convenience wrapper:

```bash
./ctl.sh start         # background launch
./ctl.sh stop
./ctl.sh restart
./ctl.sh status
./ctl.sh logs          # tail -f trading_bot.log
```

**launchd** — for auto-start on boot, add a plist under
`~/Library/LaunchAgents/com.user.tradingbot.plist` pointing to `ctl.sh start`.
stdio is captured in `logs/launchd_stdout.log` and `launchd_stderr.log`.

---

## Logs & Debugging

Two main log files in `logs/`:

- **`trading_bot.log`** — `RotatingFileHandler` (8 MB × 10 backups) with
  everything the bot does.
- **`claude_audit.log`** — full prompt + response for every Claude call, for
  post-hoc review of AI decisions.

Controls:

- `LOG_LEVEL=DEBUG|INFO|WARNING` in `.env`
- Dashboard `/logs` page: filter, search, auto-refresh, purge with configurable
  day threshold. Setting days=0 **also truncates the log files on disk**.

Common troubleshooting:

| Symptom | Likely cause |
|---|---|
| `No OHLCV data for XXX-YYY` | Pair not tradable in your region; it will be pruned next startup |
| `take_profit for XXX but sell failed: INSUFFICIENT_FUND` | Tracked quantity exceeds actual wallet balance — the bot now auto-retries with real balance |
| `NEWSAPI_KEY not configured` | Benign — set the key or ignore (warning fires once then suppresses) |
| Daily Budget stuck paused | Daily loss limit breached; resets at local midnight or by restarting after manual reset |

---

## Testing

```bash
# Paper-mode smoke test of the full loop
python test_paper.py

# Standalone news client test (keyword unit tests + live API fetch)
python tests/test_news_client.py
```

Add new tests under `tests/`.

---

## Safety Notes

- **Start in paper mode**. Always. Let it run a full week before flipping
  `PAPER_MODE=false`.
- **Set conservative caps**: `MAX_DAILY_LOSS_PCT=3`, `MAX_DAILY_DEPLOY_PCT=30`,
  `CRYPTO_MAX_OPEN_POSITIONS=5` is a sane starting baseline.
- **Use the BTC reserve** if you also hold BTC long-term: the bot will never
  spend BTC below that EUR equivalent.
- **Anthropic API costs money** — every analysis cycle is a Claude call.
  Increase `ANALYSIS_INTERVAL_*` if costs worry you.
- **No guarantees**. This is a personal research project. Don't trade money
  you can't afford to lose. Review `logs/claude_audit.log` regularly to
  sanity-check what the AI is actually deciding.

---

## License

Personal / non-commercial use. Not financial advice.
