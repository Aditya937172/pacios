"""Telegram alert delivery for PacificaEdge."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN_DEFAULT = ""
TELEGRAM_CHAT_ID_DEFAULT = ""


async def send_telegram_signal_alert(
    token: str,
    chat_id: str,
    symbol: str,
    trigger_on: str,
    signal_payload: dict[str, Any],
) -> None:
    """Send a Telegram alert message for a signal payload."""
    decision = _extract_decision(signal_payload)
    altfins = _extract_altfins(signal_payload)
    news_context = _extract_news_context(signal_payload)
    score = _extract_score(signal_payload)
    confidence_pct = _extract_confidence(signal_payload)

    altfins_view = (
        altfins.get("summary_block", {}).get("altfins_view")
        or "No altFINS analytics"
    )
    top_themes = news_context.get("top_themes") or []
    themes_str = ", ".join(str(theme) for theme in top_themes[:3]) if top_themes else "No strong news themes"

    text = (
        f"{symbol} signal: {decision}\n"
        f"Trigger: {trigger_on}\n"
        f"Score: {score} | Confidence: {confidence_pct:.1f}%\n\n"
        f"AltFINS view: {altfins_view}\n"
        f"News themes: {themes_str}"
    )

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(api_url, json=payload)
            response.raise_for_status()
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        logger.warning("Failed to send Telegram alert for %s: %s", symbol, exc)
    except Exception:
        logger.exception("Failed to send Telegram alert for %s", symbol)


def get_default_telegram_bot_token() -> str:
    """Return the default Telegram bot token from the current environment."""
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def get_default_telegram_chat_id() -> str:
    """Return the default Telegram chat id from the current environment."""
    return os.getenv("TELEGRAM_CHAT_ID", "").strip()


def _extract_decision(signal_payload: dict[str, Any]) -> str:
    """Extract the normalized signal decision from a full signal payload or analysis context."""
    signal_engine = signal_payload.get("signal_engine", {})
    if isinstance(signal_engine, dict):
        decision = signal_engine.get("decision") or signal_engine.get("final_signal")
        if isinstance(decision, str) and decision.strip():
            return decision.strip().upper()
    decision = signal_payload.get("final_signal")
    if isinstance(decision, str) and decision.strip():
        return decision.strip().upper()
    return "UNKNOWN"


def _extract_score(signal_payload: dict[str, Any]) -> int:
    """Extract the signal score from a full signal payload or analysis context."""
    signal_engine = signal_payload.get("signal_engine", {})
    if isinstance(signal_engine, dict):
        value = signal_engine.get("score")
        if value is not None:
            return _to_int(value)
    return _to_int(signal_payload.get("score", 0))


def _extract_confidence(signal_payload: dict[str, Any]) -> float:
    """Extract the confidence percentage from a full signal payload or analysis context."""
    signal_engine = signal_payload.get("signal_engine", {})
    if isinstance(signal_engine, dict):
        value = signal_engine.get("confidence_pct")
        if value is not None:
            return _to_float(value)
    return _to_float(signal_payload.get("confidence_pct", 0.0))


def _extract_altfins(signal_payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the altFINS block from a full signal payload or analysis context."""
    value = signal_payload.get("altfins", {})
    return value if isinstance(value, dict) else {}


def _extract_news_context(signal_payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the news context block from a full signal payload or analysis context."""
    value = signal_payload.get("news_context", {})
    return value if isinstance(value, dict) else {}


def _to_float(value: Any) -> float:
    """Convert a value to float safely."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    """Convert a value to int safely."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
