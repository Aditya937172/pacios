"""Order book microstructure agent for PacificaEdge."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, Dict, List, Optional

from services.pacifica import PacificaClient

logger = logging.getLogger(__name__)


class OrderBookAgent:
    """Analyze Pacifica order book depth and directional imbalance."""

    def __init__(self, pacifica_client: PacificaClient) -> None:
        """Initialize OrderBookAgent with a Pacifica client."""
        self.pacifica_client = pacifica_client

    async def analyze(self, symbol: str) -> Dict[str, Any]:
        """Analyze the order book microstructure for the given Pacifica symbol."""
        normalized_symbol = symbol.upper()
        token = normalized_symbol.split("-")[0]

        try:
            orderbook = await self.pacifica_client.get_orderbook(normalized_symbol)
            bids = self._normalize_side(self._extract_levels(orderbook, "bids"), limit=20)
            asks = self._normalize_side(self._extract_levels(orderbook, "asks"), limit=20)

            if not bids or not asks:
                return self._neutral_payload(
                    symbol=normalized_symbol,
                    reason="Orderbook data unavailable",
                )

            bid_total_usd = sum(level["price"] * level["size"] for level in bids)
            ask_total_usd = sum(level["price"] * level["size"] for level in asks)
            total = bid_total_usd + ask_total_usd
            imbalance_ratio = bid_total_usd / total if total > 0 else 0.5

            bid_wall = self._find_wall(bids, total)
            ask_wall = self._find_wall(asks, total)
            wall_alert = self._build_wall_alert(
                token=token,
                bid_wall=bid_wall,
                ask_wall=ask_wall,
            )
            signal, reason = self._build_signal_reason(
                imbalance_ratio=imbalance_ratio,
                wall_alert=wall_alert,
            )

            payload = {
                "agent": "OrderBookAgent",
                "symbol": normalized_symbol,
                "bid_total_usd": float(bid_total_usd),
                "ask_total_usd": float(ask_total_usd),
                "imbalance_ratio": float(imbalance_ratio),
                "bid_wall": bid_wall,
                "ask_wall": ask_wall,
                "wall_alert": wall_alert,
                "signal": signal,
                "reason": reason,
                "depth_data": self._build_depth_data(bids, asks),
                "timestamp": self._timestamp(),
            }
            return payload
        except Exception:
            logger.exception("Orderbook analysis failed for %s", normalized_symbol)
            return self._neutral_payload(
                symbol=normalized_symbol,
                reason="Orderbook analysis encountered an unexpected error",
            )

    async def run(self, symbol: str) -> Dict[str, Any]:
        """Run the orderbook agent with a guaranteed neutral fallback on failure."""
        normalized_symbol = symbol.upper()
        try:
            return await self.analyze(symbol)
        except Exception as exc:
            print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] {self.__class__.__name__} ERROR: {exc}")
            fallback = self._neutral_payload(
                symbol=normalized_symbol,
                reason="Orderbook analysis encountered an unexpected error",
            )
            fallback["error"] = str(exc)
            return fallback

    def _extract_levels(self, payload: Dict[str, Any], side: str) -> List[Any]:
        """Extract a raw side of the book from the Pacifica payload."""
        if payload.get("error"):
            return []

        direct_levels = payload.get(side)
        if isinstance(direct_levels, list):
            return direct_levels

        data = payload.get("data")
        if isinstance(data, dict):
            nested_levels = data.get(side)
            if isinstance(nested_levels, list):
                return nested_levels
            levels = data.get("l")
            if isinstance(levels, list) and len(levels) >= 2:
                index = 0 if side == "bids" else 1
                side_levels = levels[index]
                if isinstance(side_levels, list):
                    return side_levels

        return []

    def _normalize_side(self, levels: List[Any], limit: int) -> List[Dict[str, float]]:
        """Normalize raw order book levels into price and size floats."""
        normalized_levels: List[Dict[str, float]] = []

        for level in levels[:limit]:
            normalized_level = self._normalize_level(level)
            if normalized_level is not None:
                normalized_levels.append(normalized_level)

        return normalized_levels

    def _normalize_level(self, level: Any) -> Optional[Dict[str, float]]:
        """Normalize a single order book level entry."""
        if isinstance(level, list) and len(level) >= 2:
            price = self._to_float(level[0])
            size = self._to_float(level[1])
        elif isinstance(level, dict):
            price = self._to_float(level.get("price", level.get("p")))
            size = self._to_float(level.get("size", level.get("a")))
        else:
            return None

        if price <= 0 or size <= 0:
            return None

        return {"price": price, "size": size}

    def _find_wall(
        self,
        levels: List[Dict[str, float]],
        total: float,
    ) -> Optional[Dict[str, float]]:
        """Return the first large wall that exceeds the configured depth threshold."""
        threshold = total * 0.05
        if threshold <= 0:
            return None

        for level in levels:
            size_usd = level["price"] * level["size"]
            if size_usd >= threshold:
                return {
                    "price": float(level["price"]),
                    "size_usd": float(size_usd),
                }

        return None

    def _build_wall_alert(
        self,
        token: str,
        bid_wall: Optional[Dict[str, float]],
        ask_wall: Optional[Dict[str, float]],
    ) -> Optional[str]:
        """Build a user-facing alert if a one-sided whale wall is present."""
        if bid_wall is not None and ask_wall is None:
            return (
                f"{token} has a large bid wall of ${self._format_number(bid_wall['size_usd'])} "
                f"at ${self._format_number(bid_wall['price'])} - potential support."
            )
        if ask_wall is not None and bid_wall is None:
            return (
                f"{token} has a large ask wall of ${self._format_number(ask_wall['size_usd'])} "
                f"at ${self._format_number(ask_wall['price'])} - potential resistance."
            )
        return None

    def _build_signal_reason(self, imbalance_ratio: float, wall_alert: Optional[str]) -> tuple[str, str]:
        """Determine the directional signal and explanation from book imbalance."""
        if imbalance_ratio > 0.53:
            signal = "BULLISH"
            reason = "Orderbook is heavily skewed to bids (buyers dominating)."
        elif imbalance_ratio < 0.47:
            signal = "BEARISH"
            reason = "Orderbook is heavily skewed to asks (sellers dominating)."
        else:
            signal = "NEUTRAL"
            reason = "Orderbook is relatively balanced between bids and asks."

        if wall_alert:
            reason = f"{reason} {wall_alert}"

        return signal, reason

    def _build_depth_data(
        self,
        bids: List[Dict[str, float]],
        asks: List[Dict[str, float]],
    ) -> List[Dict[str, Any]]:
        """Build a compact depth list for front-end consumption."""
        depth_data: List[Dict[str, Any]] = []

        for level in bids[:10]:
            depth_data.append(
                {
                    "price": float(level["price"]),
                    "size": float(level["size"]),
                    "side": "bid",
                }
            )

        for level in asks[:10]:
            depth_data.append(
                {
                    "price": float(level["price"]),
                    "size": float(level["size"]),
                    "side": "ask",
                }
            )

        return depth_data

    def _neutral_payload(self, symbol: str, reason: str) -> Dict[str, Any]:
        """Return a neutral order book analysis payload."""
        return {
            "agent": "OrderBookAgent",
            "symbol": symbol,
            "bid_total_usd": 0.0,
            "ask_total_usd": 0.0,
            "imbalance_ratio": 0.5,
            "bid_wall": None,
            "ask_wall": None,
            "wall_alert": None,
            "signal": "NEUTRAL",
            "signal_value": 0,
            "reason": reason,
            "depth_data": [],
            "timestamp": self._timestamp(),
        }

    def _to_float(self, value: Any) -> float:
        """Convert a value to float safely."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _format_number(self, value: float) -> str:
        """Format a numeric value for user-facing alert text."""
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}"

    def _timestamp(self) -> str:
        """Return the current UTC timestamp in ISO 8601 format."""
        return datetime.utcnow().isoformat() + "Z"
