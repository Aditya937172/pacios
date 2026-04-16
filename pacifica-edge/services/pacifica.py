"""Async Pacifica API client."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class PacificaClient:
    """Async client for the Pacifica REST API."""

    def __init__(self, base_url: str | None = None, timeout: float = 5.0) -> None:
        """Initialize the Pacifica client.

        Args:
            base_url: Optional override for the Pacifica API base URL.
            timeout: Request timeout in seconds.
        """
        mainnet_base_url = os.getenv("PACIFICA_BASE_URL", "https://api.pacifica.fi/api/v1")
        testnet_base_url = os.getenv(
            "PACIFICA_TESTNET_URL",
            "https://test-api.pacifica.fi/api/v1",
        )
        use_testnet = os.getenv("USE_TESTNET", "true").strip().lower() == "true"
        resolved_base_url = base_url or (testnet_base_url if use_testnet else mainnet_base_url)
        self.base_url = resolved_base_url.rstrip("/")
        self.timeout = timeout

    async def get_markets(self) -> list[dict[str, Any]]:
        """Return a list of available Pacifica markets with basic info."""
        payload = await self._get("/markets")
        if payload.get("error"):
            logger.warning("Pacifica /markets request failed: %s", payload["error"])
            payload = await self._get("/info")
            if payload.get("error"):
                logger.warning("Pacifica /info fallback failed: %s", payload["error"])
                return []

        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            nested = data.get("data")
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return []

    async def get_prices(self) -> dict[str, Any]:
        """Fetch price information for all symbols."""
        return await self._get("/info/prices")

    async def get_market_info(self) -> dict[str, Any]:
        """Fetch market metadata for all symbols."""
        return await self._get("/info")

    async def get_trades(self, symbol: str) -> dict[str, Any]:
        """Fetch recent trades for a specific market symbol."""
        return await self._get("/trades", params={"symbol": self._to_base_symbol(symbol)})

    async def get_orderbook(self, symbol: str) -> dict[str, Any]:
        """Return the current order book for the given Pacifica market symbol."""
        return await self._get("/book", params={"symbol": self._to_base_symbol(symbol)})

    async def get_klines(
        self, symbol: str, interval: str = "1h", limit: int = 200
    ) -> dict[str, Any]:
        """Return OHLCV klines for the given symbol and interval."""
        normalized_symbol = symbol.upper()
        primary_payload = await self._get(
            f"/markets/{normalized_symbol}/klines",
            params={"interval": interval, "limit": limit},
        )
        if not primary_payload.get("error"):
            return primary_payload

        end_time = int(time.time() * 1000)
        interval_ms = self._interval_to_milliseconds(interval)
        start_time = end_time - max(limit, 1) * interval_ms
        fallback_payload = await self._get(
            "/kline",
            params={
                "symbol": self._to_base_symbol(normalized_symbol),
                "interval": interval,
                "start_time": start_time,
                "end_time": end_time,
            },
        )
        if fallback_payload.get("error"):
            logger.warning(
                "Pacifica klines request failed for %s: %s",
                normalized_symbol,
                fallback_payload["error"],
            )
            return {"error": str(fallback_payload["error"]), "data": []}
        return fallback_payload

    async def get_historical_funding(self, symbol: str) -> dict[str, Any]:
        """Fetch recent historical funding for a specific market symbol."""
        return await self._get(
            "/funding_rate/history",
            params={"symbol": self._to_base_symbol(symbol), "limit": 20},
        )

    def _to_base_symbol(self, symbol: str) -> str:
        """Convert a market pair into Pacifica's base symbol format."""
        return symbol.split("-")[0].upper()

    def _interval_to_milliseconds(self, interval: str) -> int:
        """Convert a textual candle interval into milliseconds."""
        mapping = {
            "1m": 60_000,
            "5m": 300_000,
            "15m": 900_000,
            "30m": 1_800_000,
            "1h": 3_600_000,
            "4h": 14_400_000,
            "1d": 86_400_000,
        }
        return mapping.get(interval, 3_600_000)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute an async GET request and normalize errors."""
        url: str = f"{self.base_url}{path}"
        logger.info("Pacifica API GET %s", url)

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                payload: Any = response.json()
                if isinstance(payload, dict):
                    return payload
                return {"data": payload}
        except httpx.TimeoutException as exc:
            logger.warning("Pacifica API timed out for %s: %s", url, exc)
            return {"error": str(exc)}
        except httpx.HTTPStatusError as exc:
            logger.warning("Pacifica API returned non-success status for %s: %s", url, exc)
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text}"}
        except httpx.RequestError as exc:
            logger.warning("Pacifica API request failed for %s: %s", url, exc)
            return {"error": str(exc)}
        except ValueError as exc:
            logger.warning("Pacifica API returned invalid JSON for %s: %s", url, exc)
            return {"error": f"Invalid JSON response: {exc}"}
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning("Unexpected Pacifica API error for %s: %s", url, exc)
            return {"error": str(exc)}
