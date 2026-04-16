# CLAUDE.md — PacificaEdge v2
> Read this entire file before writing a single line of code.
> This is the complete specification. Every agent, every endpoint, every feature, every route.

---

## Project Identity

**Project Name:** PacificaEdge  
**Tagline:** *The AI Multi-Agent Intelligence Terminal for Pacifica Traders*  
**Hackathon:** Pacifica Hackathon (DoraHacks) — Analytics & Data Track  
**Deadline:** April 16, 2026 — 9:29 PM IST  
**Prize Target:** $5,000 Grand Prize + $2,000 Analytics Track + $1,000 Best UX = $8,000  
**Positioning:** Not a trading bot. Not a DeFi vault. A real-time intelligence terminal — the Bloomberg Terminal layer that Pacifica doesn't have yet.

---

## What We Are Building

A **FastAPI backend + plain HTML/JS frontend** where **6 AI agents** run in parallel, each monitoring a specific market signal on Pacifica (Solana's #1 perpetuals DEX). All 6 agents feed into a unified Signal Engine that outputs a single **BUY / SELL / HOLD** recommendation per market.

On top of that, **Claude (claude-sonnet-4-20250514)** acts as the Signal Narrator — explaining every decision in plain English. A conversational interface lets traders ask questions about live market state. Signal history is tracked and accuracy scored in real time.

**This is the only project in the hackathon that combines:**
- 6 parallel agents with explainable AI reasoning
- Claude narrating every signal decision in English
- Elfa trending narratives (deepest Elfa integration in the competition)
- Cross-market macro correlation alerts
- 30-day historical backtest accuracy per signal pattern
- Order book depth heatmap with whale wall detection
- Live signal accuracy self-tracker
- Email + Telegram dual alert system
- Conversational Claude analyst interface
- Annualized ROI calculator on funding signals

---

## Mandatory Hackathon Rules

1. ✅ **MUST use Pacifica API and/or Builder Code** — every agent calls Pacifica endpoints
2. ✅ **All code written during hackathon period** (Mar 16 – Apr 16)
3. ✅ **Team registered** via https://forms.gle/1FP2EuvZqYiP7Tiy7
4. ✅ **Submit via** https://forms.gle/zYm9ZBH1SoUE9t9o7 before April 16 9:29 PM IST
5. ✅ **Demo video required** (max 10 minutes) — upload to YouTube

---

## API Endpoints — All Data Sources

### Pacifica REST API

```
MAINNET:  https://api.pacifica.fi/api/v1
TESTNET:  https://test-api.pacifica.fi/api/v1   ← use this during development
```

| Endpoint | Method | Auth | Used By |
|---|---|---|---|
| `/markets` | GET | ❌ | MarketAgent, NarrativeAgent |
| `/markets/summary` | GET | ❌ | MarketAgent |
| `/markets/{symbol}/orderbook` | GET | ❌ | OrderBookAgent |
| `/markets/{symbol}/trades` | GET | ❌ | LiquidationAgent |
| `/markets/{symbol}/funding` | GET | ❌ | FundingAgent |
| `/markets/{symbol}/klines` | GET | ❌ | MarketAgent, BacktestEngine |
| `/account` | GET | ✅ API Key | Optional — account info |
| `/orders` | POST | ✅ API Key | Optional — demo order placement |

### Elfa AI API

```
Base URL: https://api.elfa.ai/v2
Auth: x-elfa-api-key header
```

| Endpoint | Used By |
|---|---|
| `GET /aggregations/trending-tokens` | NarrativeAgent |
| `GET /aggregations/trending-narratives` | NarrativeAgent |
| `GET /v1/smart-twitter-account-stats` | SentimentAgent (fallback to v1) |
| `GET /aggregations/trending-cas/twitter` | NarrativeAgent |

### Anthropic Claude API

```
Model: claude-sonnet-4-20250514
Used by: SignalNarrator, ConversationalAnalyst
Max tokens: 300 per call (keep it snappy)
```

---

## Project Structure

```
pacifica-edge/
├── CLAUDE.md                    ← This file
├── README.md                    ← Submission README
├── requirements.txt
├── .env                         ← Never commit
├── .env.example                 ← Commit this
├── main.py                      ← FastAPI app entry point
│
├── agents/
│   ├── __init__.py
│   ├── market_agent.py          ← Agent 1: Price, OI, Volume, EMA trend
│   ├── funding_agent.py         ← Agent 2: Funding rates + annualized ROI
│   ├── liquidation_agent.py     ← Agent 3: Liquidation detector
│   ├── sentiment_agent.py       ← Agent 4: Elfa social sentiment score
│   ├── narrative_agent.py       ← Agent 5: Elfa trending narratives (NEW)
│   ├── orderbook_agent.py       ← Agent 6: Order book depth + whale walls (NEW)
│   └── signal_agent.py          ← Master: Combines all 6 → BUY/SELL/HOLD
│
├── services/
│   ├── pacifica.py              ← Pacifica API async client (httpx)
│   ├── elfa.py                  ← Elfa AI async client
│   ├── claude_narrator.py       ← Claude signal explanation engine (NEW)
│   ├── backtest_engine.py       ← Historical signal accuracy (NEW)
│   ├── accuracy_tracker.py      ← Live signal self-scoring (NEW)
│   ├── email_alert.py           ← SMTP email alerts
│   └── telegram_alert.py        ← Telegram Bot alerts (NEW)
│
└── static/
    └── index.html               ← Full frontend (single file)
```

---

## Tech Stack

### Backend
```
Python 3.11+
FastAPI          — web framework
uvicorn          — ASGI server
httpx            — async HTTP for all API calls
anthropic        — Claude SDK
python-dotenv    — .env loading
pydantic         — data validation
```

### Frontend
```
Plain HTML5 + Vanilla JavaScript — no React, no Vue
Chart.js (CDN)          — price chart + depth heatmap
Inter font (Google CDN) — typography
Dark terminal theme     — #0d0d0f background
```

### requirements.txt
```
fastapi==0.115.0
uvicorn==0.30.0
httpx==0.27.0
anthropic==0.25.0
python-dotenv==1.0.1
```

---

## The 6 Agents — Full Specification

### Agent 1: MarketAgent (`agents/market_agent.py`)

```python
# Pacifica endpoints: GET /markets/summary + GET /markets/{symbol}/klines
# Purpose: Live price, OI, volume + trend via 1h EMA
#
# Trend logic:
#   price > EMA_1h AND volume_24h > volume_24h_prev → "BULLISH"
#   price < EMA_1h AND volume_24h < volume_24h_prev → "BEARISH"
#   else → "NEUTRAL"
#
# Output:
# {
#   symbol: str,
#   price: float,
#   change_24h: float,
#   volume_24h: float,
#   open_interest: float,
#   ema_1h: float,
#   trend: "BULLISH" | "BEARISH" | "NEUTRAL",
#   signal_value: int  # +1 / -1 / 0
# }
```

---

### Agent 2: FundingAgent (`agents/funding_agent.py`)

```python
# Pacifica endpoint: GET /markets/{symbol}/funding
# Purpose: Funding rate monitoring + annualized ROI calculation
#
# Signal logic:
#   funding_rate > +0.01%  → "BEARISH" (longs paying = crowded long)
#   funding_rate < -0.01%  → "BULLISH" (shorts paying = crowded short → squeeze setup)
#   else                   → "NEUTRAL"
#
# Annualized ROI: funding_rate * 3 * 365 * 100  (3 payments/day)
#
# Output:
# {
#   symbol: str,
#   funding_rate: float,
#   annualized_rate_pct: float,   ← e.g. -16.4 means "earn 16.4% APY delta-neutral"
#   next_funding_time: str,
#   signal: "BULLISH" | "BEARISH" | "NEUTRAL",
#   signal_value: int,
#   roi_message: str   ← "Earn 16.4% APY on delta-neutral position right now"
# }
```

---

### Agent 3: LiquidationAgent (`agents/liquidation_agent.py`)

```python
# Pacifica endpoint: GET /markets/{symbol}/trades
# Purpose: Detect large liquidation events from recent trade feed
#
# Logic:
#   Filter trades where is_liquidation == True
#   Aggregate last 5 minutes of data
#   long_liqs_usd  = sum of liquidated long positions
#   short_liqs_usd = sum of liquidated short positions
#
# Signal logic:
#   short_liqs_usd > long_liqs_usd * 2  → "BULLISH" (shorts getting wiped → upward pressure)
#   long_liqs_usd  > short_liqs_usd * 2 → "BEARISH" (longs getting wiped → downward pressure)
#   else                                 → "NEUTRAL"
#
# Output:
# {
#   long_liquidations_usd: float,
#   short_liquidations_usd: float,
#   dominant_side: "LONGS" | "SHORTS" | "BALANCED",
#   liq_ratio: float,
#   signal: str,
#   signal_value: int
# }
```

---

### Agent 4: SentimentAgent (`agents/sentiment_agent.py`)

```python
# Elfa AI endpoint: GET /v1/smart-twitter-account-stats
# Purpose: Social sentiment score from crypto Twitter smart accounts
#
# Signal logic:
#   score > 60 → "BULLISH"
#   score < 40 → "BEARISH"
#   else       → "NEUTRAL"
#
# ALWAYS has fallback: if Elfa is down, return NEUTRAL, never crash
#
# Output:
# {
#   sentiment_score: int,   # 0-100
#   label: str,
#   top_keywords: list[str],
#   signal: str,
#   signal_value: int
# }
```

---

### Agent 5: NarrativeAgent (`agents/narrative_agent.py`) ← NEW

```python
# Elfa AI endpoints:
#   GET /v2/aggregations/trending-narratives
#   GET /v2/aggregations/trending-tokens
#   GET /v2/aggregations/trending-cas/twitter
#
# Purpose: What STORY is the market telling right now?
# This is the deepest Elfa integration in the entire hackathon.
# No other competitor uses these endpoints.
#
# Logic:
#   Pull top 5 trending narratives in last 6h
#   Tag each narrative: BULLISH / BEARISH / NEUTRAL
#   Bullish narrative keywords: ["squeeze", "accumulation", "breakout",
#                                "rotation", "bounce", "reversal", "ETF"]
#   Bearish narrative keywords: ["dump", "liquidation cascade", "sell",
#                                "regulation", "crash", "bear", "outflow"]
#
# Scoring:
#   Each bullish narrative → +0.5 (capped at +1.5 total)
#   Each bearish narrative → -0.5 (floored at -1.5 total)
#   Round to nearest whole → contributes ±1 or 0 to signal engine
#
# Output:
# {
#   top_narratives: [
#     { title: str, sentiment: str, mention_count: int, trending_score: float }
#   ],
#   narrative_summary: str,   # "ETH L2 rotation gaining momentum — 3 alpha accounts flagged"
#   raw_score: float,
#   signal_value: int,        # -1, 0, or +1
#   signal: str
# }
```

---

### Agent 6: OrderBookAgent (`agents/orderbook_agent.py`) ← NEW

```python
# Pacifica endpoint: GET /markets/{symbol}/orderbook
# Purpose: Order book depth analysis + whale wall detection
#
# Calculations:
#   bid_total_usd = sum of top 20 bid levels * price
#   ask_total_usd = sum of top 20 ask levels * price
#   imbalance_ratio = bid_total_usd / (bid_total_usd + ask_total_usd)
#
# Signal logic:
#   imbalance_ratio > 0.65 → "BULLISH" (heavy bid side = buyers dominating)
#   imbalance_ratio < 0.35 → "BEARISH" (heavy ask side = sellers dominating)
#   else                   → "NEUTRAL"
#
# Whale wall detection:
#   A single level > 5% of total book depth = WALL
#   bid_wall_price, bid_wall_size_usd
#   ask_wall_price, ask_wall_size_usd
#
# Output:
# {
#   bid_total_usd: float,
#   ask_total_usd: float,
#   imbalance_ratio: float,
#   bid_wall: { price: float, size_usd: float } | None,
#   ask_wall: { price: float, size_usd: float } | None,
#   wall_alert: str | None,   # "🐋 $2.1M ask wall at $94,500 — potential resistance"
#   signal: str,
#   signal_value: int,
#   depth_data: list   # raw bid/ask arrays for frontend chart
# }
```

---

### Signal Engine: SignalAgent (`agents/signal_agent.py`)

```python
# Purpose: Aggregate all 6 agents into one final signal
#
# Scoring:
#   Each agent BULLISH  → +1
#   Each agent BEARISH  → -1
#   Each agent NEUTRAL  →  0
#   Max possible score: +6 / Min: -6
#
# Final signal thresholds:
#   score >= +2  → "BUY"  🟢
#   score <= -2  → "SELL" 🔴
#   else         → "HOLD" 🟡
#
# Confidence calculation:
#   confidence_pct = abs(score) / 6 * 100
#   e.g. score=4 → 66.7% confidence
#
# Output:
# {
#   signal: "BUY" | "SELL" | "HOLD",
#   score: int,
#   confidence_pct: float,
#   agent_breakdown: { market, funding, liquidation, sentiment, narrative, orderbook },
#   macro_alert: str | None,    ← cross-market correlation (see below)
#   timestamp: str
# }
```

---

## Claude Integration — Signal Narrator & Conversational Analyst

### Claude Signal Narrator (`services/claude_narrator.py`) ← NEW

Every time the SignalAgent produces a final signal, pipe ALL agent outputs into Claude and get a 2-sentence plain English explanation.

```python
import anthropic

async def narrate_signal(symbol: str, signal_data: dict, agent_outputs: dict) -> str:
    client = anthropic.Anthropic()

    prompt = f"""
You are a senior crypto trading analyst. These 6 market intelligence agents just fired on {symbol}.

Market Agent: {agent_outputs['market']['trend']} — Price {agent_outputs['market']['price']}, OI {agent_outputs['market']['open_interest']}
Funding Agent: {agent_outputs['funding']['signal']} — Rate {agent_outputs['funding']['funding_rate']}% (annualized: {agent_outputs['funding']['annualized_rate_pct']}%)
Liquidation Agent: {agent_outputs['liquidation']['signal']} — Short liqs ${agent_outputs['liquidation']['short_liquidations_usd']:,.0f} vs Long liqs ${agent_outputs['liquidation']['long_liquidations_usd']:,.0f}
Sentiment Agent: {agent_outputs['sentiment']['signal']} — Score {agent_outputs['sentiment']['sentiment_score']}/100
Narrative Agent: {agent_outputs['narrative']['narrative_summary']}
OrderBook Agent: {agent_outputs['orderbook']['signal']} — Imbalance {agent_outputs['orderbook']['imbalance_ratio']:.1%} bid-side{', ' + agent_outputs['orderbook']['wall_alert'] if agent_outputs['orderbook'].get('wall_alert') else ''}

Final Signal: {signal_data['signal']} (score {signal_data['score']}/6, confidence {signal_data['confidence_pct']:.0f}%)

Write exactly 2 sentences explaining WHY this is a {signal_data['signal']} signal to a trader who is about to make a decision. Be specific. Use the data above. No fluff.
"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text

# Fallback: if Claude is down, return template string, never crash
```

---

### Conversational Analyst (`services/claude_narrator.py` — second function) ← NEW

User types a question into the dashboard. The full current state of all markets is injected as context.

```python
async def answer_market_question(question: str, all_markets_state: dict) -> str:
    """
    User asks: "Why is ETH on HOLD while BTC shows BUY?"
    We inject live state for all markets, Claude answers with full context.
    Stream the response back via SSE.
    """
    client = anthropic.Anthropic()

    context = f"Current PacificaEdge market state:\n{json.dumps(all_markets_state, indent=2)}"

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[
            {
                "role": "user",
                "content": f"{context}\n\nTrader question: {question}\n\nAnswer in 3 sentences max. Be specific to the data above."
            }
        ]
    )
    return message.content[0].text
```

---

## Backtest Engine (`services/backtest_engine.py`) ← NEW

When a signal fires, immediately look back at historical klines to see how often this exact signal pattern has been correct.

```python
# Pacifica endpoint: GET /markets/{symbol}/klines?interval=1h&limit=720  (30 days)
#
# Algorithm:
# 1. Pull 30 days of 1h OHLCV candles
# 2. For each historical candle, reconstruct what each agent WOULD have signaled
#    (simplified: use EMA position, funding rate history if available, else price action)
# 3. Find all instances where the SAME signal pattern occurred (same score ±1)
# 4. For each match: check if price was higher/lower 4h later
# 5. Calculate hit rate
#
# Output:
# {
#   pattern_matches: int,          # how many times this pattern appeared in 30 days
#   correct_predictions: int,
#   accuracy_pct: float,
#   avg_move_pct: float,           # average price move when signal was correct
#   backtest_label: str            # "This signal pattern has been correct 4/5 times (+2.3% avg move)"
# }
#
# IMPORTANT: If klines data is insufficient, return:
# { backtest_label: "Insufficient historical data for this pattern" }
# Never crash. Never block the main signal.
```

---

## Live Signal Accuracy Tracker (`services/accuracy_tracker.py`) ← NEW

Stores every signal fired during the session in memory. 30 minutes after a signal fires, checks if price moved in the predicted direction.

```python
# In-memory storage (no DB needed for hackathon)
signal_history = []  # list of SignalRecord

class SignalRecord:
    symbol: str
    signal: str           # BUY / SELL / HOLD
    price_at_signal: float
    timestamp: datetime
    outcome: str | None   # "CORRECT" | "INCORRECT" | "PENDING"
    price_30min_later: float | None

# Every 30 minutes: background task checks all PENDING signals
# BUY was correct  → price_30min_later > price_at_signal
# SELL was correct → price_30min_later < price_at_signal
# HOLD is not scored (ambiguous)

# Exposed via:
# GET /api/accuracy → { signals_today: 9, correct: 7, accuracy_pct: 77.8,
#                       last_signal: {...}, history: [...] }
```

---

## Cross-Market Macro Correlation (`agents/signal_agent.py` — macro layer) ← NEW

After running signals for ALL markets, check for coordinated moves across markets.

```python
# Logic (runs after individual signals are computed):
#
# MACRO RISK-OFF: 3+ markets simultaneously showing SELL
#   → macro_alert = "🚨 MACRO ALERT: Risk-off detected across BTC, ETH, SOL — coordinated selling"
#
# MACRO RISK-ON: 3+ markets simultaneously showing BUY
#   → macro_alert = "🚀 MACRO ALERT: Risk-on across all majors — broad market momentum"
#
# DIVERGENCE: BTC shows BUY while ETH/SOL show SELL (or vice versa)
#   → macro_alert = "⚡ DIVERGENCE: BTC decoupling from altcoins — rotation signal"
#
# This fires as a banner at the top of the dashboard.
# No competitor runs cross-market intelligence. This is a meta-layer above everything.
```

---

## FastAPI Routes (`main.py`)

```python
# CORE
GET  /                              → serves static/index.html
GET  /api/health                    → { status: "ok", timestamp, uptime_seconds }
GET  /api/markets                   → list all Pacifica markets with basic info

# SIGNAL ENGINE
GET  /api/signal/{symbol}           → run all 6 agents + Claude narrator → full signal object
GET  /api/agents/{symbol}           → individual output of each of the 6 agents
GET  /api/signals/all               → run signal for ALL markets simultaneously
GET  /api/macro                     → cross-market correlation analysis

# CHARTS & DATA
GET  /api/chart/{symbol}            → OHLCV klines for price chart
GET  /api/orderbook/{symbol}        → depth data for heatmap visualization
GET  /api/backtest/{symbol}         → historical signal accuracy for current pattern

# ACCURACY TRACKING
GET  /api/accuracy                  → live signal accuracy stats for this session

# CONVERSATIONAL ANALYST
POST /api/ask                       → body: { question: str } → Claude answers with market context

# ALERTS
POST /api/alert/subscribe           → body: { email, telegram_token?, telegram_chat_id?, symbol, trigger_on }
GET  /api/alert/test/{symbol}       → fire a test alert to verify setup
```

---

## Frontend Spec (`static/index.html`)

Single HTML file. Embedded CSS + JS. Dark trading terminal aesthetic. NO external frameworks.

### Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  🔷 PacificaEdge   [BTC-USDC ▼]  [All Markets]  [⟳ Live]       │  ← Header
├──────────────────────────────────────────────────────────────────┤
│  🚨 MACRO ALERT BANNER (appears when cross-market event fires)   │  ← Macro Bar
├───────────────────────────┬──────────────────────────────────────┤
│  📊 Price Chart (Chart.js)│  🤖 SIGNAL ENGINE                   │
│                           │  ┌────────────────────────────────┐  │
│  + Order Book Depth       │  │  🟢 BUY  Score: 4/6            │  │
│    Heatmap below chart    │  │  Confidence: 67%               │  │
│    (bid=green, ask=red)   │  │  ──────────────────────────    │  │
│                           │  │  "Shorts are being liquidated  │  │
│  🐋 Wall alert badge      │  │   at 3x normal rate. Funding   │  │
│  when wall detected       │  │   turned negative — classic    │  │
│                           │  │   squeeze setup."              │  │
│                           │  │  ── Claude Analyst ──          │  │
│                           │  │                                │  │
│                           │  │  Backtest: 4/5 correct (+2.3%) │  │
│                           │  └────────────────────────────────┘  │
├───────────────────────────┴──────────────────────────────────────┤
│  ⚡ AGENT STATUS FEED                                            │
│  [📊 Market: BULLISH ↑]  [💰 Funding: BULLISH -0.015%/8h]      │
│  [💥 Liquidations: BULLISH $2.1M short]  [🐦 Sentiment: 71/100]│
│  [📰 Narrative: BULLISH "ETH rotation"]  [📖 OrderBook: NEUTRAL]│
├──────────────────────────────────────────────────────────────────┤
│  🧠 AGENT BRAIN LOG (terminal-style scrolling feed)             │
│  [14:32:01] MarketAgent  → BTC $94,231 ↑2.3% above 1h EMA → BULLISH  │
│  [14:32:01] FundingAgent → Rate -0.015% (annualized -16.4%) → BULLISH │
│  [14:32:02] LiquidationAgent → Short $2.1M vs Long $0.3M → BULLISH    │
│  [14:32:02] SentimentAgent → Elfa score 71/100 → BULLISH              │
│  [14:32:02] NarrativeAgent → "ETH L2 rotation" trending → BULLISH     │
│  [14:32:03] OrderBookAgent → 62% bid imbalance, no walls → NEUTRAL     │
│  [14:32:03] SignalAgent   → Score 4/6 → 🟢 BUY (67% confidence)       │
│  [14:32:03] Claude        → "Shorts liquidated at 3x normal rate..."   │
├──────────────────────────────────────────────────────────────────┤
│  📈 SIGNAL ACCURACY TODAY: 7/9 correct (77.8%)  [History ▼]    │  ← Accuracy Bar
├──────────────────────────────────────────────────────────────────┤
│  💬 ASK THE ANALYST                                              │
│  [Why is ETH on HOLD while BTC shows BUY?        ] [Ask Claude] │  ← Chat Interface
│  > "ETH order book shows heavy ask walls at $3,200 suppressing  │
│     upside, while BTC funding flipped negative suggesting a     │
│     squeeze. Different market microstructure driving divergence."│
├──────────────────────────────────────────────────────────────────┤
│  🔔 ALERTS                                                       │
│  Email: [your@email.com]  Telegram: [Bot Token] [Chat ID]       │
│  Trigger: [BUY ▼]  Symbol: [BTC-USDC ▼]  [Subscribe] [Test]    │
└──────────────────────────────────────────────────────────────────┘
```

### JS Behavior

```javascript
// Auto-refresh every 30 seconds
setInterval(refreshAllData, 30000);

// Signal card color coding
const colors = { BUY: '#22c55e', SELL: '#ef4444', HOLD: '#eab308' };

// Agent Brain Log — append new entries, keep last 50 lines
function appendBrainLog(entry) {
  const log = document.getElementById('brain-log');
  const line = `<div class="log-line">
    <span class="log-time">[${entry.timestamp}]</span>
    <span class="log-agent">${entry.agent.padEnd(20)}</span>
    <span class="log-signal ${entry.signal.toLowerCase()}">${entry.message}</span>
  </div>`;
  log.insertAdjacentHTML('afterbegin', line);
  if (log.children.length > 50) log.lastChild.remove();
}

// Order book depth chart using Chart.js horizontal bar
function renderDepthChart(depthData) {
  // Green bars = bids, Red bars = asks
  // X-axis = cumulative volume, Y-axis = price levels
  // Highlight whale walls with a border + badge
}

// Conversational interface
async function askAnalyst() {
  const question = document.getElementById('analyst-question').value;
  const response = await fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question })
  });
  const data = await response.json();
  document.getElementById('analyst-answer').textContent = data.answer;
}

// Macro alert banner — animated slide down
function showMacroAlert(alert) {
  const banner = document.getElementById('macro-banner');
  banner.textContent = alert;
  banner.classList.add('visible');
  setTimeout(() => banner.classList.remove('visible'), 15000);
}

// Accuracy bar — live update
function updateAccuracy(stats) {
  document.getElementById('accuracy-display').textContent =
    `Signal Accuracy Today: ${stats.correct}/${stats.total} correct (${stats.accuracy_pct.toFixed(1)}%)`;
}
```

---

## Alert Services

### Email Alert (`services/email_alert.py`)

```python
# Trigger: signal changes state (HOLD→BUY, HOLD→SELL, BUY→SELL)
# Use SMTP (Gmail)
#
# Subject: "🟢 PacificaEdge: BUY Signal on BTC-USDC (Confidence: 67%)"
#
# Body includes:
# - Final signal + score + confidence
# - All 6 agent summaries
# - Claude narrative explanation (2 sentences)
# - Backtest accuracy
# - Funding APY if positive carry
# - Whale wall alert if present
# - Timestamp
```

### Telegram Alert (`services/telegram_alert.py`) ← NEW

```python
# Telegram Bot API: POST https://api.telegram.org/bot{token}/sendMessage
#
# Message format (markdown):
# 🟢 *BUY — BTC-USDC*
# Score: 4/6 | Confidence: 67%
# ─────────────────────
# 📊 Market: BULLISH ↑$94,231
# 💰 Funding: BULLISH (-16.4% APY)
# 💥 Liquidations: BULLISH ($2.1M shorts)
# 🐦 Sentiment: BULLISH (71/100)
# 📰 Narrative: BULLISH (ETH rotation)
# 📖 OrderBook: NEUTRAL (62% bid)
# ─────────────────────
# 🤖 "Shorts liquidated at 3x rate. Funding negative — squeeze setup."
# ─────────────────────
# 📊 Backtest: 4/5 correct (+2.3% avg)
# ⏰ 14:32:03 UTC

async def send_telegram_alert(token: str, chat_id: str, signal_data: dict):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Build message from signal_data
    # Use parse_mode="Markdown"
    # Fallback: if Telegram fails, log error, never crash
```

---

## Environment Variables (`.env.example`)

```bash
# Pacifica API
PACIFICA_BASE_URL=https://api.pacifica.fi/api/v1
PACIFICA_TESTNET_URL=https://test-api.pacifica.fi/api/v1
PACIFICA_API_KEY=           # optional — only for order placement
PACIFICA_API_SECRET=        # optional

# Elfa AI
ELFA_API_KEY=your_elfa_api_key_here

# Anthropic Claude
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# Email Alerts
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@gmail.com
SMTP_PASS=your_app_password

# App Config
USE_TESTNET=true            # SET FALSE before demo recording
PORT=8000

# Optional — Telegram default (users can override per subscription)
DEFAULT_TELEGRAM_TOKEN=
DEFAULT_TELEGRAM_CHAT_ID=
```

---

## Run Commands

```bash
# Install
pip install -r requirements.txt

# Dev (testnet)
USE_TESTNET=true uvicorn main:app --reload --port 8000

# Production / demo
uvicorn main:app --host 0.0.0.0 --port 8000

# Deploy to Railway
# 1. Push to GitHub
# 2. Connect Railway to repo
# 3. Set all env vars in Railway dashboard
# 4. Railway auto-deploys. Get public URL.
```

---

## Code Quality Rules

1. **Never hardcode API keys** — always `os.getenv()`
2. **All API calls must be async** — use `httpx.AsyncClient` everywhere
3. **Every agent must have a try/except fallback** — if API is down, return `{ signal: "NEUTRAL", signal_value: 0, error: "..." }`, never crash
4. **Every agent call must be logged** — `print(f"[{datetime.now().strftime('%H:%M:%S')}] {agent_name} → {symbol} → {signal}")`
5. **All 6 agents must run in parallel** — use `asyncio.gather()` in signal_agent.py, never sequential
6. **Claude calls must have timeout + fallback** — if Claude API takes >3s, return template string
7. **CORS must be enabled** — `CORSMiddleware` on FastAPI
8. **Signal history must persist across refreshes** — use module-level dict in accuracy_tracker.py
9. **USE_TESTNET flag must switch ALL Pacifica calls** — check at client initialization, not scattered in agents
10. **Frontend must work even if one agent fails** — show "Agent Unavailable" card, never blank screen

---

## Demo Video Script (8 minutes)

```
0:00 - 0:30  → Hook: "Every Pacifica trader right now is flying blind. No intelligence layer exists.
                      PacificaEdge changes that. 6 AI agents. Claude narrating every signal.
                      Real-time intelligence built entirely on Pacifica's live API."

0:30 - 1:00  → Show the live dashboard loading. All 6 agent cards updating simultaneously.
               Point out the Brain Log scrolling in real time. Show timestamp on each agent.

1:00 - 2:00  → Walk through each agent card:
               "MarketAgent sees BTC above its 1h EMA with rising OI — BULLISH"
               "FundingAgent sees -0.015% rate — that's 16.4% APY to be on the long side — BULLISH"
               "LiquidationAgent: shorts being wiped at 7x the rate of longs — BULLISH"
               "SentimentAgent: Elfa scores 71/100 smart account activity — BULLISH"
               "NarrativeAgent — this is unique — pulls Elfa's trending narratives API,
                detects 'ETH L2 rotation' story trending — BULLISH"
               "OrderBookAgent: 62% bid-side imbalance. No whale walls. — NEUTRAL"

2:00 - 2:30  → Show the Signal Engine output: "Score 4/6 → 🟢 BUY, 67% confidence"
               Then show Claude's narration: "Shorts being liquidated at 3x normal rate.
               Funding flipped negative — classic squeeze setup."
               SAY: "No other project in this hackathon has Claude explaining every signal in English."

2:30 - 3:00  → Show backtest panel: "This exact pattern was correct 4/5 times in the last 30 days,
               average move +2.3%." SAY: "We validate our own signals against history in real time."

3:00 - 3:30  → Show the Macro Alert banner firing (trigger it): 
               "Risk-on detected across BTC, ETH, SOL simultaneously — broad market momentum."
               SAY: "No competitor is running cross-market intelligence. We see the macro picture."

3:30 - 4:00  → Show the conversational interface. Type: "Why is SOL showing HOLD while BTC shows BUY?"
               Show Claude's answer streaming back with specific data references.
               SAY: "This is not a chatbot. This is a context-aware analyst that knows the live
               state of every Pacifica market."

4:00 - 4:30  → Show accuracy tracker: "7/9 signals correct today — 77.8%"
               SAY: "PacificaEdge scores itself. Every 30 minutes it checks if its signals were right."

4:30 - 5:00  → LIVE MOMENT: Show a signal flip. Navigate to ETH. 
               Show signal change from HOLD → SELL. 
               Watch email AND Telegram alert fire simultaneously on screen.
               SAY: "Dual alert system — email and Telegram. Because crypto traders live in Telegram."

5:00 - 5:30  → Show order book depth heatmap. Point out a whale wall badge if present.
               Show funding ROI panel: "Right now you can earn 16.4% APY on a delta-neutral position."

5:30 - 6:00  → Close: "6 agents. Claude narration. Self-validating accuracy. Cross-market macro
               intelligence. Conversational analyst. Dual alerts. All built on Pacifica's live API.
               Every Pacifica trader would have this open before every trade."
```

---

## 33-Hour Build Order

```
Hours 0-6   → Core 5 original agents + FastAPI + basic HTML + Railway deploy
Hours 6-8   → Claude Narrator (Feature 1) + Brain Log UI (Feature 9)
Hours 8-11  → NarrativeAgent / 6th agent (Feature 2) + Telegram alerts (Feature 8)
Hours 11-15 → Backtest Engine (Feature 3) + Accuracy Tracker (Feature 5)
Hours 15-18 → Cross-market Macro Correlation (Feature 4) + Funding ROI panel (Feature 7)
Hours 18-22 → OrderBook depth heatmap (Feature 6) + Conversational Chat (Feature 10)
Hours 22-28 → Demo video recording — make signal flip on camera, fire both alerts live
Hours 28-31 → README polish, screenshots, submission form
Hours 31-33 → Buffer / fixes
```

---

## What No Competitor Has

| Capability | All other projects | PacificaEdge |
|---|---|---|
| AI signal narration in English | ❌ Black box score | ✅ Claude explains every signal |
| Elfa narratives endpoint | ❌ All use account-stats only | ✅ trending-narratives + trending-tokens |
| Cross-market macro correlation | ❌ Single market loops | ✅ Macro risk-on/off/divergence alerts |
| Historical signal backtesting | ❌ None | ✅ 30-day pattern accuracy |
| Self-validating accuracy tracker | ❌ None | ✅ Live session accuracy % |
| Conversational market analyst | ❌ None | ✅ Claude knows all market state |
| Order book depth heatmap | ❌ None | ✅ Visual depth + whale wall detector |
| Funding APY calculator | ❌ PaciFund tried, 2 commits, fake data | ✅ Real-time ROI on every signal |
| Dual alert system | ❌ Email only or nothing | ✅ Email + Telegram simultaneously |
| 6 parallel agents | ❌ Max 3-4, mostly sequential | ✅ asyncio.gather() all 6 in parallel |

---

## Submission Checklist

- [ ] Team registered: https://forms.gle/1FP2EuvZqYiP7Tiy7
- [ ] All 6 agents calling real Pacifica testnet endpoints
- [ ] Claude narration working and visible in demo
- [ ] Elfa narratives endpoint integrated (Agent 5)
- [ ] Cross-market macro alerts firing
- [ ] Backtest panel showing historical accuracy
- [ ] Email + Telegram alerts both demonstrated in video
- [ ] Conversational interface shown in video
- [ ] Accuracy tracker showing % on screen
- [ ] Deployed on Railway with public URL
- [ ] Demo video uploaded to YouTube (5-8 minutes)
- [ ] Clean README with architecture diagram
- [ ] Submit via: https://forms.gle/zYm9ZBH1SoUE9t9o7
- [ ] Before: **April 16, 2026 — 9:29 PM IST**

---

*PacificaEdge — Built for Pacifica Hackathon 2026*  
*6 agents. Claude narration. Self-validating. No competition.*
