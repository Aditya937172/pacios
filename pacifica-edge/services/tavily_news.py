"""Tavily news context service for PacificaEdge."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


def _clean_secret(value: str | None) -> str:
    """Normalize env-provided secrets so quotes or CRLF do not break auth headers."""
    if not isinstance(value, str):
        return ""
    return value.strip().strip("\"'").replace("\r", "").replace("\n", "").strip()


async def fetch_news_context(symbol: str, narrative_summary: str | None = None) -> dict[str, Any]:
    """Fetch a compact Tavily news context block for a PacificaEdge symbol."""
    underlying = _underlying_symbol(symbol)
    if not underlying:
        return _fallback_news_context("")

    api_key = _clean_secret(os.getenv("TAVILY_API_KEY"))
    if not api_key:
        logger.warning("TAVILY_API_KEY is not set")
        return _fallback_news_context(underlying)

    query = _build_query(underlying, narrative_summary)
    payload = {
        "query": query,
        "max_results": 5,
        "search_depth": "basic",
        "topic": "news",
        "time_range": "day",
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post("https://api.tavily.com/search", headers=headers, json=payload)
            response.raise_for_status()
            data: Any = response.json()
        results = data.get("results", []) if isinstance(data, dict) else []
        headlines = _normalize_headlines(results, underlying)
        if not headlines:
            return _fallback_news_context(underlying)
        return {
            "available": True,
            "symbol": underlying,
            "headlines": headlines,
            "top_themes": _derive_top_themes(headlines),
        }
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        logger.warning("Tavily transport failed for %s: %s", underlying, exc)
        return _fallback_news_context(underlying)
    except Exception as exc:
        logger.warning("Tavily news fetch failed for %s: %s", underlying, exc)
        return _fallback_news_context(underlying)


async def sanity_check_tavily_news(symbol: str = "BTC-USDC") -> dict[str, Any]:
    """Run a simple sanity check against the Tavily news client and log the normalized shape."""
    result = await fetch_news_context(symbol)
    logger.info("Tavily sanity check for %s: %s", symbol, result)
    return result


def _underlying_symbol(symbol: str) -> str:
    """Derive the underlying ticker from a PacificaEdge symbol."""
    if not symbol:
        return ""
    underlying = str(symbol).split("-")[0].strip().upper()
    if not underlying or not underlying.isalnum():
        return ""
    return underlying


def _build_query(underlying: str, narrative_summary: str | None) -> str:
    """Build a Tavily search query for recent crypto market context."""
    base_query = f'"{underlying}" crypto token coin perp futures price funding liquidations news last 24 hours'
    cleaned_narrative = " ".join((narrative_summary or "").split()).strip()
    if not cleaned_narrative:
        return base_query
    short_narrative = " ".join(cleaned_narrative.split()[:12])
    return f'"{underlying}" crypto token coin {short_narrative} last 24 hours'


def _normalize_headlines(results: Any, underlying: str) -> list[dict[str, Any]]:
    """Normalize Tavily search results into a compact headline list."""
    if not isinstance(results, list):
        return []

    normalized: list[dict[str, Any]] = []
    fallback_candidates: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = _to_string(item.get("url"))
        title = _to_string(item.get("title"))
        if not title or not url:
            continue
        normalized_item = {
            "title": title,
            "source": _extract_source(item, url),
            "published_at": _extract_published_at(item),
            "url": url,
            "summary": _extract_summary(item),
        }
        fallback_candidates.append(normalized_item)
        if _is_relevant_result(item, underlying):
            normalized.append(normalized_item)
    if normalized:
        return normalized
    return fallback_candidates[:3]


def _is_relevant_result(item: dict[str, Any], underlying: str) -> bool:
    """Filter Tavily results down to asset-relevant crypto headlines."""
    title = _to_string(item.get("title")).lower()
    combined = " ".join(
        _to_string(item.get(key)).lower()
        for key in ("title", "content", "summary", "snippet")
    )
    if not combined:
        return False

    aliases = _symbol_aliases(underlying)
    crypto_markers = ("crypto", "token", "coin", "bitcoin", "ethereum", "solana", "blockchain", "exchange", "etf")
    has_alias_in_title = any(alias in title for alias in aliases)
    has_alias = any(alias in combined for alias in aliases)
    has_crypto_marker = any(marker in combined for marker in crypto_markers)
    return has_alias and (has_crypto_marker or has_alias_in_title)


def _symbol_aliases(underlying: str) -> tuple[str, ...]:
    """Return a small alias set for common crypto assets."""
    normalized = underlying.lower()
    alias_map = {
        "btc": ("btc", "bitcoin"),
        "eth": ("eth", "ethereum", "ether"),
        "sol": ("sol", "solana"),
        "xrp": ("xrp", "ripple"),
        "ton": ("ton", "the open network", "toncoin"),
        "ada": ("ada", "cardano"),
        "doge": ("doge", "dogecoin"),
    }
    return alias_map.get(normalized, (normalized,))


def _derive_top_themes(headlines: list[dict[str, Any]]) -> list[str]:
    """Derive a small deterministic set of news themes from titles and summaries."""
    if not headlines:
        return []

    keyword_buckets = [
        ("etf_flows", ("etf", "inflow", "outflow")),
        ("liquidations", ("liquidation", "squeeze", "leveraged", "wipeout")),
        ("regulation", ("sec", "regulation", "legal", "lawsuit", "policy")),
        ("macro_data", ("fed", "inflation", "cpi", "pce", "rates", "macro")),
        ("institutional_flows", ("institutional", "blackrock", "treasury", "fund", "whale")),
        ("network_activity", ("upgrade", "staking", "validator", "adoption", "partnership")),
        ("security_risk", ("hack", "exploit", "breach", "security")),
    ]

    combined_text = " ".join(
        f"{headline.get('title', '')} {headline.get('summary', '')}".lower()
        for headline in headlines
    )
    themes = [label for label, keywords in keyword_buckets if any(keyword in combined_text for keyword in keywords)]
    if themes:
        return themes[:5]
    return ["price_action", "market_context"]


def _extract_source(item: dict[str, Any], url: str) -> str | None:
    """Extract a human-readable source name from a Tavily result."""
    for key in ("source", "site_name", "domain"):
        value = _to_string(item.get(key))
        if value:
            return value
    parsed = urlparse(url)
    return parsed.netloc or None


def _extract_published_at(item: dict[str, Any]) -> str | None:
    """Extract a publish timestamp when Tavily provides one."""
    for key in ("published_at", "published_date", "publishedAt", "date"):
        value = _to_string(item.get(key))
        if value:
            return value
    return None


def _extract_summary(item: dict[str, Any]) -> str | None:
    """Extract the best available content summary from a Tavily result."""
    for key in ("content", "summary", "snippet"):
        value = _to_string(item.get(key))
        if value:
            return value
    return None


def _fallback_news_context(symbol: str) -> dict[str, Any]:
    """Return the safe fallback news context block."""
    return {
        "available": False,
        "symbol": symbol,
        "headlines": [],
        "top_themes": [],
    }


def _to_string(value: Any) -> str:
    """Convert a value to a stripped string safely."""
    if value is None:
        return ""
    return str(value).strip()
