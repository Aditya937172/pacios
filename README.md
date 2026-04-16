text
# PacificaEdge (Pacios)
> A multi-agent crypto research desk built on top of Pacifica's perpetual DEX.

Pacios turns scattered real-time Pacifica market data into a single, explainable BUY/SELL/HOLD signal per market — powered by 6 AI agents, altFINS analytics, Elfa sentiment, Tavily news, and a live Telegram alert system.

---

## What it solves

Most crypto traders are drowning in raw feeds. Pacios acts like a full research team watching every market 24/7 and distilling what matters into one clear, explainable signal — with full context, backtest evidence, and a natural-language analyst you can query.

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

- **6-agent signal engine** — each agent contributes a view (Bullish / Neutral / Bearish) before the meta signal is decided
- **100% real Pacifica data** — no mock feeds, no placeholders; live prices, orderbook, funding, and liquidations
- **altFINS integration** — external short/medium/long-term trends, momentum scores, conviction layer per symbol
- **Elfa sentiment** — smart-money account scores and positioning signals
- **Tavily-powered news** — per-symbol web search summarized into headlines and top themes (ETF inflows, regulation, macro, etc.)
- **Backtesting + session accuracy** — every signal is backed by 30-day performance stats and intraday accuracy
- **NeMo/Claude analyst** — full MCP-driven analyst that explains the signal in plain language and answers any market question
- **Telegram alert system** — subscribe per symbol, choose BUY/SELL/BUY_OR_SELL trigger, get a ping when the signal flips
- **Debug context route** — `/api/debug/context/{symbol}` exposes the full enriched context for any symbol for inspection
- **Multi-symbol support** — BTC-USDC, ETH-USDC, SOL-USDC, BTC-PERP, and any derivable symbol; unknown symbols degrade safely

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
| Deployment | Railway |

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

## Deployment (Railway)

1. Push the repo to GitHub.
2. Create a new Railway service, connect your repo.
3. In Railway → Variables, add all env vars from `.env.example`.
4. Deploy; Railway detects Python and runs `uvicorn main:app --host 0.0.0.0 --port $PORT`.

---

## Vision

Pacios is the beginning of a **full crypto research desk for Pacifica** — a team of agents and tools that watches every market 24/7, explains what's happening, stress-tests ideas using real history, and alerts you the moment the thesis changes. The goal is for a small trader or team to operate with the insight and discipline of a much larger professional desk, powered entirely by Pacifica's on-chain data and AI.

---

## License

MIT
