"""Current-affairs agent for PacificaEdge."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

from services.current_affairs import fetch_current_affairs_context
from services.nemo_llm import NeMoClient


class CurrentAgent:
    """Summarize live current-affairs context for a market without affecting core signals."""

    def __init__(self, nemo_client: NeMoClient | None) -> None:
        """Initialize the current-affairs agent."""
        self.nemo_client = nemo_client

    async def run(
        self,
        symbol: str,
        narrative_summary: str | None = None,
        fast: bool = False,
    ) -> dict[str, Any]:
        """Fetch and summarize live current-affairs context."""
        normalized_symbol = symbol.upper()
        token = normalized_symbol.split("-")[0].upper() if normalized_symbol else ""
        timestamp = self._timestamp()

        try:
            context = await fetch_current_affairs_context(
                normalized_symbol,
                narrative_summary=narrative_summary,
            )
            summary = await self._summarize(token, context, fast=fast)
            signal = self._signal_from_context(context)
            return {
                "agent": "CurrentAffairsAgent",
                "symbol": normalized_symbol,
                "token": token,
                "available": bool(context.get("available")),
                "headlines": context.get("headlines", []),
                "top_themes": context.get("top_themes", []),
                "bullish_hits": context.get("bullish_hits", 0),
                "bearish_hits": context.get("bearish_hits", 0),
                "signal": signal,
                "signal_value": 1 if signal == "BULLISH" else -1 if signal == "BEARISH" else 0,
                "summary": summary,
                "reason": self._reason_from_context(context, signal),
                "source_status": context.get("source_status", "unavailable"),
                "powered_by": self._powered_by(),
                "timestamp": timestamp,
            }
        except Exception as exc:
            return {
                "agent": "CurrentAffairsAgent",
                "symbol": normalized_symbol,
                "token": token,
                "available": False,
                "headlines": [],
                "top_themes": [],
                "bullish_hits": 0,
                "bearish_hits": 0,
                "signal": "NEUTRAL",
                "signal_value": 0,
                "summary": f"No fresh current-affairs context is available for {token or normalized_symbol}.",
                "reason": "Current-affairs web search is temporarily unavailable.",
                "source_status": "unavailable",
                "powered_by": self._powered_by(),
                "timestamp": timestamp,
                "error": str(exc),
            }

    async def _summarize(self, token: str, context: dict[str, Any], fast: bool = False) -> str:
        """Build a concise current-affairs summary, optionally refined by NeMo."""
        headlines = context.get("headlines", [])
        top_themes = context.get("top_themes", [])
        if not isinstance(headlines, list) or not headlines:
            return f"No fresh current-affairs context is available for {token}."

        lead_title = str(headlines[0].get("title", "the latest market headline")).strip()
        theme_text = ", ".join(str(theme) for theme in top_themes[:2] if isinstance(theme, str) and theme)
        deterministic = (
            f"{token} current affairs are led by {lead_title}."
            f"{' Themes include ' + theme_text + '.' if theme_text else ''}"
        )

        use_llm = os.getenv("ENABLE_CURRENT_AFFAIRS_LLM", "true").strip().lower() == "true"
        if fast or not use_llm or self.nemo_client is None:
            return deterministic

        system_prompt = (
            "You are a concise crypto news analyst. "
            "Return exactly one JSON object with a single string field named summary. "
            "Keep the summary to two short sentences maximum."
        )
        user_prompt = (
            f"Token: {token}\n"
            f"Themes: {top_themes[:4]}\n"
            f"Headlines: {[item.get('title') for item in headlines[:4] if isinstance(item, dict)]}\n"
            'Return {"summary":"..."}'
        )
        try:
            result = await asyncio.wait_for(
                self.nemo_client.chat_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=120,
                ),
                timeout=1.6,
            )
            summary = str(result.get("summary", "")).strip() if isinstance(result, dict) else ""
            return summary or deterministic
        except Exception:
            return deterministic

    def _signal_from_context(self, context: dict[str, Any]) -> str:
        """Return a lightweight directional hint from the headline set."""
        bullish_hits = int(context.get("bullish_hits", 0) or 0)
        bearish_hits = int(context.get("bearish_hits", 0) or 0)
        if bullish_hits > bearish_hits:
            return "BULLISH"
        if bearish_hits > bullish_hits:
            return "BEARISH"
        return "NEUTRAL"

    def _reason_from_context(self, context: dict[str, Any], signal: str) -> str:
        """Explain the current-affairs read without overstating conviction."""
        headlines = context.get("headlines", [])
        top_themes = context.get("top_themes", [])
        headline_count = len(headlines) if isinstance(headlines, list) else 0
        theme_text = ", ".join(str(theme) for theme in top_themes[:3] if isinstance(theme, str) and theme)
        if signal == "BULLISH":
            return f"Live current-affairs search found {headline_count} fresh headlines with a constructive tilt. {theme_text}".strip()
        if signal == "BEARISH":
            return f"Live current-affairs search found {headline_count} fresh headlines with a defensive tilt. {theme_text}".strip()
        return f"Live current-affairs search found {headline_count} fresh headlines, but the tone is mixed. {theme_text}".strip()

    def _powered_by(self) -> str:
        """Describe the current-affairs stack."""
        if self.nemo_client is not None and os.getenv("ENABLE_CURRENT_AFFAIRS_LLM", "true").strip().lower() == "true":
            return "Google News RSS + NeMo"
        return "Google News RSS"

    def _timestamp(self) -> str:
        """Return the current UTC timestamp."""
        return datetime.now(timezone.utc).isoformat()
