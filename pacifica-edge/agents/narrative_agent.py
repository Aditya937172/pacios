"""Narrative analysis agent for PacificaEdge."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from typing import Any, Dict

from services.current_affairs import fetch_current_affairs_context
from services.elfa import ElfaClient
from services.nemo_llm import NeMoClient

logger = logging.getLogger(__name__)


class NarrativeAgent:
    """Analyze token narratives using Elfa social data and NeMo reasoning."""

    def __init__(
        self, elfa_client: ElfaClient | None, nemo_client: NeMoClient | None
    ) -> None:
        """Initialize NarrativeAgent with Elfa and NeMo clients."""
        self.elfa_client = elfa_client
        self.nemo_client = nemo_client
        self.fallback_reason = "Insufficient data from Elfa AI or NeMo 120B."

    async def analyze(self, symbol: str) -> dict[str, Any]:
        """Analyze social narratives around a supported Pacifica symbol."""
        normalized_symbol = symbol.upper()
        token = self._token_from_symbol(normalized_symbol)

        try:
            if self.elfa_client is None:
                return await self._news_or_neutral_payload(
                    symbol=normalized_symbol,
                    token=token,
                    reason=self.fallback_reason,
                )
            if self.nemo_client is None:
                return await self._news_or_neutral_payload(
                    symbol=normalized_symbol,
                    token=token,
                    reason=self.fallback_reason,
                )

            summaries = await asyncio.wait_for(
                self.elfa_client.get_top_mentions_text_summaries(token, limit=10),
                timeout=2.4,
            )
            if not summaries:
                return await self._news_or_neutral_payload(
                    symbol=normalized_symbol,
                    token=token,
                    reason=self.fallback_reason,
                )

            system_prompt = (
                "You are an expert crypto market analyst. "
                "You must respond with exactly one JSON object and nothing else. "
                "Do not include markdown, analysis, or commentary outside the JSON object."
            )
            user_prompt = self._build_user_prompt(token, normalized_symbol, summaries)
            result = await asyncio.wait_for(
                self.nemo_client.chat_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=400,
                ),
                timeout=2.4,
            )
            if "error" in result:
                return await self._news_or_neutral_payload(
                    symbol=normalized_symbol,
                    token=token,
                    reason=self.fallback_reason,
                )

            signal = self._normalize_signal(result.get("signal"))
            confidence = self._normalize_confidence(result.get("confidence"))
            bullish_hits = self._to_int(result.get("bullish_hits"))
            bearish_hits = self._to_int(result.get("bearish_hits"))
            narrative_summary = self._to_string(
                result.get("narrative_summary"), "No narrative data available"
            )
            reason = self._to_string(result.get("reason"), "Narrative signal could not be derived")

            payload = {
                "agent": "NarrativeAgent",
                "symbol": normalized_symbol,
                "token": token,
                "bullish_hits": bullish_hits,
                "bearish_hits": bearish_hits,
                "narrative_summary": narrative_summary,
                "signal": signal,
                "confidence": confidence,
                "reason": reason,
                "powered_by": "Elfa AI + NeMo 120B",
                "timestamp": self._timestamp(),
            }
            return payload
        except Exception as exc:
            logger.exception("Narrative analysis failed for %s", normalized_symbol)
            fallback = await self._news_or_neutral_payload(
                symbol=normalized_symbol,
                token=token,
                reason=self.fallback_reason,
            )
            fallback["error"] = str(exc)
            return fallback

    async def run(self, symbol: str) -> dict[str, Any]:
        """Run the narrative agent with a guaranteed neutral fallback on failure."""
        normalized_symbol = symbol.upper()
        token = self._token_from_symbol(normalized_symbol)
        try:
            return await self.analyze(symbol)
        except Exception as exc:
            print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] {self.__class__.__name__} ERROR: {exc}")
            fallback = self._neutral_payload(
                symbol=normalized_symbol,
                token=token,
                reason=self.fallback_reason,
            )
            fallback["error"] = str(exc)
            return fallback

    def _build_user_prompt(self, token: str, symbol: str, summaries: list[str]) -> str:
        """Build the NeMo user prompt from synthetic Elfa summaries."""
        numbered_posts = "\n".join(
            f"{index}. {self._clean_text(summary)}"
            for index, summary in enumerate(summaries, start=1)
        )
        return f"""
Token: {token}
Symbol: {symbol}

These lines are synthetic summaries built from social engagement metadata, not tweet text.
Use engagement strength, recency, and concentration of attention to infer the likely market narrative.
If many recent posts have strong engagement, lean BULLISH unless the metadata strongly implies fear or stress.
If engagement is weak or fading, lean BEARISH.
If the evidence is balanced, choose NEUTRAL.

Here are up to 10 recent social posts summarized from Elfa AI metadata:

POSTS:
{numbered_posts}

Return exactly one JSON object with this schema:

{{
  "signal": "BULLISH" | "BEARISH" | "NEUTRAL",
  "confidence": "LOW" | "MEDIUM" | "HIGH",
  "bullish_hits": <int>,
  "bearish_hits": <int>,
  "narrative_summary": "<short sentence>",
  "reason": "<one sentence explanation>"
}}
""".strip()

    def _clean_text(self, value: str) -> str:
        """Normalize whitespace in a synthetic summary before sending it to NeMo."""
        return " ".join(value.split())

    def _normalize_signal(self, value: Any) -> str:
        """Normalize a model signal value into the supported enum."""
        candidate = self._to_string(value, "NEUTRAL").upper()
        if candidate in {"BULLISH", "BEARISH", "NEUTRAL"}:
            return candidate
        return "NEUTRAL"

    def _normalize_confidence(self, value: Any) -> str:
        """Normalize a model confidence value into the supported enum."""
        candidate = self._to_string(value, "LOW").upper()
        if candidate in {"LOW", "MEDIUM", "HIGH"}:
            return candidate
        return "LOW"

    def _to_int(self, value: Any) -> int:
        """Convert a value to int safely."""
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    def _to_string(self, value: Any, default: str) -> str:
        """Convert a value to string safely with a fallback."""
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                return cleaned
        return default

    def _neutral_payload(self, symbol: str, token: str, reason: str) -> Dict[str, Any]:
        """Return the neutral narrative payload."""
        return {
            "agent": "NarrativeAgent",
            "symbol": symbol,
            "token": token,
            "bullish_hits": 0,
            "bearish_hits": 0,
            "narrative_summary": "No narrative data available",
            "signal": "NEUTRAL",
            "signal_value": 0,
            "confidence": "LOW",
            "reason": reason,
            "powered_by": "Elfa AI + NeMo 120B",
            "timestamp": self._timestamp(),
        }

    async def _news_or_neutral_payload(self, symbol: str, token: str, reason: str) -> Dict[str, Any]:
        """Use live current-affairs context as a narrative fallback when Elfa/NeMo is thin."""
        news_context = await fetch_current_affairs_context(symbol)
        news_payload = self._news_fallback_payload(symbol=symbol, token=token, news_context=news_context)
        if news_payload is not None:
            return news_payload
        return self._neutral_payload(symbol=symbol, token=token, reason=reason)

    def _news_fallback_payload(
        self,
        symbol: str,
        token: str,
        news_context: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        """Build a narrative read from fresh web headlines when social coverage is unavailable."""
        if not isinstance(news_context, dict) or not news_context.get("available"):
            return None
        headlines = news_context.get("headlines", [])
        top_themes = news_context.get("top_themes", [])
        if not isinstance(headlines, list) or not headlines:
            return None

        text_blob = " ".join(
            f"{item.get('title', '')} {item.get('summary', '')}".lower()
            for item in headlines
            if isinstance(item, dict)
        )
        bullish_markers = ("inflow", "adoption", "partnership", "record high", "reserve", "superpower", "approval")
        bearish_markers = ("lawsuit", "hack", "exploit", "stalls", "rejection", "sell-off", "outflow")
        bullish_hits = sum(text_blob.count(marker) for marker in bullish_markers)
        bearish_hits = sum(text_blob.count(marker) for marker in bearish_markers)
        if "institutional_flows" in top_themes or "etf_flows" in top_themes:
            bullish_hits += 2
        if "security_risk" in top_themes or "regulation" in top_themes:
            bearish_hits += 2

        if bullish_hits > bearish_hits:
            signal = "BULLISH"
        elif bearish_hits > bullish_hits:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        confidence = "HIGH" if abs(bullish_hits - bearish_hits) >= 3 else "MEDIUM" if abs(bullish_hits - bearish_hits) >= 1 else "LOW"
        lead_title = str(headlines[0].get("title", "No headline")) if isinstance(headlines[0], dict) else "No headline"
        theme_text = ", ".join(str(theme) for theme in top_themes[:2]) if isinstance(top_themes, list) else ""
        theme_clause = f" Themes are centered on {theme_text}." if theme_text else ""
        return {
            "agent": "NarrativeAgent",
            "symbol": symbol,
            "token": token,
            "bullish_hits": bullish_hits,
            "bearish_hits": bearish_hits,
            "narrative_summary": f"Current affairs for {token} are led by {lead_title}.{theme_clause}",
            "signal": signal,
            "signal_value": 1 if signal == "BULLISH" else -1 if signal == "BEARISH" else 0,
            "confidence": confidence,
            "reason": f"Current-affairs web search sees {bullish_hits} bullish cues and {bearish_hits} bearish cues across the latest headlines.",
            "powered_by": "Current-affairs web search fallback",
            "timestamp": self._timestamp(),
        }

    def _token_from_symbol(self, symbol: str) -> str:
        """Extract the base token from a market symbol."""
        return symbol.split("-")[0].upper() if symbol else ""

    def _timestamp(self) -> str:
        """Return the current UTC timestamp in ISO 8601 format."""
        return datetime.utcnow().isoformat() + "Z"
