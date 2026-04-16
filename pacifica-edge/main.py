"""FastAPI entrypoint for PacificaEdge."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr

from agents.current_agent import CurrentAgent
from agents.funding_agent import FundingAgent
from agents.liquidation_agent import LiquidationAgent
from agents.market_agent import MarketAgent
from agents.narrative_agent import NarrativeAgent
from agents.orderbook_agent import OrderBookAgent
from agents.signal_agent import SignalAgent
from agents.sentiment_agent import SentimentAgent
from services.accuracy_tracker import AccuracyTracker
from services.alert_subscriptions import (
    TelegramAlertSubscription,
    add_or_update_telegram_subscription,
    get_telegram_subscriptions_for_symbol,
)
from services.altfins import AltFinsClient
from services.backtest_engine import BacktestEngine
from services.elfa import ElfaClient
from services.narrator import SignalNarrator
from services.nemo_llm import NeMoClient
from services.pacifica import PacificaClient
from services.telegram_alerts import (
    get_default_telegram_bot_token,
    get_default_telegram_chat_id,
    send_telegram_signal_alert,
)

ROOT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ROOT_ENV_PATH)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

VALID_SYMBOLS: Final[set[str]] = {"BTC-USDC", "ETH-USDC", "SOL-USDC"}
ASK_DEFAULT_SYMBOLS: Final[tuple[str, ...]] = ("BTC-USDC", "ETH-USDC", "SOL-USDC")
BASE_SYMBOL_MAP: Final[dict[str, str]] = {
    "BTC": "BTC-USDC",
    "ETH": "ETH-USDC",
    "SOL": "SOL-USDC",
}
AGENT_LABELS: Final[dict[str, str]] = {
    "frontdesk": "Frontdesk Agent",
    "market": "Market Agent",
    "funding": "Funding Agent",
    "liquidation": "Liquidation Agent",
    "sentiment": "Sentiment Agent",
    "narrative": "Narrative Agent",
    "orderbook": "Orderbook Agent",
}
APP_START_TIME = time.time()
alert_subscriptions: list[dict[str, Any]] = []
last_signal_state: dict[str, str] = {}
UI_DIR = Path(__file__).resolve().parent.parent / "ui"
CACHE_TTL_SIGNAL_SECONDS: Final[float] = 20.0
CACHE_TTL_SIGNAL_DEGRADED_SECONDS: Final[float] = 8.0
CACHE_TTL_DASHBOARD_SECONDS: Final[float] = 20.0
CACHE_TTL_CHART_SECONDS: Final[float] = 60.0
CACHE_TTL_OVERVIEW_SECONDS: Final[float] = 20.0
dashboard_cache: dict[str, tuple[float, Any]] = {}
dashboard_inflight: dict[str, asyncio.Task[Any]] = {}
last_good_dashboard_markets: dict[str, dict[str, Any]] = {}

app = FastAPI(title="PacificaEdge")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a degraded JSON payload instead of surfacing raw 500 crashes."""
    print(f"[ERROR] Unhandled exception on {request.url.path}: {exc}")
    return JSONResponse(status_code=500, content={"error": str(exc), "status": "degraded"})

pacifica_client = PacificaClient()
market_agent = MarketAgent(pacifica_client=pacifica_client)
funding_agent = FundingAgent(pacifica_client=pacifica_client)
liquidation_agent = LiquidationAgent(pacifica_client=pacifica_client)
orderbook_agent = OrderBookAgent(pacifica_client=pacifica_client)
try:
    elfa_client = ElfaClient()
except ValueError as exc:
    logger.warning("Elfa client startup failed: %s", exc)
    elfa_client = None
try:
    nemo_client = NeMoClient()
except ValueError as exc:
    logger.warning("NeMo client startup failed: %s", exc)
    nemo_client = None
try:
    altfins_client = AltFinsClient()
except ValueError as exc:
    logger.warning("altFINS client startup failed: %s", exc)
    altfins_client = None
sentiment_agent = SentimentAgent(elfa_client=elfa_client)
narrative_agent = NarrativeAgent(elfa_client=elfa_client, nemo_client=nemo_client)
current_agent = CurrentAgent(nemo_client=nemo_client)
signal_agent = SignalAgent(
    market_agent=market_agent,
    funding_agent=funding_agent,
    liquidation_agent=liquidation_agent,
    sentiment_agent=sentiment_agent,
    narrative_agent=narrative_agent,
    orderbook_agent=orderbook_agent,
)
narrator = SignalNarrator(nemo_client=nemo_client)
backtest_engine = BacktestEngine(pacifica_client=pacifica_client, signal_agent=signal_agent)
accuracy_tracker = AccuracyTracker(pacifica_client=pacifica_client)


@app.on_event("startup")
async def warm_dashboard_on_startup() -> None:
    """Kick off a background cache warm-up for the dashboard routes."""
    prewarm_flag = os.getenv("PREWARM_DASHBOARD")
    if prewarm_flag is None:
        should_prewarm = os.getenv("VERCEL", "").strip().lower() not in {"1", "true"}
    else:
        should_prewarm = prewarm_flag.strip().lower() != "false"
    if should_prewarm:
        asyncio.create_task(prewarm_dashboard_cache())
    else:
        logger.info("Dashboard cache prewarm skipped")


class AskRequest(BaseModel):
    """Request body for conversational analyst questions."""

    question: str


class AgentChatRequest(BaseModel):
    """Request body for agent-specific dashboard chat."""

    symbol: str
    agent: str
    question: str


class AlertSubscription(BaseModel):
    """Request body for storing alert subscription preferences."""

    email: Optional[EmailStr] = None
    telegram_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    symbol: str
    trigger_on: str


class AlertSubscribeRequest(BaseModel):
    """Request body for Telegram-only alert subscriptions."""

    symbol: str
    trigger_on: str
    telegram_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None


def utc_timestamp() -> str:
    """Return the current UTC timestamp in ISO 8601 format."""
    return datetime.utcnow().isoformat() + "Z"


def get_cached_value(cache_key: str) -> Any | None:
    """Return a cached value when it is still fresh."""
    cached_entry = dashboard_cache.get(cache_key)
    if cached_entry is None:
        return None
    expires_at, value = cached_entry
    if expires_at > time.monotonic():
        return value
    dashboard_cache.pop(cache_key, None)
    return None


def set_cached_value(cache_key: str, value: Any, ttl_seconds: float) -> Any:
    """Store a value in the short-lived in-memory dashboard cache."""
    dashboard_cache[cache_key] = (time.monotonic() + ttl_seconds, value)
    return value


async def get_or_build_cached(
    cache_key: str,
    ttl_seconds: float,
    builder: Any,
) -> Any:
    """Deduplicate concurrent dashboard builds and cache the resolved result."""
    cached_value = get_cached_value(cache_key)
    if cached_value is not None:
        return cached_value

    inflight_task = dashboard_inflight.get(cache_key)
    if inflight_task is not None:
        return await inflight_task

    task = asyncio.create_task(builder())
    dashboard_inflight[cache_key] = task
    try:
        result = await task
        return set_cached_value(cache_key, result, ttl_seconds)
    finally:
        dashboard_inflight.pop(cache_key, None)


def validate_symbol(symbol: str) -> str:
    """Validate and normalize a supported symbol."""
    normalized_symbol = symbol.upper()
    if normalized_symbol not in VALID_SYMBOLS:
        raise HTTPException(status_code=400, detail="Invalid symbol")
    return normalized_symbol


def normalize_symbol(symbol: str) -> str:
    """Normalize a symbol without restricting it to the supported-market set."""
    normalized_symbol = symbol.upper().strip()
    if not normalized_symbol:
        raise HTTPException(status_code=400, detail="Invalid symbol")
    return normalized_symbol


def extract_question_symbols(question: str) -> list[str]:
    """Extract explicit market symbols referenced in a trader question."""
    normalized_question = question.upper()
    extracted_symbols = re.findall(r"\b[A-Z0-9]+-(?:USDC|PERP)\b", normalized_question)
    derived_symbols = [
        mapped_symbol
        for base_symbol, mapped_symbol in BASE_SYMBOL_MAP.items()
        if re.search(rf"\b{base_symbol}\b", normalized_question)
    ]
    prioritized_symbols = [*extracted_symbols, *derived_symbols]
    ordered_symbols = list(dict.fromkeys(prioritized_symbols)) if prioritized_symbols else list(ASK_DEFAULT_SYMBOLS)
    return [normalize_symbol(symbol) for symbol in ordered_symbols]


async def get_altfins_analytics(symbol: str) -> dict[str, Any]:
    """Return altFINS analytics for a PacificaEdge market symbol."""
    base_symbol = symbol.split("-")[0].upper()
    if altfins_client is None:
        return build_altfins_derived_view(symbol, reason="altFINS client unavailable")
    return await altfins_client.get_asset_analytics(base_symbol)


def build_altfins_derived_view(
    symbol: str,
    signal_payload: dict[str, Any] | None = None,
    reason: str = "altFINS upstream unavailable",
) -> dict[str, Any]:
    """Return a PacificaEdge-derived altFINS confirmation view when direct coverage is unavailable."""
    normalized_symbol = normalize_symbol(symbol)
    base_symbol = normalized_symbol.split("-")[0].upper()
    signal_payload = signal_payload if isinstance(signal_payload, dict) else {}
    final_signal = str(signal_payload.get("final_signal", "HOLD")).upper()
    score = safe_float(signal_payload.get("score"))
    confidence_pct = safe_float(signal_payload.get("confidence_pct"))
    agents = signal_payload.get("agents", {}) if isinstance(signal_payload.get("agents"), dict) else {}
    market_agent_payload = agents.get("market", {}) if isinstance(agents.get("market"), dict) else {}
    change_24h = safe_float(market_agent_payload.get("change_24h"))
    volume_24h = safe_float(market_agent_payload.get("volume_24h"))
    open_interest = safe_float(market_agent_payload.get("open_interest"))

    if final_signal == "BUY":
        direction = "BULLISH"
        short_trend = "Constructive (derived)"
    elif final_signal == "SELL":
        direction = "BEARISH"
        short_trend = "Weak (derived)"
    else:
        direction = "NEUTRAL"
        short_trend = "Balanced (derived)"

    bullish_count = 1 if direction == "BULLISH" else 0
    bearish_count = 1 if direction == "BEARISH" else 0
    fallback_signal = {
        "name": "PacificaEdge derived altFINS confirmation",
        "direction": direction,
        "timeframe": "live derived view",
        "status": "derived_from_live_market_data",
        "notes": (
            "altFINS did not return usable analytics, so this view is derived from "
            "PacificaEdge signal, price change, open interest, and volume."
        ),
        "timestamp": utc_timestamp(),
    }
    if direction == "NEUTRAL":
        signals: list[dict[str, Any]] = []
    else:
        signals = [fallback_signal]

    return {
        "available": False,
        "derived": True,
        "source_status": "PacificaEdge-derived confirmation view",
        "symbol": base_symbol,
        "trend": {
            "short_term": short_trend,
            "medium_term": "Waiting for direct altFINS confirmation",
            "long_term": "Waiting for direct altFINS confirmation",
        },
        "momentum": {
            "rsi": None,
            "macd": None,
            "macd_signal": None,
            "momentum_score": round(50 + max(min(score, 3), -3) * 8, 1),
        },
        "volatility": {
            "atr": None,
            "volatility_score": None,
        },
        "volume": {
            "volume": volume_24h if volume_24h > 0 else None,
            "volume_score": None,
            "volume_trend": "participation rising" if open_interest > 0 and change_24h > 0 else "needs confirmation",
        },
        "on_chain": {
            "tvl": None,
            "valuation_score": None,
            "profit_ratio": None,
        },
        "technical_analysis": {
            "near_term_outlook": f"{final_signal} bias from PacificaEdge, not direct altFINS",
            "pattern_type": "derived_confirmation",
        },
        "signals": signals,
        "bullish_signal_count": bullish_count,
        "bearish_signal_count": bearish_count,
        "alignment_with_signal": "derived_match" if direction in {"BULLISH", "BEARISH"} else "derived_neutral",
        "altfins_conviction": "derived",
        "summary_block": {
            "htf_trend": f"{short_trend} / waiting for direct altFINS / waiting for direct altFINS",
            "signals_overview": f"{bullish_count} derived bullish, {bearish_count} derived bearish",
            "altfins_view": (
                f"PacificaEdge-derived confirmation: {final_signal} bias from live market structure "
                f"({confidence_pct:.0f}% confidence)."
            ),
        },
        "error": reason,
        "timestamp": utc_timestamp(),
    }


def build_signal_engine_context(signal_result: dict[str, Any]) -> dict[str, Any]:
    """Build the prompt-ready signal-engine context block from a signal result."""
    return {
        "final_signal": signal_result.get("final_signal", "HOLD"),
        "score": signal_result.get("score", 0),
        "confidence_pct": signal_result.get("confidence_pct", 0.0),
        "macro_alert": signal_result.get("macro_alert"),
        "timestamp": signal_result.get("timestamp", utc_timestamp()),
        "agents": signal_result.get("agents", {}),
    }


def format_analysis_context(
    signal_result: dict[str, Any],
    backtest: dict[str, Any],
    session_accuracy: dict[str, Any],
    altfins: dict[str, Any],
    news_context: dict[str, Any],
) -> dict[str, Any]:
    """Build a prompt-ready analysis context for the analyst layer."""
    agents = signal_result.get("agents", {})
    return {
        "market_agent": agents.get("market", {}),
        "funding_agent": agents.get("funding", {}),
        "liquidation_agent": agents.get("liquidation", {}),
        "sentiment_agent": agents.get("sentiment", {}),
        "narrative_agent": agents.get("narrative", {}),
        "orderbook_agent": agents.get("orderbook", {}),
        "signal_engine": build_signal_engine_context(signal_result),
        "backtest": backtest,
        "session_accuracy": session_accuracy,
        "altfins": altfins,
        "news_context": news_context,
    }


async def build_analysis_context(symbol: str) -> dict[str, Any]:
    """Build the unified analyst context for a symbol."""
    enriched_signal_result = await build_enriched_signal_result(symbol)
    return format_analysis_context(
        signal_result=enriched_signal_result,
        backtest=enriched_signal_result.get("backtest", {}),
        session_accuracy=enriched_signal_result.get("session_accuracy", {}),
        altfins=enriched_signal_result.get("altfins", {}),
        news_context=enriched_signal_result.get("news_context", {}),
    )


async def build_enriched_signal_result_uncached(symbol: str) -> dict[str, Any]:
    """Build a signal result enriched with backtest, session accuracy, and altFINS analytics."""
    signal_task = asyncio.create_task(signal_agent.analyze(symbol))
    altfins_task = asyncio.create_task(get_altfins_analytics(symbol))

    signal_result = await signal_task
    signal_result = await repair_signal_payload_from_prices(symbol, signal_result)
    narrative_summary = (
        signal_result.get("agents", {})
        .get("narrative", {})
        .get("narrative_summary")
    )
    backtest_task = asyncio.create_task(
        backtest_engine.backtest_current_pattern(symbol, current_signal=signal_result)
    )
    news_task = asyncio.create_task(current_agent.run(symbol, narrative_summary, fast=True))

    backtest_result, altfins_result, news_context = await asyncio.gather(
        asyncio.wait_for(backtest_task, timeout=5.0),
        asyncio.wait_for(altfins_task, timeout=2.5),
        asyncio.wait_for(news_task, timeout=3.5),
        return_exceptions=True,
    )
    if isinstance(backtest_result, Exception):
        logger.warning("Backtest timed out or failed for %s: %s", symbol, backtest_result)
        backtest_result = {
            "pattern_matches": 0,
            "correct_predictions": 0,
            "accuracy_pct": 0.0,
            "avg_move_pct": 0.0,
            "backtest_label": "Pattern sample is still building from recent market history.",
        }
    if isinstance(altfins_result, Exception):
        logger.warning("altFINS enrichment timed out or failed for %s: %s", symbol, altfins_result)
        altfins_result = build_altfins_derived_view(symbol, signal_result, reason="altFINS timeout")
    if isinstance(news_context, Exception):
        logger.warning("News enrichment timed out or failed for %s: %s", symbol, news_context)
        news_context = {
            "available": False,
            "symbol": symbol.split("-")[0].upper(),
            "headlines": [],
            "top_themes": [],
        }
    if altfins_client is not None:
        altfins_result = altfins_client.summarize_for_signal(
            altfins=altfins_result,
            final_signal=str(signal_result.get("final_signal", "HOLD")),
        )
    if not bool(altfins_result.get("available")) and not bool(altfins_result.get("derived")):
        altfins_result = build_altfins_derived_view(
            symbol,
            signal_result,
            reason=str(altfins_result.get("error") or "altFINS returned no usable analytics"),
        )
    session_accuracy = accuracy_tracker.get_stats()
    signal_result["backtest"] = backtest_result
    signal_result["session_accuracy"] = session_accuracy
    signal_result["altfins"] = altfins_result
    signal_result["news_context"] = news_context
    signal_result["analysis_context"] = format_analysis_context(
        signal_result=signal_result,
        backtest=backtest_result,
        session_accuracy=session_accuracy,
        altfins=altfins_result,
        news_context=news_context,
    )
    return signal_result


async def build_enriched_signal_result(symbol: str) -> dict[str, Any]:
    """Return a cached enriched signal result for a symbol."""
    normalized_symbol = normalize_symbol(symbol)
    cache_key = f"signal:{normalized_symbol}"
    cached_value = get_cached_value(cache_key)
    if isinstance(cached_value, dict) and not signal_payload_is_degraded(cached_value):
        return cached_value
    if signal_payload_is_degraded(cached_value if isinstance(cached_value, dict) else {}):
        dashboard_cache.pop(cache_key, None)

    inflight_task = dashboard_inflight.get(cache_key)
    if inflight_task is not None:
        return await inflight_task

    async def _builder() -> dict[str, Any]:
        first_pass = await build_enriched_signal_result_uncached(normalized_symbol)
        if not signal_payload_is_degraded(first_pass):
            return first_pass

        logger.warning("Signal payload degraded for %s; retrying once", normalized_symbol)
        await asyncio.sleep(0.1)
        second_pass = await build_enriched_signal_result_uncached(normalized_symbol)
        return second_pass

    task = asyncio.create_task(_builder())
    dashboard_inflight[cache_key] = task
    try:
        result = await task
        ttl_seconds = (
            CACHE_TTL_SIGNAL_DEGRADED_SECONDS
            if signal_payload_is_degraded(result)
            else CACHE_TTL_SIGNAL_SECONDS
        )
        return set_cached_value(cache_key, result, ttl_seconds)
    finally:
        dashboard_inflight.pop(cache_key, None)


async def build_cached_news_context(
    symbol: str,
    narrative_summary: str | None = None,
) -> dict[str, Any]:
    """Return a cached current-affairs block for ad-hoc dashboard chat use."""
    normalized_symbol = normalize_symbol(symbol)
    narrative_key = (narrative_summary or "").strip().lower()[:40]
    return await get_or_build_cached(
        cache_key=f"news:{normalized_symbol}:{narrative_key}",
        ttl_seconds=CACHE_TTL_OVERVIEW_SECONDS,
        builder=lambda: current_agent.run(normalized_symbol, narrative_summary, fast=True),
    )


async def build_all_signals_view() -> dict[str, Any]:
    """Build enriched signal payloads for the three core demo markets."""
    btc_signal, eth_signal, sol_signal = await asyncio.gather(
        build_enriched_signal_result("BTC-USDC"),
        build_enriched_signal_result("ETH-USDC"),
        build_enriched_signal_result("SOL-USDC"),
    )
    markets = {
        "BTC-USDC": btc_signal,
        "ETH-USDC": eth_signal,
        "SOL-USDC": sol_signal,
    }
    return {
        "markets": markets,
        "macro_alert": signal_agent._macro_alert(markets),
        "timestamp": utc_timestamp(),
    }


def validate_agent_key(agent_key: str) -> str:
    """Validate and normalize a supported dashboard agent key."""
    normalized_agent = agent_key.strip().lower()
    if normalized_agent not in AGENT_LABELS:
        raise HTTPException(status_code=400, detail="Invalid agent")
    return normalized_agent


def format_percent(value: Any, digits: int = 1) -> str:
    """Format a numeric percentage-like value for dashboard display."""
    try:
        return f"{float(value):.{digits}f}%"
    except (TypeError, ValueError):
        return "n/a"


def format_compact_number(value: Any) -> str:
    """Format a numeric value into a compact dashboard string."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "n/a"
    absolute = abs(numeric)
    if absolute >= 1_000_000_000:
        return f"${numeric / 1_000_000_000:.2f}B"
    if absolute >= 1_000_000:
        return f"${numeric / 1_000_000:.2f}M"
    if absolute >= 1_000:
        return f"${numeric / 1_000:.2f}K"
    return f"${numeric:,.2f}"


def format_price(value: Any) -> str:
    """Format a numeric price value."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if numeric >= 1000:
        return f"${numeric:,.0f}"
    if numeric >= 1:
        return f"${numeric:,.2f}"
    return f"${numeric:,.4f}"


def safe_float(value: Any, default: float = 0.0) -> float:
    """Convert a value to float without raising."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compact_text(value: Any, limit: int = 180) -> str:
    """Convert a value to a short single-line string."""
    text = " ".join(str(value or "").replace("\n", " ").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def signal_payload_is_degraded(signal_payload: dict[str, Any]) -> bool:
    """Return whether a signal payload is too broken to trust for normal caching."""
    if not isinstance(signal_payload, dict):
        return True
    agents = signal_payload.get("agents", {})
    market_payload = agents.get("market", {}) if isinstance(agents, dict) else {}
    price = safe_float(market_payload.get("price"))
    open_interest = safe_float(market_payload.get("open_interest"))
    volume_24h = safe_float(market_payload.get("volume_24h"))
    has_market_error = bool(market_payload.get("error"))
    has_signal_error = bool(signal_payload.get("error"))
    return (
        has_market_error
        or has_signal_error
        or (price <= 0 and open_interest <= 0 and volume_24h <= 0)
    )


def find_price_row(prices_payload: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    """Find one row for a symbol inside the Pacifica prices payload."""
    base_symbol = pacifica_client._to_base_symbol(symbol)
    data = prices_payload.get("data", []) if isinstance(prices_payload, dict) else []
    if not isinstance(data, list):
        return None
    for item in data:
        if not isinstance(item, dict):
            continue
        if str(item.get("symbol", "")).upper().strip() == base_symbol:
            return item
    return None


def recalculate_signal_summary(signal_payload: dict[str, Any]) -> dict[str, Any]:
    """Recompute score, final signal, confidence, and reasoning after a repair."""
    agents = signal_payload.get("agents", {})
    if not isinstance(agents, dict):
        return signal_payload
    score = signal_agent._calculate_score(agents)
    signal_payload["score"] = score
    signal_payload["final_signal"] = signal_agent._final_signal(score)
    signal_payload["confidence_pct"] = (abs(score) / 6) * 100.0
    signal_payload["reasoning"] = signal_agent._build_reasoning(agents)
    return signal_payload


async def repair_signal_payload_from_prices(symbol: str, signal_payload: dict[str, Any]) -> dict[str, Any]:
    """Repair a degraded market agent using the live prices board when possible."""
    if not signal_payload_is_degraded(signal_payload):
        return signal_payload

    prices_payload = await pacifica_client.get_prices()
    if not isinstance(prices_payload, dict) or prices_payload.get("error"):
        return signal_payload

    price_row = find_price_row(prices_payload, symbol)
    if not isinstance(price_row, dict):
        return signal_payload

    agents = signal_payload.setdefault("agents", {})
    if not isinstance(agents, dict):
        return signal_payload
    market_payload = agents.setdefault("market", {})
    if not isinstance(market_payload, dict):
        market_payload = {}
        agents["market"] = market_payload

    mark_price = safe_float(price_row.get("mark"))
    yesterday_price = safe_float(price_row.get("yesterday_price"))
    volume_24h = safe_float(price_row.get("volume_24h"))
    open_interest = safe_float(price_row.get("open_interest"))
    change_24h = 0.0
    if yesterday_price > 0 and mark_price > 0:
        change_24h = ((mark_price - yesterday_price) / yesterday_price) * 100.0

    if mark_price <= 0:
        return signal_payload

    market_payload["price"] = mark_price
    market_payload["change_24h"] = change_24h
    market_payload["volume_24h"] = volume_24h
    market_payload["open_interest"] = open_interest
    if open_interest > 0 and change_24h > 0.25:
        market_payload["trend"] = "BULLISH"
        market_payload["signal"] = "BULLISH"
    elif open_interest > 0 and change_24h < -0.25:
        market_payload["trend"] = "BEARISH"
        market_payload["signal"] = "BEARISH"
    else:
        market_payload["trend"] = market_payload.get("trend", "NEUTRAL") or "NEUTRAL"
        market_payload["signal"] = market_payload.get("signal", market_payload["trend"]) or "NEUTRAL"
    market_payload["repair_note"] = "Recovered from Pacifica price board fallback"
    market_payload.pop("error", None)
    return recalculate_signal_summary(signal_payload)


def extract_agent_verdict(agent_key: str, agent_payload: dict[str, Any]) -> str:
    """Extract the agent verdict from a normalized agent payload."""
    if agent_key == "market":
        return str(agent_payload.get("trend") or agent_payload.get("signal") or "NEUTRAL")
    return str(agent_payload.get("signal") or "NEUTRAL")


def extract_bull_bear_neutral_counts(agents: dict[str, Any]) -> dict[str, int]:
    """Count bullish, bearish, and neutral specialist verdicts."""
    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    for agent_key, agent_payload in agents.items():
        if agent_key == "frontdesk":
            continue
        verdict = extract_agent_verdict(str(agent_key), agent_payload if isinstance(agent_payload, dict) else {})
        normalized = verdict.upper()
        if normalized == "BULLISH":
            counts["bullish"] += 1
        elif normalized == "BEARISH":
            counts["bearish"] += 1
        else:
            counts["neutral"] += 1
    return counts


def build_agent_key_metric(agent_key: str, agent_payload: dict[str, Any]) -> tuple[str, str]:
    """Return the most important current metric for an agent workspace."""
    if agent_key == "market":
        return "Price", format_price(agent_payload.get("price"))
    if agent_key == "funding":
        return "Funding APY", format_percent(agent_payload.get("annualized_rate_pct"), 2)
    if agent_key == "liquidation":
        return "Total liquidations", format_compact_number(agent_payload.get("total_liquidations_usd"))
    if agent_key == "sentiment":
        return "Sentiment score", format_percent(agent_payload.get("sentiment_score"), 1)
    if agent_key == "narrative":
        return "Narrative confidence", str(agent_payload.get("confidence", "LOW")).title()
    return "Bid imbalance", format_percent(float(agent_payload.get("imbalance_ratio", 0.0)) * 100.0, 1)


def build_agent_report_text(agent_key: str, agent_payload: dict[str, Any]) -> str:
    """Build a concise natural-language report for an agent payload."""
    if agent_key == "market":
        return (
            f"Price is {format_price(agent_payload.get('price'))} with {format_percent(agent_payload.get('change_24h'), 2)} 24h change, "
            f"open interest at {format_compact_number(agent_payload.get('open_interest'))}, and the market trend reading {agent_payload.get('trend', 'NEUTRAL')}."
        )
    if agent_key == "funding":
        return (
            f"Funding is running at {format_percent(agent_payload.get('annualized_rate_pct'), 2)} annualized and currently reads {agent_payload.get('signal', 'NEUTRAL')}. "
            f"{agent_payload.get('reason', '')}".strip()
        )
    if agent_key == "liquidation":
        return (
            f"Recent liquidations total {format_compact_number(agent_payload.get('total_liquidations_usd'))} with {agent_payload.get('dominant_side', 'BALANCED')} dominant. "
            f"{agent_payload.get('reason', '')}".strip()
        )
    if agent_key == "sentiment":
        rank = agent_payload.get("rank_in_trending")
        rank_text = f"ranked #{rank}" if rank is not None else "not currently ranked"
        return (
            f"Elfa sentiment is {format_percent(agent_payload.get('sentiment_score'), 1)} and {rank_text} with "
            f"{int(agent_payload.get('mention_count_24h', 0)):,} recent mentions. {agent_payload.get('reason', '')}".strip()
        )
    if agent_key == "narrative":
        return str(
            agent_payload.get("reason")
            or agent_payload.get("narrative_summary")
            or "Narrative context is limited right now."
        )
    return (
        f"Orderbook imbalance is {format_percent(float(agent_payload.get('imbalance_ratio', 0.0)) * 100.0, 1)} to bids. "
        f"{agent_payload.get('reason') or agent_payload.get('wall_alert') or 'No strong wall alert is active right now.'}"
    )


def build_agent_reasoning_details(agent_key: str, agent_payload: dict[str, Any]) -> dict[str, Any]:
    """Return agent-specific reasoning details for dashboard chat and debugging."""
    verdict = extract_agent_verdict(agent_key, agent_payload).upper()
    reason = str(agent_payload.get("reason") or "").strip()

    if agent_key == "market":
        price = format_price(agent_payload.get("price"))
        change_24h = format_percent(agent_payload.get("change_24h"), 2)
        open_interest = format_compact_number(agent_payload.get("open_interest"))
        thesis = (
            f"Market structure is {verdict.lower()} because price is {price}, the 24h move is {change_24h}, "
            f"and open interest is {open_interest}."
            if verdict in {"BULLISH", "BEARISH"}
            else f"Market structure is neutral because price is {price}, the 24h move is {change_24h}, and open interest is {open_interest}."
        )
        risk = (
            "If price loses the current level or open interest stops confirming, this trend read weakens."
            if verdict in {"BULLISH", "BEARISH"}
            else "Without stronger price expansion and cleaner participation, this agent stays neutral."
        )
        return {
            "thesis": thesis,
            "risk": risk,
            "evidence": [
                f"Price: {price}",
                f"24h change: {change_24h}",
                f"Open interest: {open_interest}",
                f"Trend: {agent_payload.get('trend', 'NEUTRAL')}",
            ],
        }

    if agent_key == "funding":
        funding_rate = float(agent_payload.get("funding_rate", 0.0) or 0.0)
        annualized = format_percent(agent_payload.get("annualized_rate_pct"), 2)
        next_rate = float(agent_payload.get("next_funding_rate", 0.0) or 0.0)
        thesis = reason or f"Funding reads {verdict.lower()} at {annualized} annualized."
        risk = (
            "If funding mean-reverts quickly, the crowding signal disappears."
            if abs(funding_rate) > 0
            else "Funding is near flat, so this agent has weak edge until crowding appears."
        )
        return {
            "thesis": thesis,
            "risk": risk,
            "evidence": [
                f"Funding rate: {funding_rate:.6f}",
                f"Annualized APY: {annualized}",
                f"Next funding rate: {next_rate:.6f}",
                f"Data source: {agent_payload.get('data_source', 'unknown')}",
            ],
        }

    if agent_key == "liquidation":
        total_liq = format_compact_number(agent_payload.get("total_liquidations_usd"))
        long_liq = format_compact_number(agent_payload.get("long_liquidations_usd"))
        short_liq = format_compact_number(agent_payload.get("short_liquidations_usd"))
        dominant_side = str(agent_payload.get("dominant_side", "BALANCED"))
        thesis = reason or f"Liquidation flow is {verdict.lower()} with {dominant_side.lower()} dominating."
        risk = (
            "If fresh forced-flow prints do not arrive, this remains weak confirmation only."
            if dominant_side == "BALANCED" or total_liq == "$0.00"
            else "Liquidation pressure can reverse fast, so this read needs a fresh follow-through print."
        )
        return {
            "thesis": thesis,
            "risk": risk,
            "evidence": [
                f"Total liquidations: {total_liq}",
                f"Long liquidations: {long_liq}",
                f"Short liquidations: {short_liq}",
                f"Dominant side: {dominant_side}",
            ],
        }

    if agent_key == "sentiment":
        score = format_percent(agent_payload.get("sentiment_score"), 1)
        mentions = int(agent_payload.get("mention_count_24h", 0) or 0)
        rank = agent_payload.get("rank_in_trending")
        thesis = reason or f"Social attention reads {verdict.lower()} with sentiment score at {score}."
        risk = (
            "Social coverage is thin, so this agent has low conviction until attention broadens."
            if "insufficient" in reason.lower() or mentions == 0
            else "If the token drops out of the trending set, this sentiment read weakens quickly."
        )
        return {
            "thesis": thesis,
            "risk": risk,
            "evidence": [
                f"Sentiment score: {score}",
                f"24h mentions: {mentions:,}",
                f"Trending rank: #{rank}" if rank is not None else "Trending rank: not ranked",
                f"Source: {agent_payload.get('powered_by', 'Elfa AI')}",
            ],
        }

    if agent_key == "narrative":
        confidence = str(agent_payload.get("confidence", "LOW")).title()
        bullish_hits = int(agent_payload.get("bullish_hits", 0) or 0)
        bearish_hits = int(agent_payload.get("bearish_hits", 0) or 0)
        summary = str(agent_payload.get("narrative_summary") or "No narrative summary available.").strip()
        thesis = reason or summary
        risk = (
            "Narrative coverage is thin, so this should stay a confirmation layer instead of the lead signal."
            if "insufficient" in thesis.lower() or confidence.lower() == "low"
            else "Narratives shift quickly; if the current catalyst fades, this edge disappears fast."
        )
        return {
            "thesis": thesis,
            "risk": risk,
            "evidence": [
                f"Confidence: {confidence}",
                f"Bullish narrative hits: {bullish_hits}",
                f"Bearish narrative hits: {bearish_hits}",
                f"Summary: {summary}",
            ],
        }

    imbalance = format_percent(float(agent_payload.get("imbalance_ratio", 0.0) or 0.0) * 100.0, 1)
    bid_total = format_compact_number(agent_payload.get("bid_total_usd"))
    ask_total = format_compact_number(agent_payload.get("ask_total_usd"))
    wall_alert = str(agent_payload.get("wall_alert") or "No one-sided wall alert.").strip()
    thesis = reason or f"Orderbook is {verdict.lower()} with bid imbalance at {imbalance}."
    if "unavailable" in thesis.lower():
        thesis = "The live orderbook snapshot is unavailable right now, so there are no trustworthy levels to call out."
    risk = (
        "If the strongest wall gets absorbed or imbalance collapses, this signal can reverse quickly."
        if verdict in {"BULLISH", "BEARISH"}
        else "The book is balanced enough that microstructure is not giving a clean edge yet."
    )
    return {
        "thesis": thesis,
        "risk": risk,
        "evidence": [
            f"Bid imbalance: {imbalance}",
            f"Bid depth: {bid_total}",
            f"Ask depth: {ask_total}",
            f"Wall alert: {wall_alert}",
        ],
    }


def build_agent_next_steps(agent_key: str, signal_payload: dict[str, Any]) -> list[str]:
    """Build concise next-step guidance for the selected agent."""
    agent_payload = signal_payload.get("agents", {}).get(agent_key, {})
    if agent_key == "market":
        return [
            f"Watch whether price holds above {format_price(agent_payload.get('price'))} on the next refresh window.",
            "Confirm that open interest continues rising with price rather than fading.",
        ]
    if agent_key == "funding":
        return [
            f"Monitor whether funding stays near {format_percent(agent_payload.get('annualized_rate_pct'), 2)} annualized or starts crowding harder.",
            "Compare the funding read with the next signal-engine refresh before leaning on carry.",
        ]
    if agent_key == "liquidation":
        return [
            "Wait for fresh liquidation prints to confirm whether squeeze pressure is actually building.",
            "If new forced flows do not appear, treat this agent as supporting context only.",
        ]
    if agent_key == "sentiment":
        powered_by = str(agent_payload.get("powered_by") or "Elfa AI")
        reason = str(agent_payload.get("reason") or "")
        if "insufficient" in reason.lower():
            return [
                "Wait for stronger social coverage before treating this agent as important.",
                "Use price, funding, and orderbook first until live attention data improves.",
            ]
        if "Current-affairs" in powered_by:
            return [
                "Watch whether fresh headlines keep building around this token.",
                "If the headline flow fades, treat this sentiment read as weak confirmation only.",
            ]
        return [
            "Watch whether the token stays near the top of Elfa trending rankings.",
            "Confirm that mention strength remains elevated rather than rolling over after the current burst.",
        ]
    if agent_key == "narrative":
        powered_by = str(agent_payload.get("powered_by") or "")
        reason = str(agent_payload.get("reason") or "")
        if "insufficient" in reason.lower():
            return [
                "Wait for a clearer catalyst before leaning on narrative as a driver.",
                "Use narrative as a secondary check until fresh story flow appears.",
            ]
        if "Current-affairs" in powered_by:
            return [
                "Watch whether the headline theme stays consistent on the next refresh.",
                "If the story flow turns mixed, keep narrative as a secondary check instead of the lead driver.",
            ]
        return [
            "Watch whether a clearer catalyst emerges from the current headlines and themes.",
            "If the story stays broad and generic, keep narrative as confirmation rather than primary signal.",
        ]
    if agent_key == "orderbook" and "unavailable" in str(agent_payload.get("reason", "")).lower():
        return [
            "Wait for the next live orderbook refresh before using levels from this agent.",
            "Do not trade off book levels until fresh depth and wall data return.",
        ]
    return [
        "Monitor whether the strongest wall is being absorbed or remains a hard rejection zone.",
        "If imbalance weakens sharply, expect the overall desk conviction to fade as well.",
    ]


def build_agent_suggested_questions(agent_key: str, symbol: str) -> list[str]:
    """Return suggested quick questions for the agent workspace chat."""
    label = AGENT_LABELS[agent_key]
    return [
        f"What is the latest {label.lower()} report for {symbol}?",
        f"How does this {label.lower()} view affect the overall verdict on {symbol}?",
        f"What should I watch next from the {label.lower()} on {symbol}?",
    ]


def build_frontdesk_support_summary(signal_payload: dict[str, Any]) -> list[str]:
    """Summarize how the specialist agents line up behind the desk call."""
    support_lines: list[str] = []
    for agent_key in ("market", "funding", "liquidation", "sentiment", "narrative", "orderbook"):
        agent_payload = signal_payload.get("agents", {}).get(agent_key, {})
        verdict = extract_agent_verdict(agent_key, agent_payload)
        reason = build_agent_report_text(agent_key, agent_payload)
        support_lines.append(f"{AGENT_LABELS[agent_key]}: {verdict}. {reason}")
    return support_lines


def build_frontdesk_workspace_payload(symbol: str, signal_payload: dict[str, Any]) -> dict[str, Any]:
    """Build the collaborative frontdesk workspace from all agent outputs."""
    final_signal = str(signal_payload.get("final_signal", "HOLD"))
    confidence_pct = float(signal_payload.get("confidence_pct", 0.0))
    counts = extract_bull_bear_neutral_counts(signal_payload.get("agents", {}))
    support_summary = build_frontdesk_support_summary(signal_payload)
    news_context = signal_payload.get("news_context", {}) if isinstance(signal_payload.get("news_context"), dict) else {}
    top_themes = news_context.get("top_themes", []) if isinstance(news_context, dict) else []
    theme_blurb = f" Current themes are {', '.join(top_themes[:2])}." if top_themes else ""
    report = (
        f"The desk is currently {final_signal} on {symbol} with {confidence_pct:.0f}% confidence. "
        f"{counts['bullish']} agents are bullish, {counts['bearish']} are bearish, and {counts['neutral']} are neutral.{theme_blurb}"
    )
    overall_context = (
        f"Frontdesk is combining market structure, funding, liquidations, sentiment, narrative, and orderbook evidence for {symbol}."
    )
    next_steps = [
        "Follow the strongest confirming agents first, then check whether the conflicting agents are softening or strengthening.",
        "Use the highlighted proof points below before acting on the desk verdict.",
    ]
    current_affairs = news_context.get("headlines", []) if isinstance(news_context, dict) else []
    return {
        "symbol": symbol,
        "agent": "frontdesk",
        "agent_label": AGENT_LABELS["frontdesk"],
        "verdict": final_signal,
        "overall_verdict": final_signal,
        "overall_confidence_pct": confidence_pct,
        "report": report,
        "overall_context": overall_context,
        "key_metric_label": "Desk confidence",
        "key_metric_value": format_percent(confidence_pct, 0),
        "next_steps": next_steps,
        "current_affairs": current_affairs[:3],
        "top_themes": top_themes,
        "suggested_questions": [
            f"Why is the desk {final_signal} on {symbol} right now?",
            f"Which agents are driving the {final_signal} call on {symbol}?",
            f"What proof should I check before acting on {symbol}?",
        ],
        "support_summary": support_summary,
        "raw_agent_payload": {"counts": counts},
    }


def build_agent_workspace_payload(
    symbol: str,
    agent_key: str,
    signal_payload: dict[str, Any],
) -> dict[str, Any]:
    """Build the front-end workspace payload for a selected agent."""
    normalized_agent = validate_agent_key(agent_key)
    if normalized_agent == "frontdesk":
        return build_frontdesk_workspace_payload(symbol, signal_payload)
    agent_payload = signal_payload.get("agents", {}).get(normalized_agent, {})
    verdict = extract_agent_verdict(normalized_agent, agent_payload)
    key_metric_label, key_metric_value = build_agent_key_metric(normalized_agent, agent_payload)
    news_context = signal_payload.get("news_context", {})
    current_affairs = news_context.get("headlines", []) if isinstance(news_context, dict) else []
    final_signal = str(signal_payload.get("final_signal", "HOLD"))
    confidence_pct = float(signal_payload.get("confidence_pct", 0.0))
    reason = build_agent_report_text(normalized_agent, agent_payload)
    if normalized_agent in {"sentiment", "narrative"} and current_affairs:
        lead_headline = current_affairs[0] if isinstance(current_affairs[0], dict) else {}
        lead_title = str(lead_headline.get("title", "")).strip()
        themes = news_context.get("top_themes", []) if isinstance(news_context, dict) else []
        theme_text = ", ".join(str(theme) for theme in themes[:3] if isinstance(theme, str))
        if normalized_agent == "sentiment" and "Insufficient social data" in reason:
            reason = (
                f"Elfa social coverage is thin, so the live attention cross-check is coming from current-affairs web search. "
                f"The strongest current-affairs headline is {lead_title or 'not available'}, with themes {theme_text or 'market context'}."
            )
        elif normalized_agent == "narrative" and "Insufficient data" in reason:
            reason = (
                f"Narrative is being cross-checked through current-affairs web search because Elfa/NeMo context is thin. "
                f"The leading story is {lead_title or 'not available'}, with themes {theme_text or 'market context'}."
            )
    supporting_specialists = [
        AGENT_LABELS[other_agent_key]
        for other_agent_key, other_payload in signal_payload.get("agents", {}).items()
        if other_agent_key != normalized_agent
        and other_agent_key in AGENT_LABELS
        and str(extract_agent_verdict(other_agent_key, other_payload if isinstance(other_payload, dict) else {})).upper() in {"BUY", "BULLISH"}
    ]
    support_text = (
        f" The main cross-check support comes from {', '.join(supporting_specialists[:2])}."
        if supporting_specialists else
        " The rest of the desk is mostly neutral right now."
    )
    overall_context = (
        f"{AGENT_LABELS[normalized_agent]} is currently {verdict}. "
        f"The overall desk verdict on {symbol} is {final_signal} with {confidence_pct:.0f}% confidence.{support_text}"
    )
    reasoning_details = build_agent_reasoning_details(normalized_agent, agent_payload)
    return {
        "symbol": symbol,
        "agent": normalized_agent,
        "agent_label": AGENT_LABELS[normalized_agent],
        "verdict": verdict,
        "overall_verdict": final_signal,
        "overall_confidence_pct": confidence_pct,
        "report": reason,
        "overall_context": overall_context,
        "key_metric_label": key_metric_label,
        "key_metric_value": key_metric_value,
        "next_steps": build_agent_next_steps(normalized_agent, signal_payload),
        "current_affairs": current_affairs[:3],
        "top_themes": news_context.get("top_themes", []) if isinstance(news_context, dict) else [],
        "suggested_questions": build_agent_suggested_questions(normalized_agent, symbol),
        "reasoning_details": reasoning_details,
        "raw_agent_payload": agent_payload,
    }


def build_agent_chat_answer(
    question: str,
    workspace: dict[str, Any],
) -> str:
    """Build a concise deterministic answer for the agent chat drawer."""
    question_lower = question.lower()
    if workspace.get("agent") == "frontdesk":
        support_summary = workspace.get("support_summary", [])
        proof_line = support_summary[0] if support_summary else "The desk is using the full specialist stack."
        if "proof" in question_lower or "why" in question_lower:
            return (
                f"Here is the desk call in plain English: {workspace.get('report')} "
                f"The strongest proof right now is {proof_line} "
                f"Next step: {workspace.get('next_steps', ['Check the next refresh.'])[0]}"
            )
        if "agent" in question_lower or "who" in question_lower:
            lead_lines = " ".join(support_summary[:3]) if support_summary else "No specialist breakdown is available."
            return (
                f"The frontdesk is combining the specialist views before making a call. {lead_lines}"
            )
        return (
            f"Here is the desk read: {workspace.get('report')} "
            f"{proof_line} "
            f"Action-wise, use the verdict as a guide and verify the proof points before acting."
        )
    verdict = workspace.get("verdict", "NEUTRAL")
    overall_verdict = workspace.get("overall_verdict", "HOLD")
    metric_label = workspace.get("key_metric_label", "Metric")
    metric_value = workspace.get("key_metric_value", "n/a")
    report = workspace.get("report", "No fresh report available.")
    next_steps = workspace.get("next_steps", [])
    current_affairs = workspace.get("current_affairs", [])
    first_step = next_steps[0] if next_steps else "Watch the next dashboard refresh for confirmation."
    news_sentence = "Current affairs are thin right now."
    if current_affairs:
        lead_headline = current_affairs[0]
        news_sentence = (
            f"Current affairs lead with {lead_headline.get('title', 'a fresh headline')}"
        )
    if "news" in question_lower or "current" in question_lower:
        return (
            f"Quick read: {news_sentence}. {workspace.get('agent_label')} is still {verdict} because {report} "
            f"Next, watch this: {first_step}"
        )
    if "next" in question_lower or "watch" in question_lower:
        return (
            f"{workspace.get('agent_label')} is {verdict}. That fits with the overall desk staying {overall_verdict}. "
            f"The next thing to watch is {first_step}"
        )
    if "overall" in question_lower or "verdict" in question_lower:
        return (
            f"My current call is {verdict}, while the full desk is {overall_verdict}. "
            f"The main proof point is {metric_label} at {metric_value}. {report}"
        )
    return (
        f"Here is the simple version: {workspace.get('agent_label')} is {verdict}. "
        f"{metric_label} is {metric_value}. {report} "
        f"The overall desk verdict is {overall_verdict}. {news_sentence}. Next step: {first_step}"
    )


def build_team_reasoned_answer(
    question: str,
    workspace: dict[str, Any],
    reports: dict[str, Any],
    signal_payload: dict[str, Any] | None = None,
    all_markets_board: list[dict[str, Any]] | None = None,
) -> str:
    """Build a more conversational answer that references the whole desk."""
    question_lower = question.lower()
    if workspace.get("agent") == "frontdesk" and all_markets_board is not None:
        ranked = sorted(
            all_markets_board,
            key=lambda row: (
                1 if row.get("quick_signal") == "BUY" else 0,
                float(row.get("open_interest") or 0.0),
                float(row.get("volume_24h") or 0.0),
            ),
            reverse=True,
        )
        top_rows = ranked[:3]
        if not top_rows:
            return "I do not have enough live all-market data right now to recommend a fresh market to explore."
        if "recommend" in question_lower or "explore" in question_lower or "new market" in question_lower:
            recommendations = []
            for row in top_rows:
                recommendations.append(
                    f"{row.get('symbol')} is worth checking because it is showing {row.get('quick_signal')} on the lightweight board, with {format_compact_number(row.get('open_interest'))} open interest and {format_percent(row.get('funding_apy'), 2)} funding APY."
                )
            return (
                "If you want fresh markets to explore, I would start with "
                f"{', '.join(str(row.get('symbol')) for row in top_rows)}. "
                + " ".join(recommendations)
            )
        return (
            f"Across the broader Pacifica board, the strongest markets by activity right now are "
            f"{', '.join(str(row.get('symbol')) for row in top_rows)}. "
            f"My simple read is to explore the names with the best mix of open interest, volume, and cleaner quick-signal alignment first."
        )

    primary_line = build_agent_chat_answer(question, workspace)
    supporting_lines: list[str] = []
    for agent_key in ("market", "funding", "liquidation", "sentiment", "narrative", "orderbook"):
        if agent_key == workspace.get("agent"):
            continue
        report = reports.get(agent_key)
        if not isinstance(report, dict):
            continue
        supporting_lines.append(
            f"{report.get('agent_label')} is {report.get('verdict')} with {report.get('key_metric_label')} at {report.get('key_metric_value')}."
        )
    support_text = " ".join(supporting_lines[:3])
    news_text = ""
    current_affairs = workspace.get("current_affairs", [])
    if current_affairs and ("news" in question_lower or "why" in question_lower or "proof" in question_lower):
        top_headline = current_affairs[0]
        news_text = f" Current affairs also point to {top_headline.get('title', 'a live headline')}."
    if signal_payload and ("should" in question_lower or "do" in question_lower or "action" in question_lower):
        desk_verdict = signal_payload.get("final_signal", "HOLD")
        confidence_pct = float(signal_payload.get("confidence_pct", 0.0))
        action_text = (
            f" The full desk is leaning {desk_verdict} with {confidence_pct:.0f}% confidence, so treat that as the team context rather than acting on one agent in isolation."
        )
    else:
        action_text = ""
    return f"{primary_line} Team cross-check: {support_text}{news_text}{action_text}"


def build_all_markets_frontdesk_workspace(all_markets_board: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a workspace payload for the frontdesk when the user is in all-markets mode."""
    ranked = sorted(
        all_markets_board,
        key=lambda row: (
            1 if row.get("quick_signal") == "BUY" else 0,
            float(row.get("open_interest") or 0.0),
            float(row.get("volume_24h") or 0.0),
        ),
        reverse=True,
    )
    top_rows = ranked[:5]
    summary = ", ".join(str(row.get("symbol")) for row in top_rows) if top_rows else "No live market rows available"
    return {
        "symbol": "ALL",
        "agent": "frontdesk",
        "agent_label": AGENT_LABELS["frontdesk"],
        "verdict": "SCAN",
        "overall_verdict": "SCAN",
        "overall_confidence_pct": 0.0,
        "report": f"Frontdesk is scanning the broader Pacifica board. The most active names right now are {summary}.",
        "overall_context": "This mode is for exploring the broader market board before drilling into one symbol.",
        "key_metric_label": "Markets scanned",
        "key_metric_value": str(len(all_markets_board)),
        "next_steps": [
            "Ask for a market recommendation if you want a fresh symbol to explore.",
            "Click any market row to drill into the full desk for that symbol.",
        ],
        "current_affairs": [],
        "top_themes": [],
        "suggested_questions": [
            "Recommend a new market for me to explore.",
            "Which broader markets look most active right now?",
            "Which market has the cleanest setup on the board?",
        ],
        "support_summary": [
            f"{row.get('symbol')} | {row.get('quick_signal')} | OI {format_compact_number(row.get('open_interest'))} | Funding {format_percent(row.get('funding_apy'), 2)}"
            for row in top_rows
        ],
        "raw_agent_payload": {"markets": top_rows},
    }


def merge_news_contexts(news_contexts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Merge multiple current-affairs blocks into one deduplicated headline and theme set."""
    merged_headlines: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    merged_themes: list[str] = []

    for news_context in news_contexts:
        if not isinstance(news_context, dict):
            continue
        for theme in news_context.get("top_themes", []):
            if isinstance(theme, str) and theme and theme not in merged_themes:
                merged_themes.append(theme)
        for headline in news_context.get("headlines", []):
            if not isinstance(headline, dict):
                continue
            title = str(headline.get("title", "")).strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            merged_headlines.append(headline)

    return merged_headlines[:6], merged_themes[:6]


async def build_all_markets_frontdesk_workspace_payload() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build the all-markets frontdesk workspace and attach lightweight current-affairs context."""
    cache_key = "all-markets-workspace"

    async def _builder() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        all_markets_payload = await build_all_markets_board_payload()
        board = all_markets_payload.get("all_markets_board", [])
        normalized_board = board if isinstance(board, list) else []
        workspace = build_all_markets_frontdesk_workspace(normalized_board)
        top_rows = workspace.get("raw_agent_payload", {}).get("markets", [])

        top_symbols = [
            str(row.get("symbol"))
            for row in top_rows[:3]
            if isinstance(row, dict) and row.get("symbol")
        ]
        news_blocks = await asyncio.gather(
            *(build_cached_news_context(symbol) for symbol in top_symbols),
            return_exceptions=True,
        )
        normalized_news = [
            news_block
            for news_block in news_blocks
            if isinstance(news_block, dict)
        ]
        headlines, themes = merge_news_contexts(normalized_news)
        if not headlines:
            fallback_symbols = [
                symbol
                for symbol in ("BTC-USDC", "ETH-USDC", "SOL-USDC")
                if symbol not in top_symbols
            ]
            fallback_blocks = await asyncio.gather(
                *(build_cached_news_context(symbol) for symbol in fallback_symbols),
                return_exceptions=True,
            )
            fallback_news = [
                news_block
                for news_block in fallback_blocks
                if isinstance(news_block, dict)
            ]
            headlines, themes = merge_news_contexts(fallback_news)
        if headlines:
            workspace["current_affairs"] = headlines
        if themes:
            workspace["top_themes"] = themes
            workspace["overall_context"] = (
                f"{workspace.get('overall_context', '')} Current themes across the active board include {', '.join(themes[:3])}."
            ).strip()
        return workspace, normalized_board

    return await get_or_build_cached(cache_key, CACHE_TTL_OVERVIEW_SECONDS, _builder)


def build_dashboard_agent_chat_context(
    question: str,
    workspace: dict[str, Any],
    reports: dict[str, Any],
    signal_payload: dict[str, Any] | None = None,
    all_markets_board: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the compact context payload for dashboard agent-chat reasoning."""
    if all_markets_board is not None:
        return {
            "mode": "all_markets",
            "question": question,
            "selected_agent": workspace.get("agent", "frontdesk"),
            "workspace": {
                "agent_label": workspace.get("agent_label"),
                "report": compact_text(workspace.get("report"), 220),
                "overall_context": compact_text(workspace.get("overall_context"), 220),
                "next_steps": workspace.get("next_steps", []),
            },
            "all_markets_board": all_markets_board[:8],
            "current_affairs": workspace.get("current_affairs", []),
            "top_themes": workspace.get("top_themes", []),
        }

    signal_payload = signal_payload if isinstance(signal_payload, dict) else {}
    news_context = signal_payload.get("news_context", {}) if isinstance(signal_payload.get("news_context"), dict) else {}
    altfins = signal_payload.get("altfins", {}) if isinstance(signal_payload.get("altfins"), dict) else {}
    backtest = signal_payload.get("backtest", {}) if isinstance(signal_payload.get("backtest"), dict) else {}
    session_accuracy = (
        signal_payload.get("session_accuracy", {})
        if isinstance(signal_payload.get("session_accuracy"), dict)
        else {}
    )
    agents = signal_payload.get("agents", {}) if isinstance(signal_payload.get("agents"), dict) else {}
    team_reports = {
        agent_key: {
            "agent_label": report.get("agent_label"),
            "verdict": report.get("verdict"),
            "overall_verdict": report.get("overall_verdict"),
            "key_metric_label": report.get("key_metric_label"),
            "key_metric_value": report.get("key_metric_value"),
            "report": compact_text(report.get("report"), 140),
            "next_steps": report.get("next_steps", [])[:2],
        }
        for agent_key, report in reports.items()
        if isinstance(report, dict) and agent_key != "frontdesk"
    }
    return {
        "mode": "single_market",
        "question": question,
        "selected_agent": workspace.get("agent"),
        "workspace": {
            "symbol": workspace.get("symbol"),
            "agent": workspace.get("agent"),
            "agent_label": workspace.get("agent_label"),
            "verdict": workspace.get("verdict"),
            "overall_verdict": workspace.get("overall_verdict"),
            "overall_confidence_pct": workspace.get("overall_confidence_pct"),
            "report": compact_text(workspace.get("report"), 220),
            "overall_context": compact_text(workspace.get("overall_context"), 180),
            "key_metric_label": workspace.get("key_metric_label"),
            "key_metric_value": workspace.get("key_metric_value"),
            "next_steps": workspace.get("next_steps", [])[:3],
            "current_affairs": workspace.get("current_affairs", [])[:3],
            "top_themes": workspace.get("top_themes", [])[:4],
            "reasoning_details": workspace.get("reasoning_details", {}),
        },
        "team_reports": team_reports,
        "market_signal": {
            "symbol": workspace.get("symbol"),
            "final_signal": (signal_payload or {}).get("final_signal"),
            "confidence_pct": (signal_payload or {}).get("confidence_pct"),
            "score": (signal_payload or {}).get("score"),
        },
        "agent_metrics": {
            agent_key: {
                "signal": payload.get("signal") if isinstance(payload, dict) else None,
                "reason": compact_text(payload.get("reason"), 110) if isinstance(payload, dict) else None,
            }
            for agent_key, payload in agents.items()
            if isinstance(payload, dict)
        },
        "validation": {
            "pattern_matches": backtest.get("pattern_matches"),
            "accuracy_pct": backtest.get("accuracy_pct"),
            "avg_move_pct": backtest.get("avg_move_pct"),
            "backtest_label": backtest.get("backtest_label"),
            "session_accuracy_pct": session_accuracy.get("accuracy_pct"),
            "signals_scored": session_accuracy.get("signals_scored"),
        },
        "external_confirmation": {
            "source_status": altfins.get("source_status"),
            "view": (altfins.get("summary_block", {}) if isinstance(altfins.get("summary_block"), dict) else {}).get("altfins_view"),
            "signals_overview": (altfins.get("summary_block", {}) if isinstance(altfins.get("summary_block"), dict) else {}).get("signals_overview"),
            "alignment": altfins.get("alignment_with_signal"),
            "conviction": altfins.get("altfins_conviction"),
        },
        "news_context": {
            "available": news_context.get("available"),
            "top_themes": news_context.get("top_themes", [])[:4],
            "headlines": [
                {
                    "title": headline.get("title"),
                    "source": headline.get("source"),
                }
                for headline in news_context.get("headlines", [])[:3]
                if isinstance(headline, dict)
            ],
        },
    }


def build_dashboard_market_summary(signal_payload: dict[str, Any]) -> dict[str, Any]:
    """Build a compact market summary block for the dashboard overview."""
    market_agent_payload = signal_payload.get("agents", {}).get("market", {})
    return {
        "symbol": signal_payload.get("symbol"),
        "final_signal": signal_payload.get("final_signal", "HOLD"),
        "confidence_pct": signal_payload.get("confidence_pct", 0.0),
        "price": market_agent_payload.get("price"),
        "change_24h": market_agent_payload.get("change_24h"),
        "volume_24h": market_agent_payload.get("volume_24h"),
        "open_interest": market_agent_payload.get("open_interest"),
        "narration": signal_payload.get("narration"),
        "reasoning": signal_payload.get("reasoning"),
    }


def build_minimal_dashboard_market_payload(symbol: str) -> dict[str, object]:
    """Build a lightweight fallback dashboard payload when full enrichment is too slow."""
    normalized_symbol = normalize_symbol(symbol)
    timestamp = utc_timestamp()
    signal_payload = signal_agent._neutral_signal(normalized_symbol, timestamp)
    signal_payload["session_accuracy"] = accuracy_tracker.get_stats()
    signal_payload["backtest"] = {
        "pattern_matches": 0,
        "correct_predictions": 0,
        "accuracy_pct": 0.0,
        "avg_move_pct": 0.0,
        "backtest_label": "Pattern sample is still building from recent market history.",
    }
    signal_payload["altfins"] = build_altfins_derived_view(
        normalized_symbol,
        signal_payload,
        reason="minimal dashboard fallback",
    )
    signal_payload["news_context"] = {
        "available": False,
        "symbol": normalized_symbol.split("-")[0].upper(),
        "headlines": [],
        "top_themes": [],
    }
    signal_payload["narration"] = "Using the latest stable desk snapshot while fresh inputs settle."
    signal_payload["analysis_context"] = format_analysis_context(
        signal_result=signal_payload,
        backtest=signal_payload["backtest"],
        session_accuracy=signal_payload["session_accuracy"],
        altfins=signal_payload["altfins"],
        news_context=signal_payload["news_context"],
    )
    reports = {
        agent_key: build_agent_workspace_payload(normalized_symbol, agent_key, signal_payload)
        for agent_key in AGENT_LABELS
    }
    return {
        "symbol": normalized_symbol,
        "signal": signal_payload,
        "chart": {"symbol": normalized_symbol, "data": []},
        "reports": reports,
        "team_summary": build_dashboard_market_summary(signal_payload),
    }


async def build_fast_agent_chat_market_payload(symbol: str) -> dict[str, object]:
    """Build a lighter signal payload for agent chat when the full dashboard payload is not cached."""
    normalized_symbol = normalize_symbol(symbol)
    cached_signal = get_cached_value(f"signal:{normalized_symbol}")
    if isinstance(cached_signal, dict) and not signal_payload_is_degraded(cached_signal):
        signal_payload = dict(cached_signal)
    else:
        signal_payload = dict(await signal_agent.analyze(normalized_symbol))
        signal_payload = await repair_signal_payload_from_prices(normalized_symbol, signal_payload)
        signal_payload["session_accuracy"] = accuracy_tracker.get_stats()
        signal_payload["backtest"] = {
            "pattern_matches": 0,
            "correct_predictions": 0,
            "accuracy_pct": 0.0,
            "avg_move_pct": 0.0,
            "backtest_label": "Fast chat mode is using live desk data without a full historical backtest refresh.",
        }
        signal_payload["altfins"] = build_altfins_derived_view(
            normalized_symbol,
            signal_payload,
            reason="fast chat path",
        )
        narrative_summary = (
            signal_payload.get("agents", {})
            .get("narrative", {})
            .get("narrative_summary")
        )
        news_context = await build_cached_news_context(normalized_symbol, narrative_summary)
        signal_payload["news_context"] = news_context if isinstance(news_context, dict) else {
            "available": False,
            "symbol": normalized_symbol.split("-")[0].upper(),
            "headlines": [],
            "top_themes": [],
        }
        signal_payload["analysis_context"] = format_analysis_context(
            signal_result=signal_payload,
            backtest=signal_payload["backtest"],
            session_accuracy=signal_payload["session_accuracy"],
            altfins=signal_payload["altfins"],
            news_context=signal_payload["news_context"],
        )

    reports = {
        agent_key: build_agent_workspace_payload(normalized_symbol, agent_key, signal_payload)
        for agent_key in AGENT_LABELS
    }
    return {
        "symbol": normalized_symbol,
        "signal": signal_payload,
        "reports": reports,
        "team_summary": build_dashboard_market_summary(signal_payload),
    }


def dashboard_market_payload_is_degraded(payload: dict[str, Any]) -> bool:
    """Return whether a cached dashboard market payload should be treated as degraded."""
    if not isinstance(payload, dict):
        return True
    signal_payload = payload.get("signal", {})
    return signal_payload_is_degraded(signal_payload if isinstance(signal_payload, dict) else {})


def dashboard_market_payload_quality(payload: dict[str, Any]) -> tuple[int, int, int]:
    """Score a dashboard market payload so weaker degraded states can reuse stronger cached ones."""
    if not isinstance(payload, dict):
        return (-1, -1, -1)
    signal_payload = payload.get("signal", {})
    if not isinstance(signal_payload, dict):
        return (-1, -1, -1)
    agents = signal_payload.get("agents", {})
    if not isinstance(agents, dict):
        agents = {}
    decisive_agents = sum(
        1
        for agent_payload in agents.values()
        if isinstance(agent_payload, dict)
        and str(agent_payload.get("signal") or agent_payload.get("trend") or "NEUTRAL").upper() != "NEUTRAL"
    )
    agent_errors = sum(
        1
        for agent_payload in agents.values()
        if isinstance(agent_payload, dict) and agent_payload.get("error")
    )
    confidence = int(round(float(signal_payload.get("confidence_pct", 0.0))))
    return (decisive_agents, -agent_errors, confidence)


def choose_best_dashboard_market_payload(
    symbol: str,
    current_payload: dict[str, Any],
) -> dict[str, Any]:
    """Always prefer the current payload so old dashboard states do not leak back in."""
    normalized_symbol = normalize_symbol(symbol)
    if not dashboard_market_payload_is_degraded(current_payload):
        last_good_dashboard_markets[normalized_symbol] = current_payload
    else:
        last_good_dashboard_markets.pop(normalized_symbol, None)
    return current_payload


async def build_all_markets_board(limit: int = 18) -> list[dict[str, Any]]:
    """Build a lightweight all-markets board from Pacifica's full price and market info feeds."""
    def _safe_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    prices_payload = await pacifica_client.get_prices()
    prices = prices_payload.get("data", []) if isinstance(prices_payload, dict) else []
    rows: list[dict[str, Any]] = []
    for item in prices:
        if not isinstance(item, dict):
            continue
        base_symbol = str(item.get("symbol", "")).upper().strip()
        if not base_symbol:
            continue
        mark = _safe_float(item.get("mark") or 0.0)
        yesterday_price = _safe_float(item.get("yesterday_price") or 0.0)
        change_24h = 0.0
        if yesterday_price > 0:
            change_24h = ((mark - yesterday_price) / yesterday_price) * 100.0
        funding_rate = _safe_float(item.get("funding") or 0.0)
        annualized_funding = funding_rate * 3 * 365 * 100
        open_interest = _safe_float(item.get("open_interest") or 0.0)
        volume_24h = _safe_float(item.get("volume_24h") or 0.0)
        quick_signal = "HOLD"
        if change_24h > 1 and annualized_funding <= 0:
            quick_signal = "BUY"
        elif change_24h < -1 and annualized_funding >= 0:
            quick_signal = "SELL"
        rows.append(
            {
                "symbol": f"{base_symbol}-USDC",
                "base_symbol": base_symbol,
                "price": mark,
                "change_24h": change_24h,
                "funding_apy": annualized_funding,
                "open_interest": open_interest,
                "volume_24h": volume_24h,
                "max_leverage": None,
                "quick_signal": quick_signal,
            }
        )
    rows.sort(key=lambda row: (row["open_interest"], row["volume_24h"]), reverse=True)
    return rows[:limit]


async def build_cached_chart_payload(symbol: str) -> dict[str, object]:
    """Return a cached chart payload for the dashboard chart panel."""
    normalized_symbol = normalize_symbol(symbol)

    async def _builder() -> dict[str, object]:
        return await get_chart(normalized_symbol)

    return await get_or_build_cached(
        cache_key=f"chart:{normalized_symbol}",
        ttl_seconds=CACHE_TTL_CHART_SECONDS,
        builder=_builder,
    )


async def build_cached_narration(
    symbol: str,
    signal_payload: dict[str, Any],
) -> str:
    """Return a cached narration string, with a context fallback if NeMo is slow."""
    normalized_symbol = normalize_symbol(symbol)

    async def _builder() -> str:
        fallback = narrator._signal_fallback_from_context(
            normalized_symbol,
            signal_payload,
            signal_payload.get("agents", {}),
        )
        use_llm_narration = os.getenv("ENABLE_DASHBOARD_LLM_NARRATION", "false").strip().lower() == "true"
        if not use_llm_narration:
            return fallback
        try:
            return await asyncio.wait_for(
                narrator.narrate_signal(
                    symbol=normalized_symbol,
                    signal_data=signal_payload,
                    agent_outputs=signal_payload.get("agents", {}),
                ),
                timeout=4.5,
            )
        except Exception:
            logger.warning("Dashboard narration fell back for %s", normalized_symbol)
            return fallback

    return await get_or_build_cached(
        cache_key=f"narration:{normalized_symbol}:{signal_payload.get('final_signal')}:{signal_payload.get('score')}",
        ttl_seconds=CACHE_TTL_DASHBOARD_SECONDS,
        builder=_builder,
    )


async def build_dashboard_market_payload(symbol: str) -> dict[str, object]:
    """Return the full dashboard payload for one symbol with caching and fallback reuse."""
    normalized_symbol = normalize_symbol(symbol)
    cache_key = f"dashboard-market:{normalized_symbol}"

    async def _builder() -> dict[str, object]:
        signal_task = asyncio.create_task(build_enriched_signal_result(normalized_symbol))
        chart_task = asyncio.create_task(build_cached_chart_payload(normalized_symbol))
        signal_payload = await signal_task
        signal_payload = dict(signal_payload)
        narration_task = asyncio.create_task(build_cached_narration(normalized_symbol, signal_payload))
        signal_payload["narration"] = await narration_task
        try:
            chart_payload = await asyncio.wait_for(chart_task, timeout=6.0)
        except Exception as exc:
            logger.warning("Chart payload degraded for %s: %s", normalized_symbol, exc)
            chart_payload = {"symbol": normalized_symbol, "interval": "1h", "data": []}
        reports = {
            agent_key: build_agent_workspace_payload(normalized_symbol, agent_key, signal_payload)
            for agent_key in AGENT_LABELS
        }
        payload = {
            "symbol": normalized_symbol,
            "signal": signal_payload,
            "chart": chart_payload,
            "reports": reports,
            "team_summary": build_dashboard_market_summary(signal_payload),
        }
        return choose_best_dashboard_market_payload(normalized_symbol, payload)

    cached_value = get_cached_value(cache_key)
    if isinstance(cached_value, dict) and not dashboard_market_payload_is_degraded(cached_value):
        return cached_value
    if dashboard_market_payload_is_degraded(cached_value if isinstance(cached_value, dict) else {}):
        dashboard_cache.pop(cache_key, None)

    inflight_task = dashboard_inflight.get(cache_key)
    if inflight_task is not None:
        return await inflight_task

    task = asyncio.create_task(_builder())
    dashboard_inflight[cache_key] = task
    try:
        payload = await task
        ttl_seconds = (
            CACHE_TTL_SIGNAL_DEGRADED_SECONDS
            if dashboard_market_payload_is_degraded(payload)
            else CACHE_TTL_DASHBOARD_SECONDS
        )
        return set_cached_value(cache_key, payload, ttl_seconds)
    finally:
        dashboard_inflight.pop(cache_key, None)


async def build_dashboard_overview_payload() -> dict[str, object]:
    """Return a cached overview payload across the three demo markets."""

    async def _builder() -> dict[str, object]:
        symbols = ("BTC-USDC", "ETH-USDC", "SOL-USDC")
        raw_results = await asyncio.gather(
            *(signal_agent.analyze(symbol) for symbol in symbols),
            return_exceptions=True,
        )
        markets: dict[str, dict[str, Any]] = {}
        for symbol, result in zip(symbols, raw_results, strict=False):
            if isinstance(result, Exception) or not isinstance(result, dict):
                timestamp = utc_timestamp()
                markets[symbol] = signal_agent._neutral_signal(symbol, timestamp)
                continue
            repaired = await repair_signal_payload_from_prices(symbol, result)
            markets[symbol] = repaired
        summary_cards: list[dict[str, Any]] = []
        for symbol, signal_payload in markets.items():
            if not isinstance(signal_payload, dict):
                continue
            summary_cards.append(build_dashboard_market_summary(signal_payload))
        return {
            "macro_alert": signal_agent._macro_alert(markets),
            "timestamp": utc_timestamp(),
            "summary_cards": summary_cards,
            "markets": markets,
        }

    payload = await get_or_build_cached(
        cache_key="dashboard-overview",
        ttl_seconds=CACHE_TTL_OVERVIEW_SECONDS,
        builder=_builder,
    )
    return payload


async def build_all_markets_board_payload() -> dict[str, object]:
    """Return a cached payload for the lightweight all-markets dashboard board."""

    async def _builder() -> dict[str, object]:
        return {
            "timestamp": utc_timestamp(),
            "all_markets_board": await build_all_markets_board(),
        }

    payload = await get_or_build_cached(
        cache_key="dashboard-all-markets",
        ttl_seconds=CACHE_TTL_OVERVIEW_SECONDS,
        builder=_builder,
    )
    if not payload.get("all_markets_board"):
        dashboard_cache.pop("dashboard-all-markets", None)
        payload = await _builder()
        set_cached_value(
            "dashboard-all-markets",
            payload,
            CACHE_TTL_SIGNAL_DEGRADED_SECONDS if not payload.get("all_markets_board") else CACHE_TTL_OVERVIEW_SECONDS,
        )
    return payload


async def prewarm_dashboard_cache() -> None:
    """Warm the live dashboard caches in the background after app startup."""
    try:
        await build_dashboard_overview_payload()
        await asyncio.gather(
            build_dashboard_market_payload("BTC-USDC"),
            build_dashboard_market_payload("ETH-USDC"),
            build_dashboard_market_payload("SOL-USDC"),
        )
        logger.info("Dashboard cache prewarm complete")
    except Exception:
        logger.exception("Dashboard cache prewarm failed")


async def handle_signal_flip(symbol: str, signal_payload: dict[str, Any]) -> None:
    """Send Telegram alerts when a symbol changes into a subscribed BUY or SELL state."""
    new_state = extract_signal_decision(signal_payload)
    old_state = last_signal_state.get(symbol)

    if new_state == old_state:
        return

    last_signal_state[symbol] = new_state
    subscriptions = get_telegram_subscriptions_for_symbol(symbol)
    if not subscriptions:
        return

    for subscription in subscriptions:
        should_fire = should_trigger_alert(subscription.trigger_on, new_state)
        if not should_fire:
            continue

        token = subscription.telegram_token or get_default_telegram_bot_token()
        chat_id = subscription.telegram_chat_id or get_default_telegram_chat_id()
        if not token or not chat_id:
            continue

        await send_telegram_signal_alert(
            token=token,
            chat_id=chat_id,
            symbol=symbol,
            trigger_on=subscription.trigger_on,
            signal_payload=signal_payload,
        )


def extract_signal_decision(signal_payload: dict[str, Any]) -> str:
    """Normalize a signal payload into BUY, SELL, HOLD, or UNKNOWN."""
    signal_engine = signal_payload.get("signal_engine", {})
    if isinstance(signal_engine, dict):
        decision = signal_engine.get("decision") or signal_engine.get("final_signal")
        if isinstance(decision, str) and decision.strip():
            return normalize_signal_decision(decision)
    final_signal = signal_payload.get("final_signal")
    if isinstance(final_signal, str) and final_signal.strip():
        return normalize_signal_decision(final_signal)
    return "UNKNOWN"


def normalize_signal_decision(value: str) -> str:
    """Normalize a signal decision into the supported alert states."""
    normalized_value = value.strip().upper()
    if normalized_value in {"BUY", "SELL", "HOLD"}:
        return normalized_value
    return "UNKNOWN"


def should_trigger_alert(trigger_on: str, new_state: str) -> bool:
    """Return whether a subscription should fire for a new signal state."""
    normalized_trigger = trigger_on.strip().upper()
    if normalized_trigger == "BUY_OR_SELL" and new_state in {"BUY", "SELL"}:
        return True
    if normalized_trigger == "BUY" and new_state == "BUY":
        return True
    if normalized_trigger == "SELL" and new_state == "SELL":
        return True
    return False


@app.get("/api/health")
async def health() -> dict[str, object]:
    """Return a simple health response."""
    return {
        "status": "ok",
        "timestamp": utc_timestamp(),
        "uptime_seconds": int(time.time() - APP_START_TIME),
    }


@app.get("/api/markets")
async def get_markets() -> dict[str, object]:
    """Return the list of configured Pacifica markets."""
    markets = await pacifica_client.get_markets()
    return {"markets": markets}


@app.get("/api/market/{symbol}")
async def get_market(symbol: str) -> dict[str, object]:
    """Run the market agent for a supported symbol.

    Args:
        symbol: Market symbol from the request path.
    """
    return await market_agent.analyze(validate_symbol(symbol))


@app.get("/api/funding/{symbol}")
async def get_funding(symbol: str) -> dict[str, object]:
    """Run the funding agent for a supported symbol."""
    return await funding_agent.analyze(validate_symbol(symbol))


@app.get("/api/liquidations/{symbol}")
async def get_liquidations(symbol: str) -> dict[str, object]:
    """Run the liquidation agent for a supported symbol."""
    return await liquidation_agent.analyze(validate_symbol(symbol))


@app.get("/api/sentiment/{symbol}")
async def get_sentiment(symbol: str) -> dict[str, object]:
    """Run the sentiment agent for a supported symbol."""
    return await sentiment_agent.analyze(validate_symbol(symbol))


@app.get("/api/orderbook/{symbol}")
async def get_orderbook(symbol: str) -> dict[str, object]:
    """Run the orderbook agent for a supported symbol."""
    return await orderbook_agent.analyze(validate_symbol(symbol))


@app.get("/api/chart/{symbol}")
async def get_chart(symbol: str) -> dict[str, object]:
    """Return chart klines for a supported symbol."""
    normalized_symbol = normalize_symbol(symbol)
    data = await pacifica_client.get_klines(symbol=normalized_symbol, interval="1h", limit=120)
    response: dict[str, object] = {
        "symbol": normalized_symbol,
        "interval": "1h",
        "data": data.get("data", []) if isinstance(data, dict) else [],
    }
    if isinstance(data, dict) and data.get("error"):
        response["error"] = data["error"]
    return response


@app.get("/api/signal/{symbol}")
async def get_signal(symbol: str) -> dict[str, object]:
    """Run the master signal agent for a supported symbol."""
    normalized_symbol = normalize_symbol(symbol)
    signal_result = await build_enriched_signal_result(normalized_symbol)
    final_signal = signal_result.get("final_signal")
    price_from_market_agent = signal_result.get("agents", {}).get("market", {}).get("price")
    try:
        if final_signal in {"BUY", "SELL"} and price_from_market_agent is not None:
            accuracy_tracker.record_signal(
                normalized_symbol,
                str(final_signal),
                float(price_from_market_agent),
            )
        await accuracy_tracker.update_outcomes()
    except Exception:
        logger.exception("Accuracy tracker update failed for %s", normalized_symbol)
    signal_result["session_accuracy"] = accuracy_tracker.get_stats()
    signal_result["analysis_context"] = format_analysis_context(
        signal_result=signal_result,
        backtest=signal_result.get("backtest", {}),
        session_accuracy=signal_result.get("session_accuracy", {}),
        altfins=signal_result.get("altfins", {}),
        news_context=signal_result.get("news_context", {}),
    )
    await handle_signal_flip(normalized_symbol, signal_result)
    narration = await build_cached_narration(normalized_symbol, signal_result)
    signal_result["narration"] = narration
    return signal_result


@app.get("/api/narrative/{symbol}")
async def get_narrative(symbol: str) -> dict[str, object]:
    """Run the narrative agent for a supported symbol."""
    return await narrative_agent.analyze(validate_symbol(symbol))


@app.get("/api/current-affairs/{symbol}")
async def get_current_affairs(symbol: str) -> dict[str, object]:
    """Return the live current-affairs agent output for a supported symbol."""
    return await current_agent.run(validate_symbol(symbol))


@app.get("/api/agents/{symbol}")
async def get_agents(symbol: str) -> dict[str, object]:
    """Run all data agents in parallel for a supported symbol."""
    normalized_symbol = validate_symbol(symbol)
    (
        market_result,
        funding_result,
        liquidation_result,
        sentiment_result,
        narrative_result,
        orderbook_result,
        current_affairs_result,
    ) = await asyncio.gather(
        market_agent.analyze(normalized_symbol),
        funding_agent.analyze(normalized_symbol),
        liquidation_agent.analyze(normalized_symbol),
        sentiment_agent.analyze(normalized_symbol),
        narrative_agent.analyze(normalized_symbol),
        orderbook_agent.analyze(normalized_symbol),
        current_agent.run(normalized_symbol),
    )
    return {
        "symbol": normalized_symbol,
        "market": market_result,
        "funding": funding_result,
        "liquidation": liquidation_result,
        "sentiment": sentiment_result,
        "narrative": narrative_result,
        "orderbook": orderbook_result,
        "current_affairs": current_affairs_result,
        "timestamp": utc_timestamp(),
    }


@app.get("/api/signals/all")
async def get_all_signals() -> dict[str, object]:
    """Run the signal agent across all supported markets."""
    result = await build_all_signals_view()
    markets = result.get("markets", {})
    if isinstance(markets, dict):
        await asyncio.gather(
            *(
                handle_signal_flip(symbol, payload)
                for symbol, payload in markets.items()
                if isinstance(payload, dict)
            )
        )
    return result


@app.get("/api/macro")
async def get_macro() -> dict[str, object]:
    """Return macro-level signal summaries across supported markets."""
    result = await signal_agent.analyze_all_markets()
    markets = result.get("markets", {})
    return {
        "macro_alert": result.get("macro_alert"),
        "markets": {
            symbol: {
                "final_signal": market_result.get("final_signal", "HOLD"),
                "score": market_result.get("score", 0),
                "confidence_pct": market_result.get("confidence_pct", 0.0),
            }
            for symbol, market_result in markets.items()
            if isinstance(market_result, dict)
        },
        "timestamp": result.get("timestamp", utc_timestamp()),
    }


@app.get("/api/backtest/{symbol}")
async def get_backtest(symbol: str) -> dict[str, object]:
    """Backtest the current signal pattern for a supported symbol."""
    normalized_symbol = normalize_symbol(symbol)
    result = await backtest_engine.backtest_current_pattern(normalized_symbol)
    return {
        "symbol": normalized_symbol,
        "pattern_matches": result.get("pattern_matches", 0),
        "correct_predictions": result.get("correct_predictions", 0),
        "accuracy_pct": result.get("accuracy_pct", 0.0),
        "avg_move_pct": result.get("avg_move_pct", 0.0),
        "backtest_label": result.get("backtest_label", "Pattern sample is still building from recent market history."),
    }


@app.get("/api/accuracy")
async def get_accuracy() -> dict[str, object]:
    """Return the current in-memory live accuracy stats for this session."""
    try:
        await accuracy_tracker.update_outcomes()
    except Exception:
        logger.exception("Accuracy tracker refresh failed")
    return accuracy_tracker.get_stats()


@app.post("/api/alert/subscribe")
async def subscribe_alert(subscription: AlertSubscription) -> JSONResponse:
    """Store an alert subscription for later email or Telegram delivery."""
    normalized_symbol = subscription.symbol.upper()
    normalized_trigger = subscription.trigger_on.upper()
    has_email = subscription.email is not None
    has_telegram = bool(subscription.telegram_token and subscription.telegram_chat_id)

    if normalized_symbol not in VALID_SYMBOLS:
        return JSONResponse(status_code=400, content={"error": "Invalid symbol"})
    if normalized_trigger not in {"BUY", "SELL", "HOLD"}:
        return JSONResponse(status_code=400, content={"error": "Invalid trigger_on value"})
    if not has_email and not has_telegram:
        return JSONResponse(
            status_code=400,
            content={"error": "Provide email or both telegram_token and telegram_chat_id"},
        )

    stored_subscription = {
        "email": str(subscription.email) if subscription.email is not None else None,
        "telegram_token": subscription.telegram_token,
        "telegram_chat_id": subscription.telegram_chat_id,
        "symbol": normalized_symbol,
        "trigger_on": normalized_trigger,
    }
    alert_subscriptions.append(stored_subscription)
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "message": "Subscription registered.",
            "subscription": stored_subscription,
        },
    )


@app.post("/apialert/subscribe")
async def subscribe_telegram_alert(body: AlertSubscribeRequest) -> JSONResponse:
    """Store or update a Telegram alert subscription for a symbol."""
    normalized_symbol = normalize_symbol(body.symbol)
    normalized_trigger = body.trigger_on.strip().upper()
    if normalized_trigger not in {"BUY", "SELL", "BUY_OR_SELL"}:
        return JSONResponse(status_code=400, content={"error": "Invalid trigger_on value"})

    telegram_token = (body.telegram_token or get_default_telegram_bot_token() or "").strip()
    telegram_chat_id = (body.telegram_chat_id or get_default_telegram_chat_id() or "").strip()
    if not telegram_token or not telegram_chat_id:
        return JSONResponse(
            status_code=400,
            content={"error": "Telegram token or chat id missing"},
        )

    subscription = TelegramAlertSubscription(
        symbol=normalized_symbol,
        telegram_token=telegram_token,
        telegram_chat_id=telegram_chat_id,
        trigger_on=normalized_trigger,
    )
    add_or_update_telegram_subscription(subscription)
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "message": "Telegram alert subscription saved",
            "symbol": normalized_symbol,
            "trigger_on": normalized_trigger,
            "telegram_configured": True,
        },
    )


@app.get("/api/alert/test/{symbol}")
async def test_alert(symbol: str) -> dict[str, object]:
    """Return the subscriptions that would match the current live signal for a symbol."""
    normalized_symbol = validate_symbol(symbol)
    signal_result = await signal_agent.analyze(normalized_symbol)
    final_signal = str(signal_result.get("final_signal", "HOLD"))
    matching_subscriptions = [
        subscription
        for subscription in alert_subscriptions
        if subscription.get("symbol") == normalized_symbol
        and subscription.get("trigger_on") == final_signal
    ]
    return {
        "symbol": normalized_symbol,
        "final_signal": final_signal,
        "score": signal_result.get("score", 0),
        "confidence_pct": signal_result.get("confidence_pct", 0.0),
        "matching_subscriptions": matching_subscriptions,
    }


@app.get("/apialert/test/{symbol}")
async def test_telegram_alert(symbol: str) -> dict[str, object]:
    """Send a Telegram test alert for a symbol using subscriptions or default config."""
    normalized_symbol = normalize_symbol(symbol)
    signal_payload = await build_enriched_signal_result(normalized_symbol)
    subscriptions = get_telegram_subscriptions_for_symbol(normalized_symbol)

    if not subscriptions:
        default_token = get_default_telegram_bot_token()
        default_chat_id = get_default_telegram_chat_id()
        if default_token and default_chat_id:
            await send_telegram_signal_alert(
                token=default_token,
                chat_id=default_chat_id,
                symbol=normalized_symbol,
                trigger_on="TEST",
                signal_payload=signal_payload,
            )
            return {
                "status": "ok",
                "message": f"Sent test alert for {normalized_symbol} to default chat",
                "subscriptions_count": 0,
            }
        return {
            "status": "ok",
            "message": f"No subscriptions and no default Telegram config for {normalized_symbol}",
            "subscriptions_count": 0,
        }

    for subscription in subscriptions:
        token = subscription.telegram_token or get_default_telegram_bot_token()
        chat_id = subscription.telegram_chat_id or get_default_telegram_chat_id()
        if not token or not chat_id:
            continue
        await send_telegram_signal_alert(
            token=token,
            chat_id=chat_id,
            symbol=normalized_symbol,
            trigger_on="TEST",
            signal_payload=signal_payload,
        )

    return {
        "status": "ok",
        "message": f"Sent test alerts for {normalized_symbol}",
        "subscriptions_count": len(subscriptions),
    }


@app.get("/api/debug/context/{symbol}")
async def get_debug_context(symbol: str) -> dict[str, object]:
    """Return the full unified analysis context for a symbol."""
    normalized_symbol = normalize_symbol(symbol)
    return await build_analysis_context(normalized_symbol)


@app.post("/api/ask")
async def ask_market_question(request: AskRequest) -> dict[str, object]:
    """Answer a trader question from the current three-market signal state."""
    target_symbols = extract_question_symbols(request.question)
    market_results = await asyncio.gather(
        *(build_enriched_signal_result(symbol) for symbol in target_symbols)
    )
    markets = {
        symbol: market_result
        for symbol, market_result in zip(target_symbols, market_results, strict=False)
    }
    analyst_state = {
        "markets": {
            symbol: market_result.get("analysis_context", {})
            for symbol, market_result in markets.items()
        },
        "session_accuracy": accuracy_tracker.get_stats(),
        "timestamp": utc_timestamp(),
    }
    answer = await narrator.answer_market_question(request.question, analyst_state)
    return {
        "question": request.question,
        "answer": answer,
        "markets": markets,
        "session_accuracy": analyst_state["session_accuracy"],
        "analysis_context": analyst_state,
    }


@app.get("/api/dashboard/overview")
async def get_dashboard_overview() -> dict[str, object]:
    """Return a compact live overview payload for the front-end dashboard."""
    try:
        return await asyncio.wait_for(asyncio.shield(build_dashboard_overview_payload()), timeout=10.0)
    except Exception as exc:
        logger.warning("Dashboard overview degraded: %s", exc)
        symbols = ("BTC-USDC", "ETH-USDC", "SOL-USDC")
        summaries = [
            build_dashboard_market_summary(signal_agent._neutral_signal(symbol, utc_timestamp()))
            for symbol in symbols
        ]
        return {
            "macro_alert": None,
            "timestamp": utc_timestamp(),
            "summary_cards": summaries,
            "markets": {
                symbol: signal_agent._neutral_signal(symbol, utc_timestamp())
                for symbol in symbols
            },
            "status": "degraded",
        }


@app.get("/api/dashboard/all-markets")
async def get_dashboard_all_markets() -> dict[str, object]:
    """Return the lightweight all-markets board for the dashboard."""
    try:
        return await asyncio.wait_for(asyncio.shield(build_all_markets_board_payload()), timeout=10.0)
    except Exception as exc:
        logger.warning("All-markets board degraded: %s", exc)
        return {"timestamp": utc_timestamp(), "all_markets_board": [], "status": "degraded"}


@app.get("/api/dashboard/all-markets/workspace")
async def get_dashboard_all_markets_workspace() -> dict[str, object]:
    """Return the frontdesk workspace for all-markets discovery mode."""
    try:
        workspace, board = await asyncio.wait_for(
            asyncio.shield(build_all_markets_frontdesk_workspace_payload()),
            timeout=12.0,
        )
    except Exception as exc:
        logger.warning("All-markets workspace degraded: %s", exc)
        workspace = build_all_markets_frontdesk_workspace([])
        board = []
    return {
        "timestamp": utc_timestamp(),
        "workspace": workspace,
        "all_markets_board": board,
    }


@app.get("/api/dashboard/market/{symbol}")
async def get_dashboard_market(symbol: str) -> dict[str, object]:
    """Return the full selected-market dashboard payload for the front end."""
    normalized_symbol = normalize_symbol(symbol)
    try:
        return await asyncio.wait_for(asyncio.shield(build_dashboard_market_payload(normalized_symbol)), timeout=18.0)
    except Exception as exc:
        logger.warning("Dashboard market degraded for %s: %s", normalized_symbol, exc)
        return build_minimal_dashboard_market_payload(normalized_symbol)


@app.post("/api/agent/ask")
async def ask_agent_question(request: AgentChatRequest) -> dict[str, object]:
    """Answer a dashboard chat question for a specific agent and symbol."""
    normalized_agent = validate_agent_key(request.agent)
    if request.symbol.strip().upper() == "ALL":
        workspace, board = await build_all_markets_frontdesk_workspace_payload()
        agent_chat_context = build_dashboard_agent_chat_context(
            question=request.question.strip(),
            workspace=workspace,
            reports={},
            all_markets_board=board,
        )
        answer = await narrator.answer_dashboard_agent_question(
            request.question.strip(),
            agent_chat_context,
        )
        return {
            "question": request.question,
            "answer": answer,
            "workspace": workspace,
            "symbol": "ALL",
            "agent": "frontdesk",
            "overall_verdict": "SCAN",
        }

    normalized_symbol = normalize_symbol(request.symbol)
    cached_market_payload = get_cached_value(f"dashboard-market:{normalized_symbol}")
    if isinstance(cached_market_payload, dict):
        market_payload = choose_best_dashboard_market_payload(normalized_symbol, cached_market_payload)
    else:
        market_payload = await build_fast_agent_chat_market_payload(normalized_symbol)
    signal_payload = market_payload.get("signal", {})
    reports = market_payload.get("reports", {})
    workspace = reports.get(normalized_agent)
    if not isinstance(workspace, dict):
        workspace = build_agent_workspace_payload(normalized_symbol, normalized_agent, signal_payload)
    agent_chat_context = build_dashboard_agent_chat_context(
        question=request.question.strip(),
        workspace=workspace,
        reports=reports if isinstance(reports, dict) else {},
        signal_payload=signal_payload if isinstance(signal_payload, dict) else None,
    )
    answer = await narrator.answer_dashboard_agent_question(
        request.question.strip(),
        agent_chat_context,
    )
    return {
        "question": request.question,
        "answer": answer,
        "workspace": workspace,
        "symbol": normalized_symbol,
        "agent": normalized_agent,
        "overall_verdict": signal_payload.get("final_signal", "HOLD"),
    }


@app.get("/")
async def get_dashboard_index() -> FileResponse:
    """Serve the dashboard index page when the UI bundle is present."""
    if not UI_DIR.exists():
        raise HTTPException(status_code=404, detail="UI directory not found")
    index_path = UI_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard index not found")
    return FileResponse(index_path)


@app.get("/style.css")
async def get_dashboard_styles() -> FileResponse:
    """Serve dashboard styles from the UI bundle."""
    css_path = UI_DIR / "style.css"
    if not css_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard stylesheet not found")
    return FileResponse(css_path, media_type="text/css")


@app.get("/script.js")
async def get_dashboard_script() -> FileResponse:
    """Serve dashboard script from the UI bundle."""
    script_path = UI_DIR / "script.js"
    if not script_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard script not found")
    return FileResponse(script_path, media_type="text/javascript")
