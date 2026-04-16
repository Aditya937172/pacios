"""Market intelligence agent for PacificaEdge."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.pacifica import PacificaClient


class MarketAgent:
    """Analyze basic market conditions for a Pacifica symbol."""

    def __init__(self, pacifica_client: PacificaClient) -> None:
        """Initialize the agent with a Pacifica client dependency.

        Args:
            pacifica_client: Configured Pacifica API client.
        """
        self.pacifica_client = pacifica_client

    async def analyze(self, symbol: str) -> dict[str, Any]:
        """Analyze a market and produce a simple directional signal.

        Args:
            symbol: Market symbol such as ``BTC-USDC``.

        Returns:
            A normalized market analysis payload. On failure, returns a neutral signal
            with an ``error`` field.
        """
        timestamp: str = self._timestamp()

        try:
            prices = await self.pacifica_client.get_prices()
            if prices.get("error"):
                raise ValueError(str(prices["error"]))

            market = self._find_market(prices, symbol)
            if market is None:
                raise ValueError(f"Market data not found for symbol '{symbol}'")

            price: float = self._to_float(
                self._first_value(
                    market,
                    ["mark", "price", "last_price", "mark_price", "index_price", "close"],
                )
            )
            yesterday_price: float = self._to_float(
                self._first_value(market, ["yesterday_price", "oracle", "mid"])
            )
            change_24h: float = self._calculate_change_24h(price, yesterday_price)
            volume_24h: float = self._to_float(
                self._first_value(market, ["volume_24h", "volume24h", "quote_volume_24h"])
            )
            open_interest: float = self._to_float(
                self._first_value(market, ["open_interest", "openInterest", "oi"])
            )
            trend: str = self._determine_trend(
                current_price=price,
                previous_price=yesterday_price,
                open_interest=open_interest,
            )

            return {
                "agent": "MarketAgent",
                "symbol": symbol,
                "price": price,
                "change_24h": change_24h,
                "volume_24h": volume_24h,
                "open_interest": open_interest,
                "trend": trend,
                "signal": trend,
                "timestamp": timestamp,
            }
        except Exception as exc:
            fallback = self._neutral_payload(symbol=symbol, timestamp=timestamp)
            fallback["error"] = str(exc)
            return fallback

    async def run(self, symbol: str) -> dict[str, Any]:
        """Run the market agent with a guaranteed neutral fallback on failure."""
        try:
            return await self.analyze(symbol)
        except Exception as exc:
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {self.__class__.__name__} ERROR: {exc}")
            fallback = self._neutral_payload(symbol=symbol, timestamp=self._timestamp())
            fallback["error"] = str(exc)
            return fallback

    def _neutral_payload(self, symbol: str, timestamp: str) -> dict[str, Any]:
        """Return the neutral fallback response."""
        return {
            "agent": "MarketAgent",
            "symbol": symbol,
            "price": 0.0,
            "change_24h": 0.0,
            "volume_24h": 0.0,
            "open_interest": 0.0,
            "trend": "NEUTRAL",
            "signal": "NEUTRAL",
            "signal_value": 0,
            "timestamp": timestamp,
        }

    def _find_market(self, summary: dict[str, Any], symbol: str) -> dict[str, Any] | None:
        """Find a market entry for the requested symbol within a summary payload.

        Args:
            summary: Raw summary payload from Pacifica.
            symbol: Market symbol to locate.
        """
        base_symbol = self.pacifica_client._to_base_symbol(symbol)
        containers: list[Any] = []

        if "data" in summary:
            containers.append(summary["data"])
        containers.append(summary)

        for container in containers:
            if isinstance(container, list):
                for item in container:
                    if isinstance(item, dict) and self._matches_symbol(item, symbol):
                        return item

            if isinstance(container, dict):
                direct_market = container.get(symbol)
                if isinstance(direct_market, dict):
                    return direct_market

                for key in ("markets", "items", "results"):
                    value = container.get(key)
                    if isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict) and self._matches_symbol(item, symbol):
                                return item
                    if isinstance(value, dict):
                        nested_market = value.get(symbol)
                        if isinstance(nested_market, dict):
                            return nested_market

        return None

    def _matches_symbol(self, market: dict[str, Any], symbol: str) -> bool:
        """Check whether a market record corresponds to the requested symbol."""
        market_symbol = self._first_value(market, ["symbol", "market", "name", "ticker"])
        base_symbol = self.pacifica_client._to_base_symbol(symbol)
        return isinstance(market_symbol, str) and market_symbol.upper() == base_symbol

    def _calculate_change_24h(self, current_price: float, previous_price: float) -> float:
        """Calculate the 24-hour percentage change."""
        if previous_price == 0:
            return 0.0
        return ((current_price - previous_price) / previous_price) * 100

    def _determine_trend(
        self, current_price: float, previous_price: float, open_interest: float
    ) -> str:
        """Determine trend based on price direction and volume behavior."""
        if current_price > previous_price and open_interest > 0:
            return "BULLISH"
        if current_price < previous_price and open_interest > 0:
            return "BEARISH"
        return "NEUTRAL"

    def _first_value(self, payload: dict[str, Any], keys: list[str]) -> Any:
        """Return the first non-null value found for any key in a payload."""
        for key in keys:
            if key in payload and payload[key] is not None:
                return payload[key]
        raise ValueError(f"Missing expected keys: {', '.join(keys)}")

    def _to_float(self, value: Any) -> float:
        """Convert a value to float with a clear error message."""
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Unable to convert value '{value}' to float") from exc

    def _timestamp(self) -> str:
        """Return the current UTC timestamp in ISO 8601 format."""
        return datetime.now(timezone.utc).isoformat()
