"""Sentiment analysis agent for PacificaEdge."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from services.current_affairs import fetch_current_affairs_context
from services.elfa import ElfaClient

logger = logging.getLogger(__name__)


class SentimentAgent:
    """Analyze token social sentiment using Elfa AI."""

    def __init__(self, elfa_client: ElfaClient | None) -> None:
        """Initialize the agent with an Elfa client dependency."""
        self.elfa_client = elfa_client

    async def analyze(self, symbol: str) -> dict[str, Any]:
        """Analyze social sentiment for a supported market symbol."""
        timestamp = self._timestamp()
        token = symbol.split("-")[0].upper() if symbol else ""

        try:
            if not token:
                raise ValueError(f"Unsupported symbol '{symbol}'")
            if self.elfa_client is None:
                raise ValueError("Elfa client is unavailable")

            trending_data, mentions_data = await asyncio.wait_for(
                asyncio.gather(
                    self.elfa_client.get_trending_tokens(),
                    self.elfa_client.get_token_sentiment(token),
                ),
                timeout=2.4,
            )
            if "error" in trending_data:
                raise ValueError(str(trending_data["error"]))
            if "error" in mentions_data:
                raise ValueError(str(mentions_data["error"]))

            trending_tokens = self._extract_trending_tokens(trending_data)
            average_count = self._average_trending_count(trending_tokens)
            token_entry, rank_in_trending = self._find_trending_entry(trending_tokens, token)
            mention_count = self._extract_mentions_count(mentions_data)

            if token_entry is None or average_count <= 0 or mention_count <= 0:
                return self._fallback(
                    symbol=symbol,
                    token=token,
                    timestamp=timestamp,
                    mention_count=mention_count,
                )

            trending_count = self._extract_integer(token_entry.get("current_count"))
            sentiment_score = self._calculate_sentiment_score(trending_count, average_count)
            signal = self._determine_signal(trending_count, average_count)
            reason = self._build_reason(token, rank_in_trending, mention_count)

            logger.info("Sentiment analysis computed for %s", symbol)
            payload = {
                "agent": "SentimentAgent",
                "symbol": symbol,
                "token": token,
                "sentiment_score": sentiment_score,
                "mention_count_24h": mention_count,
                "is_trending": True,
                "rank_in_trending": rank_in_trending,
                "signal": signal,
                "reason": reason,
                "powered_by": "Elfa AI",
                "timestamp": timestamp,
            }
            return payload
        except Exception as exc:
            logger.warning("Sentiment analysis failed for %s: %s", symbol, exc)
            fallback = await self._fallback_from_current_affairs(symbol=symbol, token=token, timestamp=timestamp)
            fallback["error"] = str(exc)
            return fallback

    async def run(self, symbol: str) -> dict[str, Any]:
        """Run the sentiment agent with a guaranteed neutral fallback on failure."""
        try:
            return await self.analyze(symbol)
        except Exception as exc:
            token = symbol.split("-")[0].upper() if symbol else ""
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {self.__class__.__name__} ERROR: {exc}")
            fallback = self._fallback(symbol=symbol, token=token, timestamp=self._timestamp())
            fallback["error"] = str(exc)
            fallback["signal_value"] = 0
            return fallback

    def _extract_trending_tokens(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract the trending token list from the Elfa response."""
        data = payload.get("data")
        if isinstance(data, dict):
            nested = data.get("data")
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    def _average_trending_count(self, items: list[dict[str, Any]]) -> float:
        """Calculate the average mention count across trending tokens."""
        counts = [self._extract_integer(item.get("current_count")) for item in items]
        valid_counts = [count for count in counts if count > 0]
        if not valid_counts:
            return 0.0
        return sum(valid_counts) / len(valid_counts)

    def _find_trending_entry(
        self, items: list[dict[str, Any]], token: str
    ) -> tuple[dict[str, Any] | None, int | None]:
        """Find a token entry and its 1-based rank in the trending list."""
        normalized_token = token.lower()
        for index, item in enumerate(items, start=1):
            item_token = item.get("token")
            if isinstance(item_token, str) and item_token.lower() == normalized_token:
                return item, index
        return None, None

    def _extract_mentions_count(self, payload: dict[str, Any]) -> int:
        """Extract the 24-hour mention count from top mentions metadata."""
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            total = self._extract_integer(metadata.get("total"))
            if total > 0:
                return total

        data = payload.get("data")
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            total = self._extract_integer(data.get("total"))
            if total > 0:
                return total
            nested = data.get("data")
            if isinstance(nested, list):
                return len(nested)

        return 0

    def _extract_integer(self, value: Any) -> int:
        """Convert a value to integer safely."""
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    def _calculate_sentiment_score(self, our_count: int, average_count: float) -> float:
        """Calculate a bounded 0-100 sentiment score from relative mention volume."""
        if average_count <= 0:
            return 0.0
        score = (our_count / average_count) * 50
        return max(0.0, min(100.0, round(score, 1)))

    def _determine_signal(self, our_count: int, average_count: float) -> str:
        """Determine the directional signal from relative mention volume."""
        if average_count <= 0:
            return "NEUTRAL"
        if our_count > average_count * 1.5:
            return "BULLISH"
        if our_count < average_count * 0.5:
            return "BEARISH"
        return "NEUTRAL"

    def _build_reason(self, token: str, rank_in_trending: int | None, mention_count: int) -> str:
        """Build a human-readable explanation for the sentiment result."""
        if rank_in_trending is None:
            return "Insufficient social data"
        return (
            f"{token} is ranked #{rank_in_trending} in trending tokens with "
            f"{mention_count:,} mentions in 24h"
        )

    def _fallback(
        self,
        symbol: str,
        token: str,
        timestamp: str,
        mention_count: int = 0,
    ) -> dict[str, Any]:
        """Return the neutral fallback response."""
        return {
            "agent": "SentimentAgent",
            "symbol": symbol,
            "token": token,
            "sentiment_score": 50,
            "mention_count_24h": mention_count,
            "is_trending": False,
            "rank_in_trending": None,
            "signal": "NEUTRAL",
            "signal_value": 0,
            "reason": "Insufficient social data",
            "powered_by": "Elfa AI",
            "timestamp": timestamp,
        }

    async def _fallback_from_current_affairs(
        self,
        symbol: str,
        token: str,
        timestamp: str,
    ) -> dict[str, Any]:
        """Use fresh current-affairs context when Elfa is unavailable or rate-limited."""
        current_affairs = await fetch_current_affairs_context(symbol)
        news_payload = self._news_attention_payload(
            symbol=symbol,
            token=token,
            timestamp=timestamp,
            news_context=current_affairs,
        )
        if news_payload is not None:
            return news_payload
        return self._fallback(symbol=symbol, token=token, timestamp=timestamp)

    def _news_attention_payload(
        self,
        symbol: str,
        token: str,
        timestamp: str,
        news_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Build a market-attention fallback from live current-affairs search."""
        if not isinstance(news_context, dict) or not news_context.get("available"):
            return None
        headlines = news_context.get("headlines", [])
        top_themes = news_context.get("top_themes", [])
        if not isinstance(headlines, list) or not headlines:
            return None

        attention_score = min(100.0, 45.0 + (len(headlines) * 8.0) + (len(top_themes) * 4.0))
        bullish_themes = {"institutional_flows", "etf_flows", "network_activity"}
        bearish_themes = {"security_risk", "regulation", "liquidations"}
        if any(theme in bullish_themes for theme in top_themes):
            signal = "BULLISH"
        elif any(theme in bearish_themes for theme in top_themes):
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        lead_title = str(headlines[0].get("title", "the latest headline")) if isinstance(headlines[0], dict) else "the latest headline"
        return {
            "agent": "SentimentAgent",
            "symbol": symbol,
            "token": token,
            "sentiment_score": round(attention_score, 1),
            "mention_count_24h": len(headlines),
            "is_trending": True,
            "rank_in_trending": None,
            "signal": signal,
            "signal_value": 1 if signal == "BULLISH" else -1 if signal == "BEARISH" else 0,
            "reason": f"Elfa is unavailable, so this attention read is using fresh current-affairs headlines led by {lead_title}.",
            "powered_by": "Current-affairs web search fallback",
            "timestamp": timestamp,
        }

    def _timestamp(self) -> str:
        """Return the current UTC timestamp in ISO 8601 format."""
        return datetime.now(timezone.utc).isoformat()
