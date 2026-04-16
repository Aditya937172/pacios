"""Async Elfa AI API client."""

from __future__ import annotations

import logging
import os
from typing import Any, List

import httpx

logger = logging.getLogger(__name__)


def _clean_secret(value: str | None) -> str:
    """Normalize env-provided secrets so stray quotes or CRLF do not break headers."""
    if not isinstance(value, str):
        return ""
    return value.strip().strip("\"'").replace("\r", "").replace("\n", "").strip()


class ElfaClient:
    """Async client for the Elfa AI API."""

    def __init__(self, api_key: str | None = None, timeout: float = 5.0) -> None:
        """Initialize the Elfa AI client."""
        resolved_api_key = _clean_secret(api_key or os.getenv("ELFA_API_KEY"))
        if not resolved_api_key:
            raise ValueError("ELFA_API_KEY is not set")
        self.api_key = resolved_api_key
        self.base_url = "https://api.elfa.ai/v2"
        self.timeout = timeout

    async def get_token_sentiment(self, token: str) -> dict[str, Any]:
        """Fetch top 24-hour mentions for a token ticker."""
        return await self._get(
            path="/data/top-mentions",
            params={
                "ticker": token,
                "timeWindow": "24h",
                "page": 1,
                "pageSize": 20,
            },
        )

    async def get_trending_tokens(self) -> dict[str, Any]:
        """Fetch trending tokens over the last 24 hours."""
        return await self._get(
            path="/aggregations/trending-tokens",
            params={"timeWindow": "24h"},
        )

    async def get_top_mentions(self, token: str) -> dict[str, Any]:
        """Return top social posts mentioning the given token over the last 24 hours."""
        url = f"{self.base_url}/data/top-mentions"
        headers = {"x-elfa-api-key": self.api_key}
        params = {"ticker": token, "timeWindow": "24h"}

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                payload: Any = response.json()
                if isinstance(payload, dict):
                    return payload
                return {"data": payload}
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            logger.warning("Elfa top mentions transport failed for %s: %s", url, exc)
            return {"error": str(exc), "data": []}
        except Exception as exc:
            logger.warning("Elfa top mentions request failed for %s: %s", url, exc)
            return {"error": str(exc), "data": []}

    async def get_top_mentions_text_summaries(self, token: str, limit: int = 10) -> List[str]:
        """Build synthetic human-readable summaries from Elfa top-mentions metadata."""
        payload = await self.get_top_mentions(token)
        if payload.get("error"):
            return []

        data = payload.get("data")
        if not isinstance(data, list):
            return []

        summaries: List[str] = []
        for item in data[:limit]:
            if not isinstance(item, dict):
                continue
            post_type = self._to_label(item.get("type"), "post")
            like_count = self._to_int(item.get("likeCount"))
            repost_count = self._to_int(item.get("repostCount"))
            view_count = self._to_int(item.get("viewCount"))
            reply_count = self._to_int(item.get("replyCount"))
            quote_count = self._to_int(item.get("quoteCount"))
            mentioned_at = self._to_label(item.get("mentionedAt"), "")
            link = self._to_label(item.get("link"), "")

            parts = [
                f"{post_type} post with {like_count} likes, {repost_count} reposts, {view_count} views about {token}."
            ]
            if reply_count > 0:
                parts.append(f"It has {reply_count} replies.")
            if quote_count > 0:
                parts.append(f"It has {quote_count} quotes.")
            if mentioned_at:
                parts.append(f"Mentioned at {mentioned_at}.")
            if link:
                parts.append(f"Source: {link}.")
            summaries.append(" ".join(parts))

        return summaries

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute an authenticated GET request against the Elfa API."""
        url = f"{self.base_url}{path}"
        headers = {"x-elfa-api-key": self.api_key}

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                payload: Any = response.json()
                if isinstance(payload, dict):
                    return payload
                return {"data": payload}
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            logger.warning("Elfa API transport failed for %s: %s", url, exc)
            return {"error": str(exc), "data": []}
        except Exception as exc:
            logger.warning("Elfa API request failed for %s: %s", url, exc)
            return {"error": str(exc), "data": []}

    def _to_int(self, value: Any) -> int:
        """Convert a value to a non-negative integer safely."""
        try:
            return max(0, int(float(value)))
        except (TypeError, ValueError):
            return 0

    def _to_label(self, value: Any, default: str) -> str:
        """Convert a value to a cleaned string label."""
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                return cleaned
        return default
