"""Live current-affairs lookup for PacificaEdge."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)


def _underlying_symbol(symbol: str) -> str:
    """Derive the underlying ticker from a PacificaEdge symbol."""
    if not symbol:
        return ""
    underlying = str(symbol).split("-")[0].strip().upper()
    return underlying if underlying.isalnum() else ""


def _symbol_aliases(underlying: str) -> tuple[str, ...]:
    """Return a small alias set for common assets."""
    normalized = underlying.lower()
    alias_map = {
        "btc": ("btc", "bitcoin"),
        "eth": ("eth", "ethereum", "ether"),
        "sol": ("sol", "solana"),
        "xrp": ("xrp", "ripple"),
        "ton": ("ton", "toncoin", "the open network"),
        "ada": ("ada", "cardano"),
        "doge": ("doge", "dogecoin"),
    }
    return alias_map.get(normalized, (normalized,))


def _build_query(underlying: str, narrative_summary: str | None) -> str:
    """Build a Google News RSS query for the current asset."""
    aliases = " OR ".join(_symbol_aliases(underlying))
    base_query = f"({aliases}) crypto token perp futures when:1d"
    cleaned_narrative = " ".join((narrative_summary or "").split()).strip()
    if not cleaned_narrative:
        return base_query
    short_narrative = " ".join(cleaned_narrative.split()[:8])
    return f"({aliases}) crypto {short_narrative} when:1d"


def _build_google_news_url(query: str) -> str:
    """Return a Google News RSS search URL."""
    encoded = quote_plus(query)
    return (
        "https://news.google.com/rss/search?"
        f"q={encoded}&hl=en-US&gl=US&ceid=US:en"
    )


async def fetch_current_affairs_context(
    symbol: str,
    narrative_summary: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Fetch lightweight current-affairs context from live web-search headlines."""
    underlying = _underlying_symbol(symbol)
    if not underlying:
        return _fallback_current_affairs("")

    query = _build_query(underlying, narrative_summary)
    url = _build_google_news_url(query)

    try:
        async with httpx.AsyncClient(timeout=2.5, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
        headlines = _parse_google_news_rss(response.text, underlying, limit=limit)
        if not headlines:
            return _fallback_current_affairs(underlying)
        themes = _derive_top_themes(headlines)
        bullish_hits, bearish_hits = _score_bias(headlines, themes)
        return {
            "available": True,
            "symbol": underlying,
            "headlines": headlines,
            "top_themes": themes,
            "bullish_hits": bullish_hits,
            "bearish_hits": bearish_hits,
            "source_status": "google_news_rss",
            "query": query,
        }
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        logger.warning("Current-affairs transport failed for %s: %s", underlying, exc)
        return _fallback_current_affairs(underlying)
    except Exception as exc:
        logger.warning("Current-affairs fetch failed for %s: %s", underlying, exc)
        return _fallback_current_affairs(underlying)


def _parse_google_news_rss(xml_text: str, underlying: str, limit: int = 5) -> list[dict[str, Any]]:
    """Parse a Google News RSS payload into compact headline records."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []

    headlines: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    aliases = _symbol_aliases(underlying)
    items = root.findall("./channel/item")
    for item in items:
        title = _clean_text(item.findtext("title"))
        link = _clean_text(item.findtext("link"))
        pub_date = _clean_text(item.findtext("pubDate"))
        source = _clean_text(item.findtext("source"))
        description = _clean_text(item.findtext("description"))
        if not title or title in seen_titles or not link:
            continue
        combined = f"{title} {description}".lower()
        if not any(alias in combined for alias in aliases):
            continue
        seen_titles.add(title)
        headlines.append(
            {
                "title": title,
                "source": source or None,
                "published_at": pub_date or None,
                "url": link,
                "summary": description or None,
            }
        )
        if len(headlines) >= limit:
            break
    return headlines


def _derive_top_themes(headlines: list[dict[str, Any]]) -> list[str]:
    """Derive a small set of themes from headline text."""
    if not headlines:
        return []

    keyword_buckets = [
        ("etf_flows", ("etf", "inflow", "outflow")),
        ("liquidations", ("liquidation", "squeeze", "wipeout", "leveraged")),
        ("regulation", ("sec", "regulation", "lawsuit", "policy", "legal")),
        ("macro_data", ("fed", "inflation", "cpi", "rates", "macro")),
        ("institutional_flows", ("institutional", "blackrock", "treasury", "fund", "whale")),
        ("network_activity", ("upgrade", "staking", "validator", "partnership", "adoption")),
        ("security_risk", ("hack", "exploit", "breach", "security")),
        ("price_action", ("surge", "drop", "rally", "sell-off", "bounce", "breakout")),
    ]
    combined = " ".join(
        f"{headline.get('title', '')} {headline.get('summary', '')}".lower()
        for headline in headlines
    )
    themes = [label for label, keywords in keyword_buckets if any(keyword in combined for keyword in keywords)]
    return themes[:5] if themes else ["market_context"]


def _score_bias(headlines: list[dict[str, Any]], themes: list[str]) -> tuple[int, int]:
    """Estimate a bullish/bearish bias from current-affairs headlines."""
    combined = " ".join(
        f"{headline.get('title', '')} {headline.get('summary', '')}".lower()
        for headline in headlines
    )
    bullish_markers = (
        "inflow",
        "approval",
        "breakout",
        "adoption",
        "partnership",
        "record high",
        "bounce",
        "rally",
    )
    bearish_markers = (
        "outflow",
        "hack",
        "exploit",
        "lawsuit",
        "sell-off",
        "drop",
        "crash",
        "rejection",
    )
    bullish_hits = sum(combined.count(marker) for marker in bullish_markers)
    bearish_hits = sum(combined.count(marker) for marker in bearish_markers)
    if "institutional_flows" in themes or "etf_flows" in themes:
        bullish_hits += 1
    if "security_risk" in themes or "regulation" in themes:
        bearish_hits += 1
    return bullish_hits, bearish_hits


def _fallback_current_affairs(symbol: str) -> dict[str, Any]:
    """Return the safe fallback current-affairs block."""
    return {
        "available": False,
        "symbol": symbol,
        "headlines": [],
        "top_themes": [],
        "bullish_hits": 0,
        "bearish_hits": 0,
        "source_status": "unavailable",
        "query": None,
    }


def _clean_text(value: Any) -> str:
    """Normalize whitespace from raw RSS strings."""
    return " ".join(str(value or "").replace("\n", " ").split()).strip()
