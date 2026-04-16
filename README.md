# PacificaEdge

PacificaEdge is a crypto intelligence terminal built with a vanilla HTML/CSS/JavaScript frontend and a FastAPI backend. It combines market structure, funding, liquidations, sentiment, narrative, and orderbook analysis into a single multi-agent dashboard for `BTC-USDC`, `ETH-USDC`, and `SOL-USDC`.

## Stack

- Frontend: static dashboard in [ui/index.html](ui/index.html)
- Backend: FastAPI app in [pacifica-edge/main.py](pacifica-edge/main.py)
- Runtime: Python 3.12+ recommended
- Deployment: Vercel serverless Python entrypoint in [api/index.py](api/index.py)

## Features

- Multi-agent signal engine with a frontdesk summary
- Live dashboard endpoints for overview, per-market analysis, and agent workspaces
- Orderbook, funding, liquidation, sentiment, and narrative reporting
- Chat-style analyst endpoints for market and agent questions
- Static dashboard served directly by the FastAPI app

## Project Structure

```text
.
|-- api/
|   `-- index.py
|-- pacifica-edge/
|   |-- agents/
|   |-- services/
|   |-- main.py
|   `-- requirements.txt
|-- ui/
|   |-- index.html
|   |-- script.js
|   `-- style.css
|-- start_dashboard.py
`-- vercel.json
```

## Local Run

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Add environment variables to `.env` as needed.
4. Start the app:

```bash
python start_dashboard.py
```

The local dashboard runs at `http://127.0.0.1:8000`.

## Environment Variables

These integrations are optional. When some keys are missing, parts of the app degrade gracefully instead of crashing.

- `BEDROCK_API_KEY`
- `BEDROCK_MODEL_ID`
- `BEDROCK_REGION`
- `NEMO_API_KEY`
- `NEMO_API_BASE_URL`
- `NEMO_MODEL_NAME`
- `ALTFNS_API_KEY`
- `ALTFINS_API_KEY`
- `ALTFINS_API_BASE_URL`
- `TAVILY_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `PACIFICA_BASE_URL`
- `PACIFICA_TESTNET_URL`
- `USE_TESTNET`
- `PREWARM_DASHBOARD`

## Deployment

This repo is configured for Vercel with a single deployment that serves both the FastAPI backend and the static dashboard frontend. All requests are routed through the FastAPI entrypoint, which exposes the API and serves the UI assets.
