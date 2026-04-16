"""Liquidation analysis agent for PacificaEdge."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from services.pacifica import PacificaClient

logger = logging.getLogger(__name__)


class LiquidationAgent:
    """Analyze recent liquidations for a Pacifica market."""

    def __init__(self, pacifica_client: PacificaClient) -> None:
        """Initialize the agent with a Pacifica client."""
        self.pacifica_client = pacifica_client

    async def analyze(self, symbol: str) -> dict[str, Any]:
        """Analyze liquidation pressure for a market symbol."""
        timestamp: str = self._timestamp()

        try:
            trades_data = await self.pacifica_client.get_trades(symbol)
            if trades_data.get("error"):
                raise ValueError(str(trades_data["error"]))

            trades = self._extract_trades(trades_data)
            if not trades:
                raise ValueError("No trade data available")

            recent_trades = self._filter_recent_trades(trades, minutes=60)
            candidate_trades = recent_trades or trades
            liquidation_trades = self._extract_liquidation_trades(candidate_trades)
            if not liquidation_trades:
                liquidation_trades = self._extract_possible_forced_trades(candidate_trades)
            long_liq_usd, short_liq_usd = self._sum_liquidations(liquidation_trades)
            total_liquidations_usd: float = long_liq_usd + short_liq_usd
            dominant_side: str = self._dominant_side(long_liq_usd, short_liq_usd)
            signal: str = self._signal_from_liquidations(long_liq_usd, short_liq_usd)
            reason: str = self._reason_from_liquidations(signal, dominant_side, total_liquidations_usd)

            logger.info("Liquidation analysis computed for %s", symbol)
            return {
                "agent": "LiquidationAgent",
                "symbol": symbol,
                "long_liquidations_usd": long_liq_usd,
                "short_liquidations_usd": short_liq_usd,
                "total_liquidations_usd": total_liquidations_usd,
                "dominant_side": dominant_side,
                "signal": signal,
                "reason": reason,
                "timestamp": timestamp,
            }
        except Exception as exc:
            logger.exception("Liquidation analysis failed for %s", symbol)
            fallback = self._neutral_payload(symbol=symbol, timestamp=timestamp)
            fallback["error"] = str(exc)
            return fallback

    async def run(self, symbol: str) -> dict[str, Any]:
        """Run the liquidation agent with a guaranteed neutral fallback on failure."""
        try:
            return await self.analyze(symbol)
        except Exception as exc:
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {self.__class__.__name__} ERROR: {exc}")
            fallback = self._neutral_payload(symbol=symbol, timestamp=self._timestamp())
            fallback["error"] = str(exc)
            return fallback

    def _neutral_payload(self, symbol: str, timestamp: str) -> dict[str, Any]:
        """Return the neutral liquidation fallback response."""
        return {
            "agent": "LiquidationAgent",
            "symbol": symbol,
            "long_liquidations_usd": 0.0,
            "short_liquidations_usd": 0.0,
            "total_liquidations_usd": 0.0,
            "dominant_side": "BALANCED",
            "signal": "NEUTRAL",
            "signal_value": 0,
            "reason": "Liquidation data could not be analyzed",
            "timestamp": timestamp,
        }

    def _extract_trades(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize trades payloads into a trade list."""
        candidate_values: list[Any] = [payload]
        if "data" in payload:
            candidate_values.append(payload["data"])
        if "result" in payload:
            candidate_values.append(payload["result"])

        for value in candidate_values:
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                for key in ("trades", "items", "results"):
                    nested = value.get(key)
                    if isinstance(nested, list):
                        return [item for item in nested if isinstance(item, dict)]
        return []

    def _filter_recent_trades(
        self, trades: list[dict[str, Any]], minutes: int
    ) -> list[dict[str, Any]]:
        """Filter trades to the requested recent time window."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        filtered: list[dict[str, Any]] = []

        for trade in trades:
            trade_time = self._extract_trade_time(trade)
            if trade_time is not None and trade_time >= cutoff:
                filtered.append(trade)

        return filtered

    def _extract_liquidation_trades(
        self, trades: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Return liquidation trades identified by Pacifica trade cause."""
        return [
            trade
            for trade in trades
            if self._to_string(trade.get("cause")).lower() == "market_liquidation"
        ]

    def _extract_possible_forced_trades(self, trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return trades that look like forced flows when explicit liquidation tags are sparse."""
        forced_markers = ("liquid", "adl", "bankrupt", "forced")
        extracted: list[dict[str, Any]] = []
        for trade in trades:
            haystack = " ".join(
                [
                    self._to_string(trade.get("cause")),
                    self._to_string(trade.get("type")),
                    self._to_string(trade.get("side")),
                ]
            ).lower()
            if any(marker in haystack for marker in forced_markers):
                extracted.append(trade)
        return extracted

    def _sum_liquidations(self, trades: list[dict[str, Any]]) -> tuple[float, float]:
        """Sum liquidation notional by long and short side."""
        long_liq_usd: float = 0.0
        short_liq_usd: float = 0.0

        for trade in trades:
            value_usd = self._extract_notional_usd(trade)
            side = self._extract_liquidated_side(trade)
            if side == "LONG":
                long_liq_usd += value_usd
            elif side == "SHORT":
                short_liq_usd += value_usd

        return long_liq_usd, short_liq_usd

    def _extract_trade_time(self, trade: dict[str, Any]) -> datetime | None:
        """Extract a UTC trade timestamp if available."""
        raw_time = None
        for key in ("timestamp", "time", "ts", "created_at", "createdAt"):
            if key in trade and trade[key] is not None:
                raw_time = trade[key]
                break

        if raw_time is None:
            return None

        if isinstance(raw_time, (int, float)):
            if raw_time > 1_000_000_000_000:
                return datetime.fromtimestamp(raw_time / 1000, tz=timezone.utc)
            return datetime.fromtimestamp(raw_time, tz=timezone.utc)

        if isinstance(raw_time, str):
            normalized = raw_time.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return None
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

        return None

    def _extract_size(self, trade: dict[str, Any]) -> float:
        """Extract trade size."""
        for key in ("amount", "size", "qty", "quantity", "base_size"):
            if key in trade and trade[key] is not None:
                return self._to_float(trade[key], default=0.0)
        return 0.0

    def _extract_notional_usd(self, trade: dict[str, Any]) -> float:
        """Extract USD notional for a trade."""
        for key in ("usd_value", "notional_usd", "value_usd", "notional", "quote_size"):
            if key in trade and trade[key] is not None:
                return self._to_float(trade[key], default=0.0)

        size = self._extract_size(trade)
        price = 0.0
        for key in ("price", "p", "fill_price", "avg_price"):
            if key in trade and trade[key] is not None:
                price = self._to_float(trade[key], default=0.0)
                break
        return size * price

    def _extract_liquidated_side(self, trade: dict[str, Any]) -> str:
        """Determine whether the liquidation affected longs or shorts."""
        value = trade.get("side")
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "close_long":
                return "LONG"
            if normalized == "close_short":
                return "SHORT"
            if normalized == "open_long":
                return "SHORT"
            if normalized == "open_short":
                return "LONG"
        return "UNKNOWN"

    def _dominant_side(self, long_liq_usd: float, short_liq_usd: float) -> str:
        """Determine the dominant liquidation side."""
        if short_liq_usd > long_liq_usd * 1.5:
            return "SHORTS"
        if long_liq_usd > short_liq_usd * 1.5:
            return "LONGS"
        return "BALANCED"

    def _signal_from_liquidations(self, long_liq_usd: float, short_liq_usd: float) -> str:
        """Determine the liquidation-based signal."""
        if short_liq_usd > long_liq_usd * 1.5:
            return "BULLISH"
        if long_liq_usd > short_liq_usd * 1.5:
            return "BEARISH"
        return "NEUTRAL"

    def _reason_from_liquidations(self, signal: str, dominant_side: str, total_liquidations_usd: float) -> str:
        """Build a human-readable explanation for liquidation pressure."""
        if signal == "BULLISH":
            return (
                f"Forced short exits dominate recent flow, with {total_liquidations_usd:,.0f} USD tagged in liquidation-style trades."
            )
        if signal == "BEARISH":
            return (
                f"Forced long exits dominate recent flow, with {total_liquidations_usd:,.0f} USD tagged in liquidation-style trades."
            )
        if total_liquidations_usd > 0:
            return f"Liquidation-style flow is present but balanced across both sides ({dominant_side})."
        return "No sizable forced-flow prints are visible in the current trade sample."

    def _to_float(self, value: Any, default: float) -> float:
        """Convert a value to float, returning a default on failure."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _to_string(self, value: Any) -> str:
        """Convert a value to string safely."""
        return str(value) if value is not None else ""

    def _timestamp(self) -> str:
        """Return the current UTC timestamp in ISO 8601 format."""
        return datetime.now(timezone.utc).isoformat()
