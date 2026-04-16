# PacificaEdge (Pacios)
> A multi-agent crypto research desk built on top of Pacifica's perpetual DEX.

Pacios turns scattered real-time Pacifica market data into a single, explainable BUY/SELL/HOLD signal per market — powered by 6 AI agents, altFINS analytics, Elfa sentiment, Tavily news, and a live Telegram alert system.

---

## What it solves

Most crypto traders are drowning in raw feeds with no context. Pacios acts like a full research team watching every market 24/7 and distilling what matters into one clear, explainable signal — with full agent reasoning, external analytics, backtest evidence, and a natural-language analyst you can query in real time.

---

## Architecture overview
Pacifica Live Feeds (prices, orderbook, funding, liquidations)
↓
6-Agent Research Team (via MCP)
├── Market Agent → price action, trends, key levels
├── Funding Agent → funding rates, perp market skew
├── Liquidation Agent → short/long liquidation clusters
├── Sentiment Agent → Elfa smart-money scores
├── Narrative Agent → social/narrative signals
└── Orderbook Agent → depth, imbalances, walls
↓
External Analytics Layer
├── altFINS API → trend, momentum, signals, conviction
└── Tavily Web Search → live news headlines + top themes
↓
Signal Engine → BUY / SELL / HOLD + confidence
↓
NeMo/Claude Analyst → plain-language explanation via MCP
↓
Dashboard + Telegram Alerts

text

---

## Features

### Core signal engine
- **6-agent team** — each agent (market, funding, liquidation, sentiment, narrative, orderbook) contributes a Bullish/Neutral/Bearish view independently before the meta signal is decided
- **Real Pacifica data only** — 100% live feeds from Pacifica's perp DEX, no mocks or placeholders
- **Async parallel context builder** — all agents + external data fetched in parallel using `asyncio.gather` for minimal latency
- **Stable signal shapes** — every route returns predictable, versioned JSON so frontend and analyst never break on missing fields

### External analytics layer
- **altFINS API** — pulls structured short/medium/long-term trends, RSI, MACD, volume score, ATR, and altFINS signal feed per symbol; includes a conviction rating (aligned / conflicted / low / unknown) that tells you whether altFINS agrees with the internal signal
- **Elfa sentiment scores** — smart-money account activity and positioning data surfaced as a per-symbol sentiment signal
- **Tavily-powered news** — per-symbol real-time web search summarized into top themes (ETF inflows, regulation, macro, liquidations, hack) and 2–3 latest headlines with source and timestamp

### Signal quality & evaluation
- **30-day backtest** — every symbol carries a win rate, average PnL %, and trade count from historical signal replay
- **Session accuracy** — real-time counter of how many signals have been correct today, with the last signal direction and outcome shown on the dashboard
- **altFINS vs internal alignment** — explicit `alignment_with_signal` field showing whether external data reinforces or conflicts with the internal decision

### Analyst Q&A
- **NeMo/Claude analyst** — MCP-powered analyst that receives the full context (all 6 agents + signal engine + altFINS + Elfa + Tavily + backtest + session accuracy) and answers any market question in plain language
- **Model routing** — uses a fast Haiku-class model for normal questions and escalates to a Sonnet-class model only when the question is clearly complex or the answer quality is low, keeping costs minimal
- **Safe fallback** — if external models are unavailable, the analyst returns a structured fallback message instead of crashing the route

### Real-time Telegram alerts
- **Per-symbol subscriptions** — subscribe any symbol with `BUY`, `SELL`, or `BUY_OR_SELL` trigger conditions
- **Signal-flip detection** — backend watches for state changes and fires alerts only when a symbol transitions into the subscribed trigger state
- **Rich alert messages** — each Telegram message includes symbol, current signal decision, altFINS view, and top news themes for full context
- **Test endpoint** — `/apialert/test/{symbol}` lets you send an immediate test alert to verify your setup at any time

### Dashboard
- Global symbol selector across all Pacifica markets
- Primary signal strip: price, decision, confidence, altFINS + news summary
- 6-agent pills with individual Bullish/Neutral/Bearish + reason per agent
- altFINS block: short/medium/long trend, conviction, signal count
- News block: top themes as chips + latest headlines
- Backtest + session accuracy panel
- Claude/NeMo analyst Q&A panel
- Telegram alerts panel with toggle, trigger selector, and "Send test alert" button

### Developer / debug
- `/api/debug/context/{symbol}` — returns the full enriched context JSON for any symbol, including all agents, signal engine, altFINS, news, backtest, and session accuracy, useful for verifying data correctness
- Multi-symbol robustness: unknown or low-liquidity symbols degrade safely with `altfins.available = false` and `news_context.available = false` instead of crashing

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | FastAPI (Python) |
| Agent orchestration | MCP (Model Context Protocol) |
| LLM analyst | NeMo / Claude via Amazon Bedrock |
| External analytics | altFINS Crypto Data API |
| Sentiment | Elfa API |
| News search | Tavily Search API |
| Alerts | Telegram Bot API |
| Market data | Pacifica REST/WS feeds |
| Deployment | Vercel |

---

## API endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/signal/{symbol}` | Full enriched signal for one symbol |
| GET | `/api/signals/all` | All markets with enriched context |
| POST | `/api/ask` | Analyst Q&A (NeMo/Claude powered) |
| GET | `/api/backtest/{symbol}` | 30-day backtest stats |
| GET | `/api/accuracy` | Session accuracy for all symbols |
| GET | `/api/markets` | List of supported markets |
| GET | `/api/chart/{symbol}` | Kline/candle data |
| GET | `/api/health` | Uptime and health check |
| POST | `/apialert/subscribe` | Subscribe to Telegram alerts |
| GET | `/apialert/test/{symbol}` | Send a test Telegram alert |
| GET | `/api/debug/context/{symbol}` | Full enriched context dump (dev only) |

---

## Environment variables

```env
# Pacifica exchange
PACIFICA_BASE_URL=
PACIFICA_TESTNET_URL=
USE_TESTNET=false

# altFINS
ALFNS_API_KEY=

# Tavily
TAVILY_API_KEY=

# Elfa
ELFA_API_KEY=

# Telegram alerts
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Amazon Bedrock (Claude analyst)
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
BEDROCK_REGION=us-east-1
CLAUDE_MODEL_TIER=haiku_then_sonnet
BEDROCK_HAIKU_PROFILE_ID=
BEDROCK_SONNET_PROFILE_ID=
```

---

## Getting started

```bash
# 1. Clone the repo
git clone <your-repo-url>
cd pacifica-edge

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy and fill env vars
cp .env.example .env

# 4. Run the backend
uvicorn main:app --reload --port 8000

# 5. Test a signal
curl http://localhost:8000/api/signal/BTC-USDC

# 6. Test a debug context dump
curl http://localhost:8000/api/debug/context/ETH-USDC
```

---

## Alert setup (Telegram)

1. Create a bot via `@BotFather` on Telegram and copy the bot token.
2. Send a message to your bot, then open:  
   `https://api.telegram.org/bot<TOKEN>/getUpdates`  
   and find your `chat.id`.
3. Add both to `.env` as `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
4. Subscribe to alerts:

```bash
curl -X POST http://localhost:8000/apialert/subscribe \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BTC-USDC", "trigger_on": "BUY_OR_SELL"}'
```

5. Test immediately:

```bash
curl http://localhost:8000/apialert/test/BTC-USDC
```

---

## Deployment (Vercel)

1. Push the repo to GitHub.
2. Go to [vercel.com](https://vercel.com) → New Project → import your repo.
3. Set the **Framework Preset** to `Other`.
4. Add a `vercel.json` at the root:

```json
{
  "builds": [
    {
      "src": "main.py",
      "use": "@vercel/python"
    }
  ],
  "routes": [
    {
      "src": "/(.*)",
      "dest": "main.py"
    }
  ]
}
```

5. In Vercel → Settings → Environment Variables, add all env vars from `.env.example`.
6. Deploy — Vercel will install `requirements.txt` and serve the FastAPI app.

---

## Vision

Pacios is the beginning of a **full crypto research desk for Pacifica** — a team of agents and tools that watches every market 24/7, explains what's happening, stress-tests ideas using real history, and alerts you the moment the thesis changes. The goal is for a small trader or team to operate with the insight and discipline of a much larger professional desk, powered entirely by Pacifica's on-chain data and AI.

---

## License

MIT
