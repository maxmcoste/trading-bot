# Prompt per Claude Code — Applicazione Agentiva di Trading v2

Costruisci un'applicazione Python completa per micro-investimenti algoritmici guidati da AI.
L'app gira su un Mac domestico sempre connesso, usa Interactive Brokers (IBKR) per azioni/ETF
e/o Coinbase Advanced Trade per crypto (configurabile), e utilizza Claude AI come motore
decisionale multi-segnale. Include una dashboard web professionale per monitoraggio e controllo.

---

## STRUTTURA DEL PROGETTO

```
trading_bot/
├── main.py
├── config.py
├── requirements.txt
├── .env.example
├── setup_mac.sh
│
├── data/
│   ├── ibkr_client.py
│   ├── coinbase_client.py
│   ├── news_client.py
│   ├── fear_greed_client.py
│   └── indicators.py
│
├── ai/
│   ├── prompt_builder.py
│   ├── claude_agent.py
│   └── system_prompt.py
│
├── risk/
│   ├── risk_manager.py
│   └── position_sizer.py
│
├── execution/
│   ├── ibkr_executor.py
│   ├── coinbase_executor.py
│   └── paper_mode.py
│
├── strategies/
│   ├── base_strategy.py
│   ├── mean_reversion.py
│   ├── sentiment_trading.py
│   ├── multi_signal.py
│   ├── btc_correlation_filter.py
│   ├── fear_greed_contrarian.py
│   └── session_momentum.py
│
├── monitoring/
│   ├── logger.py
│   ├── notifier.py
│   └── dashboard.py
│
└── templates/
    ├── base.html
    ├── dashboard.html
    ├── trades.html
    ├── strategies.html
    ├── portfolio.html
    ├── settings.html
    └── components/
        ├── navbar.html
        ├── trade_card.html
        ├── signal_gauge.html
        └── live_log.html
```

---

## PARTE 1 — CONFIGURAZIONE BROKER (NUOVO)

### config.py

Usa `pydantic-settings` per caricare tutta la configurazione da variabili d'ambiente.
Il sistema deve supportare tre modalità broker, configurabili tramite la variabile
`BROKER_MODE`:

```python
# ============================================================
# BROKER MODE — scegli quale broker abilitare
# ============================================================
# "ibkr"      → solo Interactive Brokers (azioni/ETF)
# "coinbase"  → solo Coinbase Advanced Trade (crypto)
# "both"      → entrambi attivi contemporaneamente
BROKER_MODE: str = "both"

# ============================================================
# WATCHLIST — attiva solo per il broker corrispondente
# ============================================================
WATCHLIST_STOCKS: list = ["AAPL", "MSFT", "NVDA", "SPY", "QQQ"]
WATCHLIST_CRYPTO: list = ["BTC-USD", "ETH-USD", "SOL-USD"]

# ============================================================
# INTERACTIVE BROKERS
# ============================================================
IBKR_ENABLED: bool = True          # override automatico da BROKER_MODE
IBKR_HOST: str = "127.0.0.1"
IBKR_PORT: int = 7497              # 7497 paper | 7496 live
IBKR_CLIENT_ID: int = 1

# ============================================================
# COINBASE ADVANCED TRADE
# ============================================================
COINBASE_ENABLED: bool = True      # override automatico da BROKER_MODE
COINBASE_API_KEY: str = ""
COINBASE_API_SECRET: str = ""

# ============================================================
# PAPER TRADING
# ============================================================
PAPER_MODE: bool = True            # True = nessun ordine reale su nessun broker

# ============================================================
# ANALISI — intervalli in secondi
# ============================================================
ANALYSIS_INTERVAL_STOCKS: int = 300   # ogni 5 min, solo ore NYSE
ANALYSIS_INTERVAL_CRYPTO: int = 120   # ogni 2 min, sempre

# ============================================================
# RISK MANAGEMENT — azioni
# ============================================================
CONFIDENCE_THRESHOLD: float = 0.68
MAX_POSITION_SIZE_PCT: float = 8.0
MAX_DAILY_LOSS_PCT: float = 3.0
MAX_OPEN_POSITIONS: int = 5
STOP_LOSS_DEFAULT_PCT: float = 1.5
TAKE_PROFIT_DEFAULT_PCT: float = 3.0

# ============================================================
# RISK MANAGEMENT — crypto (parametri separati, volatilità più alta)
# ============================================================
CRYPTO_CONFIDENCE_THRESHOLD: float = 0.75
CRYPTO_MAX_POSITION_SIZE_PCT: float = 4.0
CRYPTO_STOP_LOSS_DEFAULT_PCT: float = 3.0
CRYPTO_TAKE_PROFIT_DEFAULT_PCT: float = 6.0
CRYPTO_MAX_OPEN_POSITIONS: int = 3

# ============================================================
# STRATEGIE — ogni strategia è abilitabile/disabilitabile
# ============================================================
# Strategie comuni (azioni + crypto)
STRATEGY_MEAN_REVERSION_ENABLED: bool = True
STRATEGY_SENTIMENT_ENABLED: bool = True
STRATEGY_MULTI_SIGNAL_ENABLED: bool = True

# Strategie crypto-specifiche
STRATEGY_BTC_CORRELATION_ENABLED: bool = True
STRATEGY_FEAR_GREED_ENABLED: bool = True
STRATEGY_SESSION_MOMENTUM_ENABLED: bool = True

# Pesi relativi delle strategie nel multi-signal (devono sommare a 1.0)
STRATEGY_WEIGHT_TECHNICAL: float = 0.40
STRATEGY_WEIGHT_SENTIMENT: float = 0.30
STRATEGY_WEIGHT_MACRO: float = 0.30

# ============================================================
# API ESTERNE
# ============================================================
NEWSAPI_KEY: str = ""              # https://newsapi.org — piano gratuito sufficiente
ANTHROPIC_API_KEY: str = ""

# ============================================================
# DASHBOARD
# ============================================================
DASHBOARD_HOST: str = "127.0.0.1"
DASHBOARD_PORT: int = 8080
DASHBOARD_REFRESH_INTERVAL: int = 10   # secondi auto-refresh

# ============================================================
# NOTIFICHE
# ============================================================
NOTIFY_EMAIL: str = ""
NOTIFY_ON_TRADE: bool = True
NOTIFY_ON_ERROR: bool = True
NOTIFY_ON_DAILY_SUMMARY: bool = True
```

La logica di override da `BROKER_MODE`:
```python
def __init__(self, **data):
    super().__init__(**data)
    if self.BROKER_MODE == "ibkr":
        self.IBKR_ENABLED = True
        self.COINBASE_ENABLED = False
    elif self.BROKER_MODE == "coinbase":
        self.IBKR_ENABLED = False
        self.COINBASE_ENABLED = True
    elif self.BROKER_MODE == "both":
        self.IBKR_ENABLED = True
        self.COINBASE_ENABLED = True
```

---

## PARTE 2 — STRATEGIE CRYPTO COINBASE (NUOVO)

Ogni strategia è un modulo separato in `strategies/` e implementa `BaseStrategy`.

### strategies/base_strategy.py

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class StrategySignal:
    name: str           # nome della strategia
    signal: str         # "bullish" | "bearish" | "neutral"
    strength: float     # 0.0 - 1.0
    reason: str         # spiegazione human-readable
    enabled: bool       # se la strategia è attiva in config

class BaseStrategy(ABC):
    @abstractmethod
    def analyze(self, data: dict) -> StrategySignal:
        pass

    @property
    @abstractmethod
    def docs_url(self) -> str:
        """URL alla documentazione ufficiale della strategia"""
        pass

    @property
    @abstractmethod
    def config_key(self) -> str:
        """Nome della variabile config che abilita/disabilita questa strategia"""
        pass
```

---

### Strategia 1 — Mean Reversion

**File**: `strategies/mean_reversion.py`
**Config key**: `STRATEGY_MEAN_REVERSION_ENABLED`
**Docs**: https://www.investopedia.com/terms/m/meanreversion.asp

Logica:
- Calcola Z-score del prezzo rispetto alla media mobile 20 periodi
- Z-score < -2.0 → segnale bullish forte (prezzo molto sotto la media)
- Z-score < -1.0 → segnale bullish moderato
- Z-score > +2.0 → segnale bearish forte (prezzo molto sopra la media)
- Z-score > +1.0 → segnale bearish moderato
- RSI < 30 rafforza il segnale bullish, RSI > 70 rafforza il bearish
- Bollinger Band position < 0.1 (prezzo vicino alla banda inferiore) → conferma bullish

Il `strength` è proporzionale all'entità dello Z-score: abs(z_score) / 3.0, clippato a 1.0.

```python
class MeanReversionStrategy(BaseStrategy):
    docs_url = "https://www.investopedia.com/terms/m/meanreversion.asp"
    config_key = "STRATEGY_MEAN_REVERSION_ENABLED"
```

---

### Strategia 2 — Sentiment Trading

**File**: `strategies/sentiment_trading.py`
**Config key**: `STRATEGY_SENTIMENT_ENABLED`
**Docs**: https://www.investopedia.com/terms/m/marketsentiment.asp

Logica:
- Usa il `sentiment_score` da `news_client.py` (range -1.0 a +1.0)
- Score > +0.3 → segnale bullish
- Score < -0.3 → segnale bearish
- Score tra -0.3 e +0.3 → neutral
- Peso extra se le ultime 3 news hanno tutte lo stesso segno (consistenza)
- Per crypto: include anche il Fear & Greed Index nel calcolo del sentiment

```python
class SentimentStrategy(BaseStrategy):
    docs_url = "https://www.investopedia.com/terms/m/marketsentiment.asp"
    config_key = "STRATEGY_SENTIMENT_ENABLED"
```

---

### Strategia 3 — Multi-Signal Fusion

**File**: `strategies/multi_signal.py`
**Config key**: `STRATEGY_MULTI_SIGNAL_ENABLED`
**Docs**: https://www.investopedia.com/terms/t/technicalanalysis.asp

Logica:
- Aggrega i segnali di tutte le altre strategie abilitate usando i pesi in config
  (`STRATEGY_WEIGHT_TECHNICAL`, `STRATEGY_WEIGHT_SENTIMENT`, `STRATEGY_WEIGHT_MACRO`)
- Converte ogni segnale in un valore numerico: bullish=+1, neutral=0, bearish=-1
- Moltiplica per strength e per il peso configurato
- Score composito > +0.4 → BUY, < -0.4 → SELL, altrimenti HOLD
- È la strategia "master" — l'unica che produce la decisione finale per Claude

```python
class MultiSignalStrategy(BaseStrategy):
    docs_url = "https://www.investopedia.com/terms/t/technicalanalysis.asp"
    config_key = "STRATEGY_MULTI_SIGNAL_ENABLED"
```

---

### Strategia 4 — BTC Correlation Filter (solo crypto)

**File**: `strategies/btc_correlation_filter.py`
**Config key**: `STRATEGY_BTC_CORRELATION_ENABLED`
**Docs**: https://academy.binance.com/en/articles/bitcoin-dominance-and-its-impact-on-altcoins

Logica (si applica a TUTTI i simboli crypto, BTC incluso come self-check):
- Calcola variazione BTC nelle ultime 1h e 4h
- Se BTC_change_1h < -3% → forza segnale bearish su tutti i crypto (effetto contagio)
- Se BTC_change_4h < -5% → forza HOLD su tutti i crypto (mercato in panico)
- Se BTC è in trend positivo (EMA20 > EMA50) → segnali BUY permessi
- Se BTC è in trend negativo (EMA20 < EMA50) → solo SELL o HOLD permessi
- Per il simbolo BTC-USD stesso: questo filtro non si applica (evita circolarità)
- Calcola rolling correlation 24h tra il simbolo e BTC: se correlation > 0.8, usa
  il trend BTC come conferma aggiuntiva

```python
class BTCCorrelationStrategy(BaseStrategy):
    docs_url = "https://academy.binance.com/en/articles/bitcoin-dominance-and-its-impact-on-altcoins"
    config_key = "STRATEGY_BTC_CORRELATION_ENABLED"
```

---

### Strategia 5 — Fear & Greed Contrarian (solo crypto)

**File**: `strategies/fear_greed_contrarian.py`
**Config key**: `STRATEGY_FEAR_GREED_ENABLED`
**Docs**: https://alternative.me/crypto/fear-and-greed-index/

Fonte dati: API gratuita `https://api.alternative.me/fng/?limit=1`
**File dedicato**: `data/fear_greed_client.py` — fetch e cache del Fear & Greed Index
(aggiorna ogni ora — l'API pubblica un valore al giorno ma è stabile)

Logica contrarian (compra la paura, vendi l'euforia):
- Score 0-24 (Extreme Fear) → segnale bullish forte, strength=0.9
- Score 25-44 (Fear) → segnale bullish moderato, strength=0.6
- Score 45-55 (Neutral) → segnale neutral, strength=0.0
- Score 56-74 (Greed) → segnale bearish moderato, strength=0.6
- Score 75-100 (Extreme Greed) → segnale bearish forte, strength=0.9

Regola aggiuntiva: se il Fear & Greed è rimasto in Extreme Fear per 3+ giorni
consecutivi, abbassa la strength del segnale bullish a 0.5 (il mercato potrebbe
continuare a scendere — "catching a falling knife").

```python
class FearGreedStrategy(BaseStrategy):
    docs_url = "https://alternative.me/crypto/fear-and-greed-index/"
    config_key = "STRATEGY_FEAR_GREED_ENABLED"
```

---

### Strategia 6 — Session Momentum (solo crypto)

**File**: `strategies/session_momentum.py`
**Config key**: `STRATEGY_SESSION_MOMENTUM_ENABLED`
**Docs**: https://www.investopedia.com/terms/m/momentum_investing.asp

Logica basata sulle sessioni di trading crypto (orari CET):

```
Sessione Asia:      01:00 - 09:00 CET   (alta liquidità, spesso trend iniziali)
Sessione Europa:    09:00 - 14:00 CET   (transizione, volatilità media)
Sessione USA:       14:00 - 22:00 CET   (massima liquidità, trend forti)
Sessione morta:     22:00 - 01:00 CET   (bassa liquidità, evitare entry)
```

- Calcola il momentum (variazione percentuale) nelle ultime N candele del timeframe 5min
- In sessione USA (14:00-22:00 CET): se momentum > +1% nelle ultime 6 candele → bullish
- In sessione Asia (01:00-09:00 CET): usa periodo più lungo (12 candele) — trend più lento
- In sessione morta (22:00-01:00 CET): forza neutral sempre (non entrare in posizioni nuove)
- Volume confirmation: il momentum è valido solo se volume_ratio > 1.2 (sopra la media)
- Strength proporzionale all'entità del momentum: min(abs(momentum_pct) / 3.0, 1.0)

```python
class SessionMomentumStrategy(BaseStrategy):
    docs_url = "https://www.investopedia.com/terms/m/momentum_investing.asp"
    config_key = "STRATEGY_SESSION_MOMENTUM_ENABLED"
```

---

## PARTE 3 — AI AGENT

### ai/system_prompt.py

Il system prompt deve avere sezioni distinte per azioni e crypto.

```
SEZIONE 1 — RUOLO
Sei un analista quantitativo specializzato in micro-investimenti a breve termine.
Ricevi segnali pre-elaborati da più strategie e devi produrre una decisione finale.
Il tuo output deve essere ESCLUSIVAMENTE un JSON valido. Zero testo aggiuntivo.

SEZIONE 2 — STRATEGIE ATTIVE
Ricevi i segnali già calcolati da queste strategie (solo quelle abilitate):
- mean_reversion: basata su Z-score e Bollinger Bands
- sentiment: basata su news e Fear & Greed Index
- btc_correlation: filtro BTC per asset crypto
- fear_greed_contrarian: contrarian su indice Fear & Greed
- session_momentum: momentum basato sulla sessione di trading attiva

SEZIONE 3 — REGOLE PER AZIONI (asset_type = "stock")
- Confidence minima per operare: 0.68
- In caso di segnali contrastanti: HOLD
- Stop loss range: 0.5% - 3.0%
- Take profit range: 1.0% - 6.0%
- Non operare fuori dalle ore NYSE (09:30-16:00 ET)

SEZIONE 4 — REGOLE PER CRYPTO (asset_type = "crypto")
- Confidence minima per operare: 0.75 (più alta — mercato più volatile)
- Se btc_correlation segnala bearish forte: forza HOLD su tutti i crypto
- Se fear_greed < 25 (extreme fear): favorisci BUY ma riduci position size
- Se fear_greed > 75 (extreme greed): favorisci SELL o HOLD
- Non aprire nuove posizioni in sessione morta (22:00-01:00 CET)
- Stop loss range: 1.5% - 5.0%
- Take profit range: 3.0% - 10.0%
- Position size massimo: 50% rispetto alle azioni

SEZIONE 5 — SCHEMA OUTPUT JSON
[schema completo come definito nella sezione claude_agent.py]
```

### ai/claude_agent.py

- Modello: `claude-sonnet-4-20250514`
- `max_tokens=1024`
- Retry logic: 3 tentativi con backoff esponenziale (1s, 2s, 4s)
- Valida output con Pydantic prima di restituire
- Logga prompt inviato e risposta ricevuta (file separato `logs/claude_audit.log`)
- Gestisci: `anthropic.APIConnectionError`, `anthropic.RateLimitError`, `json.JSONDecodeError`
- Rate limit: sleep 3 secondi tra chiamate consecutive sullo stesso simbolo

Schema JSON output di Claude (validato con Pydantic):
```json
{
  "action": "BUY | SELL | HOLD",
  "asset_type": "stock | crypto",
  "confidence": "float 0.0-1.0",
  "reasoning": "string max 3 frasi",
  "strategy_signals": {
    "mean_reversion": {"signal": "bullish|bearish|neutral", "strength": 0.0},
    "sentiment": {"signal": "bullish|bearish|neutral", "strength": 0.0},
    "btc_correlation": {"signal": "bullish|bearish|neutral", "strength": 0.0},
    "fear_greed": {"signal": "bullish|bearish|neutral", "strength": 0.0},
    "session_momentum": {"signal": "bullish|bearish|neutral", "strength": 0.0}
  },
  "dominant_strategy": "nome della strategia con peso maggiore",
  "stop_loss_pct": "float",
  "take_profit_pct": "float",
  "position_size_pct": "float",
  "time_horizon": "scalping | intraday | swing",
  "session": "asia | europe | usa | dead",
  "fear_greed_index": "int 0-100 | null",
  "warnings": ["array di stringhe"]
}
```

---

## PARTE 4 — RISK MANAGEMENT

### risk/risk_manager.py

Controlli in sequenza per AZIONI:
1. `confidence < CONFIDENCE_THRESHOLD` → HOLD
2. `MAX_OPEN_POSITIONS` raggiunto → HOLD
3. Perdita giornaliera > `MAX_DAILY_LOSS_PCT` → ferma bot + notifica
4. Già in posizione nella stessa direzione → HOLD
5. Fuori ore NYSE → HOLD
6. Clippa `position_size_pct` a `MAX_POSITION_SIZE_PCT`
7. Verifica range stop_loss e take_profit

Controlli aggiuntivi per CRYPTO:
8. `confidence < CRYPTO_CONFIDENCE_THRESHOLD` → HOLD
9. `CRYPTO_MAX_OPEN_POSITIONS` raggiunto → HOLD
10. Sessione morta (22:00-01:00 CET) → HOLD su nuove posizioni
11. BTC_change_1h < -3% e strategia BTC correlation abilitata → HOLD
12. Clippa `position_size_pct` a `CRYPTO_MAX_POSITION_SIZE_PCT`

---

## PARTE 5 — DASHBOARD UI (NUOVO — ARTICOLATA)

La dashboard è un'applicazione web multi-pagina servita da FastAPI.
Il design deve essere professionale, dark-mode by default, responsive.
Usa Tailwind CSS via CDN per lo styling e Chart.js via CDN per i grafici.
Usa Alpine.js via CDN per la reattività client-side (nessun framework pesante).

### Layout generale

Struttura con sidebar fissa a sinistra e contenuto principale a destra:

```
┌─────────────────────────────────────────────────────────┐
│  SIDEBAR (240px)     │  CONTENUTO PRINCIPALE             │
│                      │                                   │
│  [Logo + Bot name]   │  [Header pagina + breadcrumb]     │
│                      │                                   │
│  • Dashboard         │  [Contenuto specifico pagina]     │
│  • Portfolio         │                                   │
│  • Trade History     │                                   │
│  • Strategies        │                                   │
│  • Settings          │                                   │
│                      │                                   │
│  ─────────────────   │                                   │
│  [Status bar]        │                                   │
│  BOT: RUNNING        │                                   │
│  MODE: PAPER         │                                   │
│  BROKER: BOTH        │                                   │
└─────────────────────────────────────────────────────────┘
```

La sidebar mostra sempre in basso:
- Indicatore stato bot (verde pulsante = running, rosso = stopped)
- Modalità attiva (badge PAPER in giallo o LIVE in rosso)
- Broker attivi (badge IBKR e/o COINBASE)
- Pulsante "Stop Bot" (con conferma modale)

---

### Pagina 1 — Dashboard (/)

Griglia di card nella parte superiore (4 colonne):
```
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Portfolio    │ │ P&L Oggi     │ │ Trade Oggi   │ │ Win Rate     │
│ $12,450.00   │ │ +$127.50     │ │ 8 eseguiti   │ │ 75%          │
│ +2.55% oggi  │ │ +1.03%       │ │ 3 HOLD       │ │ ultimi 30gg  │
└──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
```

Sezione centrale — due colonne:
- Colonna sinistra (60%): grafico P&L intraday (line chart, aggiornamento live)
- Colonna destra (40%): "Ultima analisi" per ogni simbolo monitorato
  con indicatore BUY/SELL/HOLD colorato e confidence score come barra

Sezione inferiore — due colonne:
- Colonna sinistra: ultimi 5 trade eseguiti (tabella compatta)
- Colonna destra: "Live Log" — stream degli ultimi 20 messaggi di log
  con auto-scroll verso il basso, aggiornamento ogni 5 secondi via fetch

Fear & Greed widget (solo se Coinbase abilitato):
- Box separato con il valore attuale dell'indice
- Gauge semicircolare colorata (0-24 verde scuro, 25-44 verde,
  45-55 giallo, 56-74 arancione, 75-100 rosso)
- Label testuale (Extreme Fear / Fear / Neutral / Greed / Extreme Greed)

---

### Pagina 2 — Portfolio (/portfolio)

Sezione superiore: valore totale portfolio con breakdown per broker
```
┌─────────────────────────────────────────┐
│ Portfolio Totale: $12,450.00            │
│ ├── IBKR (azioni):   $8,200.00  65.8%  │
│ └── Coinbase (crypto): $4,250.00 34.2% │
└─────────────────────────────────────────┘
```

Grafico a torta (donut): allocazione per asset con hover tooltip.

Tabella posizioni aperte con colonne:
- Simbolo | Broker | Tipo (stock/crypto) | Direzione | Prezzo entrata |
  Prezzo attuale | P&L $ | P&L % | Stop Loss | Take Profit | Aperta il

Ogni riga ha un pulsante "Chiudi posizione" (in paper mode: simula la chiusura).

Grafico P&L storico (30 giorni): line chart con area fill sotto la linea.
Toggle per visualizzare: Totale / Solo IBKR / Solo Coinbase.

---

### Pagina 3 — Trade History (/trades)

Filtri in alto:
- Date range picker (da / a)
- Filtro Broker (Tutti / IBKR / Coinbase)
- Filtro Action (Tutti / BUY / SELL / HOLD)
- Filtro Simbolo (input testuale)
- Filtro Strategia (dropdown con le strategie attive)

Tabella paginata (20 righe per pagina) con colonne:
- # | Timestamp | Simbolo | Broker | Action (badge colorato) |
  Confidence | Strategia | Prezzo | Quantità | P&L | Eseguito

Click su una riga espande un pannello con:
- Reasoning completo di Claude
- Tutti i segnali delle strategie con i loro valori (bullish/bearish + strength)
- Warning emessi
- Fear & Greed al momento del trade (se crypto)
- Sessione attiva al momento del trade

Export CSV della tabella filtrata (pulsante in alto a destra).

---

### Pagina 4 — Strategies (/strategies)

Per ogni strategia configurata, mostra una card con:

```
┌────────────────────────────────────────────────────────┐
│ [●] Mean Reversion                    [ENABLED] [Docs] │
│                                                        │
│ Identifica asset che si sono allontanati troppo dalla  │
│ loro media storica e scommette sul ritorno.            │
│                                                        │
│ Config:                                                │
│   STRATEGY_MEAN_REVERSION_ENABLED = true               │
│                                                        │
│ Performance (ultimi 30gg):                             │
│   Segnali generati: 142  │  Accuracy: 68%              │
│   Contributo medio: +0.8% per trade                   │
│                                                        │
│ Ultimi segnali:                                        │
│   AAPL  bullish  0.82  "Z-score -2.3, RSI 28"         │
│   MSFT  neutral  0.10  "Z-score -0.4"                 │
│   BTC   bearish  0.71  "Z-score +2.1, RSI 74"         │
└────────────────────────────────────────────────────────┘
```

Il badge [ENABLED] è un toggle cliccabile che chiama `POST /api/strategies/{name}/toggle`
e aggiorna la config in runtime senza riavviare il bot.

Il link [Docs] apre la documentazione ufficiale della strategia (URL da `docs_url`).

In fondo alla pagina: sliders per modificare i pesi delle strategie nel multi-signal.
I tre sliders (Technical / Sentiment / Macro) devono sommare a 1.0 con validazione live.
Pulsante "Salva pesi" che chiama `POST /api/strategies/weights`.

---

### Pagina 5 — Settings (/settings)

Organizzata in sezioni con accordion:

**Sezione Broker:**
- Radio button: IBKR only / Coinbase only / Both
- Mostra/nasconde le sezioni IBKR e Coinbase in base alla selezione
- IBKR: campo host, porta (con info tooltip "7497=paper, 7496=live"), client ID
- Coinbase: campo API Key, API Secret (masked), pulsante "Test connessione"
- Pulsante "Salva configurazione broker"

**Sezione Risk Management:**
- Due colonne: azioni (sinistra) e crypto (destra)
- Sliders per: confidence threshold, max position size, stop loss default,
  take profit default, max open positions
- Campo: max daily loss %
- Toggle: Paper Mode (con warning rosso "ATTENZIONE: disabilitare Paper Mode
  eseguirà ordini REALI sul tuo conto")

**Sezione Notifiche:**
- Toggle Email notifications + campo email
- Toggle: notifica su trade / notifica su errore / summary giornaliero
- Pulsante "Invia email di test"

**Sezione Watchlist:**
- Due liste editabili: Azioni e Crypto
- Add/remove simboli con validazione (controlla che il simbolo esista su IBKR/Coinbase)
- Drag & drop per riordinare la priorità di analisi

Tutte le modifiche alle settings chiamano `POST /api/settings` e aggiornano
la config in runtime. Le modifiche critiche (broker mode, paper mode) richiedono
una conferma modale prima di applicarsi.

---

### API endpoints FastAPI

```python
# Status
GET  /api/status           # stato bot, broker, ultima analisi, uptime
GET  /api/health           # health check per monitoring

# Portfolio
GET  /api/portfolio        # valore portfolio, posizioni aperte, breakdown broker
GET  /api/portfolio/pnl    # P&L storico per grafico (parametro: days=30)

# Trades
GET  /api/trades/recent    # ultimi N trade (parametro: limit=10)
GET  /api/trades           # trade con filtri e paginazione
GET  /api/trades/{id}      # dettaglio singolo trade
GET  /api/trades/export    # CSV download

# Strategie
GET  /api/strategies       # lista strategie con stato e performance
POST /api/strategies/{name}/toggle   # abilita/disabilita strategia
POST /api/strategies/weights         # aggiorna pesi multi-signal

# Settings
GET  /api/settings         # configurazione attuale
POST /api/settings         # aggiorna configurazione

# Log
GET  /api/logs             # ultimi N log (parametro: limit=50)
GET  /api/logs/stream      # Server-Sent Events per live log nella dashboard

# Controllo bot
POST /api/bot/stop         # ferma il bot gracefully
POST /api/bot/start        # avvia il bot
POST /api/bot/analyze/{symbol}  # forza analisi immediata di un simbolo

# Fear & Greed (solo se Coinbase abilitato)
GET  /api/fear-greed       # valore attuale e storico 7 giorni
```

---

## PARTE 6 — DATI E INDICATORI

### data/indicators.py

Calcola su DataFrame OHLCV con `pandas-ta`:
- RSI(14)
- MACD(12, 26, 9) → valore, signal, histogram
- Bollinger Bands(20, 2) → upper, mid, lower, bb_position (0-1)
- Volume ratio: volume attuale / SMA30 del volume
- ATR(14) per position sizing dinamico
- EMA(20) e EMA(50) per trend direction
- Z-score del prezzo rispetto a SMA(20): (price - sma20) / std20

Restituisci dataclass `TechnicalSignals` con tutti i campi tipizzati e nullable
(alcuni indicatori potrebbero non essere calcolabili con pochi dati storici).

### data/fear_greed_client.py

- Fetch da `https://api.alternative.me/fng/?limit=7` (ultimi 7 giorni)
- Cache in memoria con TTL 1 ora (l'indice cambia una volta al giorno)
- Restituisce `FearGreedData`: value (int), classification (str), timestamp, history (list)
- Gestisce gracefully il fallimento della chiamata (restituisce None, non crashare)

### data/news_client.py

- Fonte: NewsAPI.org (`https://newsapi.org/v2/everything`)
- Fetch ultime 10 news nelle ultime 6 ore per simbolo
- Sentiment scoring con keyword matching (lista ~30 termini positivi, ~30 negativi):
  - Positivi: "surge", "rally", "bullish", "growth", "beat", "profit", "upgrade",
    "partnership", "launch", "record", "breakthrough", "strong", "buy", ecc.
  - Negativi: "crash", "bearish", "loss", "decline", "drop", "downgrade", "risk",
    "investigation", "lawsuit", "miss", "weak", "sell", "concern", ecc.
- Score = (positive_hits - negative_hits) / total_words, normalizzato in [-1, 1]
- Restituisce `SentimentData`: score, news_count, headlines (lista di dict)

---

## PARTE 7 — MONITORING E LOGGING

### monitoring/logger.py

Database SQLite `trading_bot.db` con queste tabelle:

```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    broker TEXT NOT NULL,               -- "ibkr" | "coinbase"
    asset_type TEXT NOT NULL,           -- "stock" | "crypto"
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
    strategy_signals TEXT,              -- JSON oggetto con tutti i segnali
    warnings TEXT,                      -- JSON array
    fear_greed_value INTEGER,           -- null per azioni
    session TEXT,                       -- "asia"|"europe"|"usa"|"dead"|null
    paper_mode INTEGER DEFAULT 1,
    executed INTEGER DEFAULT 0,
    closed_at TEXT,
    close_price REAL,
    pnl_usd REAL,
    pnl_pct REAL
);

CREATE TABLE portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    total_value_usd REAL,
    ibkr_value_usd REAL,
    coinbase_value_usd REAL,
    cash_usd REAL,
    daily_pnl_usd REAL,
    daily_pnl_pct REAL,
    open_positions TEXT                 -- JSON
);

CREATE TABLE strategy_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    signal TEXT NOT NULL,
    strength REAL,
    reason TEXT,
    enabled INTEGER
);

CREATE TABLE errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    component TEXT,
    error_type TEXT,
    message TEXT,
    resolved INTEGER DEFAULT 0
);

CREATE TABLE bot_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    level TEXT NOT NULL,                -- "INFO"|"WARNING"|"ERROR"
    component TEXT,
    message TEXT
);
```

---

## PARTE 8 — SETUP E AVVIO

### setup_mac.sh

Script bash interattivo che:
1. Verifica Python 3.11+ installato
2. Crea venv e installa requirements
3. Chiede interattivamente:
   - Quale broker usare (ibkr / coinbase / both)
   - Se IBKR: host e porta
   - Se Coinbase: API key e secret
   - API key Anthropic
   - API key NewsAPI
   - Email notifiche (opzionale)
4. Salva tutte le API key nel macOS Keychain
5. Crea `.env` con i valori non sensibili
6. Crea launchd plist con `caffeinate` per prevenire lo sleep
7. Carica il launchd agent automaticamente
8. Esegue `test_paper.py` per verificare che tutto funzioni
9. Stampa URL dashboard e istruzioni finali

### main.py

```python
async def main():
    # 1. Carica config e verifica broker abilitati
    # 2. Inizializza client (solo quelli abilitati da BROKER_MODE)
    # 3. Avvia dashboard FastAPI in thread separato
    # 4. Avvia APScheduler:
    #    - Job stocks (se IBKR abilitato): ogni ANALYSIS_INTERVAL_STOCKS
    #      solo durante ore NYSE
    #    - Job crypto (se Coinbase abilitato): ogni ANALYSIS_INTERVAL_CRYPTO
    #      sempre attivo
    #    - Snapshot portfolio: ogni 10 minuti
    #    - Health check: ogni 60 secondi
    #    - Fear & Greed refresh: ogni ora (se Coinbase abilitato)
    # 5. Gestisci SIGINT/SIGTERM per graceful shutdown
```

Ciclo di analisi per ogni simbolo:
```python
async def analyze_symbol(symbol, broker, asset_type):
    # a. Fetch OHLCV (100 candele, 5min)
    # b. Calcola TechnicalSignals
    # c. Fetch SentimentData da news
    # d. Se crypto: fetch FearGreedData, calcola BTCCorrelation
    # e. Esegui tutte le strategie abilitate → lista di StrategySignal
    # f. Costruisci prompt con tutti i segnali
    # g. Chiama Claude → TradeDecision
    # h. Passa a RiskManager → decisione finale
    # i. Se approvata: esegui ordine (paper o live)
    # j. Logga tutto nel DB
    # k. Notifica se necessario
    # l. Aggiorna signal_gauge nella dashboard
```

### test_paper.py

Test completo con dati mock (nessuna connessione reale):
- Genera OHLCV sintetico per AAPL e BTC-USD
- Calcola tutti gli indicatori
- Esegue tutte le strategie
- Chiama Claude con dati mock (usa una risposta hardcoded se API non disponibile)
- Verifica risk manager con vari scenari (confidence bassa, sessione morta, ecc.)
- Simula 5 trade in paper mode
- Verifica che il DB venga popolato correttamente
- Stampa report finale dei test

---

## VINCOLI FINALI

1. **Sicurezza**: API key mai in log o codice. Solo env vars o Keychain.
2. **Paper Mode default**: `PAPER_MODE = True`. Passare a live richiede
   modifica config E cambio porta IBKR a 7496 — doppia conferma.
3. **Isolamento errori**: ogni componente fallisce in isolamento.
   Se news API è down → analisi continua senza sentiment (warning nel log).
   Se Coinbase è down → bot continua con solo IBKR (se abilitato).
   Se IBKR si disconnette → riconnette automaticamente ogni 30s.
4. **Mac compatibility**: macOS 13+, Python 3.11+. Nessuna dipendenza
   che richieda compilazione (no ta-lib, no C extensions).
5. **Rate limiting**: Claude max 1 chiamata ogni 3s per simbolo.
   NewsAPI max 100 req/giorno sul piano gratuito — gestisci cache.
6. **Ore di mercato**: azioni solo NYSE 09:30-16:00 ET lun-ven.
   Crypto: sempre, ma no nuove posizioni in sessione morta (22:00-01:00 CET).
7. **Broker mode runtime**: il cambio di BROKER_MODE da settings
   richiede riavvio del bot (avvisa l'utente nella UI).
8. **Strategia toggle runtime**: abilitare/disabilitare singole strategie
   dalla UI deve funzionare senza riavvio (aggiorna config in memoria).

---

## PRIMO PASSO — ORDINE DI IMPLEMENTAZIONE

Implementa i moduli in questo ordine. Dopo ogni modulo aggiungi
un blocco `if __name__ == "__main__"` con un esempio funzionante.

1.  `config.py` + `.env.example`
2.  `data/indicators.py`
3.  `strategies/base_strategy.py`
4.  `strategies/mean_reversion.py`
5.  `strategies/sentiment_trading.py`
6.  `strategies/btc_correlation_filter.py`
7.  `strategies/fear_greed_contrarian.py`
8.  `strategies/session_momentum.py`
9.  `strategies/multi_signal.py`
10. `data/fear_greed_client.py`
11. `data/news_client.py`
12. `ai/system_prompt.py`
13. `ai/prompt_builder.py`
14. `ai/claude_agent.py`
15. `risk/risk_manager.py` + `risk/position_sizer.py`
16. `execution/paper_mode.py`
17. `monitoring/logger.py`
18. `data/ibkr_client.py` (solo se IBKR abilitato)
19. `data/coinbase_client.py` (solo se Coinbase abilitato)
20. `execution/ibkr_executor.py` + `execution/coinbase_executor.py`
21. `monitoring/notifier.py`
22. `templates/` (tutti i file HTML)
23. `monitoring/dashboard.py`
24. `main.py`
25. `setup_mac.sh`
26. `test_paper.py`
