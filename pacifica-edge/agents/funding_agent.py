"""Funding analysis agent for PacificaEdge."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from services.pacifica import PacificaClient

logger = logging.getLogger(__name__)


class FundingAgent:
    """Analyze funding conditions for a Pacifica market."""

    def __init__(self, pacifica_client: PacificaClient) -> None:
        """Initialize the agent with a Pacifica client."""
        self.pacifica_client = pacifica_client
        self._last_good_by_symbol: dict[str, dict[str, Any]] = {}

    async def analyze(self, symbol: str) -> dict[str, Any]:
        """Analyze funding data for a market symbol."""
        timestamp: str = self._timestamp()

        try:
            funding_rate = 0.0
            next_funding_rate = 0.0
            source = "market_info"

            funding_data = await self.pacifica_client.get_market_info()
            if not funding_data.get("error"):
                normalized_payload = self._find_market_info(funding_data, symbol)
                funding_rate = self._to_float(
                    self._first_value(
                        normalized_payload,
                        ["funding_rate", "current_funding_rate", "rate", "fundingRate"],
                        default=0.0,
                    )
                )
                next_funding_rate = self._to_float(
                    self._first_value(
                        normalized_payload,
                        [
                            "next_funding_rate",
                            "nextFundingRate",
                            "next_rate",
                        ],
                        default=0.0,
                    )
                )

            if abs(funding_rate) < 1e-12:
                price_payload = await self.pacifica_client.get_prices()
                if not price_payload.get("error"):
                    price_row = self._find_market_info(price_payload, symbol)
                    funding_rate = self._to_float(
                        self._first_value(
                            price_row,
                            ["funding", "funding_rate", "current_funding_rate", "rate"],
                            default=0.0,
                        )
                    )
                    if abs(funding_rate) > 1e-12:
                        source = "prices_board"

            if abs(funding_rate) < 1e-12:
                historical_payload = await self.pacifica_client.get_historical_funding(symbol)
                funding_rate = self._extract_historical_funding_rate(historical_payload)
                if abs(funding_rate) > 1e-12:
                    source = "funding_history"

            next_funding_rate: float = self._to_float(
                next_funding_rate
            )
            annualized_rate_pct: float = funding_rate * 3 * 365 * 100
            signal: str = self._determine_signal(funding_rate)
            reason: str = self._build_reason(signal, funding_rate, source)

            logger.info("Funding analysis computed for %s", symbol)
            payload = {
                "agent": "FundingAgent",
                "symbol": symbol,
                "funding_rate": funding_rate,
                "annualized_rate_pct": annualized_rate_pct,
                "next_funding_time": "",
                "next_funding_rate": next_funding_rate,
                "signal": signal,
                "reason": reason,
                "data_source": source,
                "timestamp": timestamp,
            }
            self._last_good_by_symbol[symbol.upper()] = payload
            return payload
        except Exception as exc:
            logger.exception("Funding analysis failed for %s", symbol)
            cached = self._last_good_by_symbol.get(symbol.upper())
            if isinstance(cached, dict):
                cached_payload = dict(cached)
                cached_payload["reason"] = (
                    f"{cached_payload.get('reason', 'Using cached funding context.')} Live funding refresh failed, so this answer is using the last good funding read."
                )
                cached_payload["stale"] = True
                cached_payload["error"] = str(exc)
                cached_payload["timestamp"] = timestamp
                return cached_payload
            fallback = self._neutral_payload(symbol=symbol, timestamp=timestamp)
            fallback["error"] = str(exc)
            return fallback

    async def run(self, symbol: str) -> dict[str, Any]:
        """Run the funding agent with a guaranteed neutral fallback on failure."""
        try:
            return await self.analyze(symbol)
        except Exception as exc:
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {self.__class__.__name__} ERROR: {exc}")
            fallback = self._neutral_payload(symbol=symbol, timestamp=self._timestamp())
            fallback["error"] = str(exc)
            return fallback

    def _neutral_payload(self, symbol: str, timestamp: str) -> dict[str, Any]:
        """Return the neutral funding fallback response."""
        return {
            "agent": "FundingAgent",
            "symbol": symbol,
            "funding_rate": 0.0,
            "annualized_rate_pct": 0.0,
            "next_funding_time": "",
            "next_funding_rate": 0.0,
            "signal": "NEUTRAL",
            "signal_value": 0,
            "reason": "Funding data could not be analyzed",
            "timestamp": timestamp,
        }

    def _find_market_info(self, payload: dict[str, Any], symbol: str) -> dict[str, Any]:
        """Find market info for a specific symbol."""
        base_symbol = self.pacifica_client._to_base_symbol(symbol)
        data = payload.get("data")
        if not isinstance(data, list):
            raise ValueError("Market info payload missing data list")
        for item in data:
            if isinstance(item, dict) and self._to_string(item.get("symbol")).upper() == base_symbol:
                return item
        raise ValueError(f"Funding data not found for symbol '{symbol}'")

    def _determine_signal(self, funding_rate: float) -> str:
        """Determine the directional signal from funding rate."""
        if funding_rate > 0.00002:
            return "BEARISH"
        if funding_rate < -0.00002:
            return "BULLISH"
        return "NEUTRAL"

    def _build_reason(self, signal: str, funding_rate: float, source: str) -> str:
        """Build a human-readable explanation for the funding signal."""
        source_text = {
            "market_info": "live market info",
            "prices_board": "the live prices board",
            "funding_history": "recent funding history",
        }.get(source, "live funding data")
        if signal == "BEARISH":
            return f"Funding is positive on {source_text}, which suggests longs are getting crowded."
        if signal == "BULLISH":
            return f"Funding is negative on {source_text}, which suggests shorts are getting crowded."
        return f"Funding is relatively balanced at {funding_rate:.6f} based on {source_text}."

    def _first_value(self, payload: dict[str, Any], keys: list[str], default: Any | None = None) -> Any:
        """Return the first present, non-null value for the given keys."""
        for key in keys:
            if key in payload and payload[key] is not None:
                return payload[key]
        if default is not None:
            return default
        raise ValueError(f"Missing expected keys: {', '.join(keys)}")

    def _extract_historical_funding_rate(self, payload: dict[str, Any]) -> float:
        """Extract the latest usable funding rate from the historical endpoint."""
        data = payload.get("data")
        rows = data if isinstance(data, list) else payload if isinstance(payload, list) else []
        if not isinstance(rows, list):
            return 0.0
        for item in rows:
            if not isinstance(item, dict):
                continue
            rate = self._to_float(
                self._first_value(
                    item,
                    ["funding_rate", "rate", "fundingRate"],
                    default=0.0,
                )
            )
            if abs(rate) > 1e-12:
                return rate
        return 0.0

    def _to_float(self, value: Any) -> float:
        """Convert a value to float."""
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Unable to convert value '{value}' to float") from exc

    def _to_string(self, value: Any) -> str:
        """Convert a value to string."""
        return str(value)

    def _timestamp(self) -> str:
        """Return the current UTC timestamp in ISO 8601 format."""
        return datetime.now(timezone.utc).isoformat()
