"""Live in-memory accuracy tracking for PacificaEdge signals."""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from services.pacifica import PacificaClient

logger = logging.getLogger(__name__)


@dataclass
class SignalRecord:
    """A recorded live signal and its later outcome."""

    symbol: str
    signal: str
    price_at_signal: float
    timestamp: datetime.datetime
    outcome: Optional[str] = field(default=None)
    price_30min_later: Optional[float] = field(default=None)


class AccuracyTracker:
    """Tracks the live session accuracy of signals using in-memory storage."""

    def __init__(self, pacifica_client: PacificaClient) -> None:
        """Initialize the accuracy tracker with a Pacifica client."""
        self.pacifica_client = pacifica_client
        self._history: List[SignalRecord] = []

    def record_signal(self, symbol: str, signal: str, price: float) -> None:
        """Record a new signal emitted for a symbol at the given price."""
        try:
            self._history.append(
                SignalRecord(
                    symbol=symbol.upper(),
                    signal=signal.upper(),
                    price_at_signal=float(price),
                    timestamp=datetime.datetime.utcnow(),
                )
            )
        except Exception:
            logger.exception("Failed to record signal for %s", symbol)

    async def update_outcomes(self) -> None:
        """Check all pending signals that are at least 30 minutes old and update their outcome."""
        try:
            pending_records = [
                record
                for record in self._history
                if record.outcome is None
                and record.signal in {"BUY", "SELL"}
                and datetime.datetime.utcnow() - record.timestamp >= datetime.timedelta(minutes=30)
            ]
            if not pending_records:
                return

            prices_payload = await self.pacifica_client.get_prices()
            if prices_payload.get("error"):
                return

            for record in pending_records:
                latest_price = self._extract_price(prices_payload, record.symbol)
                if latest_price <= 0:
                    continue
                record.price_30min_later = latest_price
                if record.signal == "BUY":
                    record.outcome = "CORRECT" if latest_price > record.price_at_signal else "INCORRECT"
                elif record.signal == "SELL":
                    record.outcome = "CORRECT" if latest_price < record.price_at_signal else "INCORRECT"
        except Exception:
            logger.exception("Failed to update signal outcomes")

    def get_stats(self) -> Dict[str, Any]:
        """Return current accuracy stats and recent history for this session."""
        try:
            scored_records = [
                record
                for record in self._history
                if record.outcome is not None and record.signal in {"BUY", "SELL"}
            ]
            signals_scored = len(scored_records)
            correct = sum(1 for record in scored_records if record.outcome == "CORRECT")
            accuracy_pct = (correct / signals_scored) * 100.0 if signals_scored > 0 else 0.0

            recent_history = [
                {
                    "symbol": record.symbol,
                    "signal": record.signal,
                    "price_at_signal": record.price_at_signal,
                    "timestamp": record.timestamp.isoformat() + "Z",
                    "outcome": record.outcome,
                    "price_30min_later": record.price_30min_later,
                }
                for record in self._history[-20:]
            ]
            return {
                "signals_scored": signals_scored,
                "correct": correct,
                "accuracy_pct": accuracy_pct,
                "history": recent_history,
            }
        except Exception:
            logger.exception("Failed to build accuracy stats")
            return {
                "signals_scored": 0,
                "correct": 0,
                "accuracy_pct": 0.0,
                "history": [],
            }

    def _extract_price(self, payload: Dict[str, Any], symbol: str) -> float:
        """Extract the latest market price for a symbol from the Pacifica prices payload."""
        market = self._find_market(payload, symbol)
        if market is None:
            return 0.0

        for key in ("mark", "price", "last_price", "mark_price", "index_price", "close"):
            value = market.get(key)
            if value is not None:
                return self._to_float(value)
        return 0.0

    def _find_market(self, payload: Dict[str, Any], symbol: str) -> Dict[str, Any] | None:
        """Find a market record for the given symbol inside a Pacifica payload."""
        base_symbol = symbol.split("-")[0].upper()
        containers: List[Any] = []
        if "data" in payload:
            containers.append(payload["data"])
        containers.append(payload)

        for container in containers:
            if isinstance(container, list):
                for item in container:
                    if isinstance(item, dict) and self._matches_symbol(item, base_symbol):
                        return item

            if isinstance(container, dict):
                direct_market = container.get(symbol)
                if isinstance(direct_market, dict):
                    return direct_market
                for key in ("markets", "items", "results"):
                    nested = container.get(key)
                    if isinstance(nested, list):
                        for item in nested:
                            if isinstance(item, dict) and self._matches_symbol(item, base_symbol):
                                return item
                    if isinstance(nested, dict):
                        market = nested.get(symbol)
                        if isinstance(market, dict):
                            return market
        return None

    def _matches_symbol(self, market: Dict[str, Any], base_symbol: str) -> bool:
        """Check whether a market payload corresponds to the requested base symbol."""
        market_symbol = market.get("symbol") or market.get("market") or market.get("name") or market.get("ticker")
        return isinstance(market_symbol, str) and market_symbol.upper() == base_symbol

    def _to_float(self, value: Any) -> float:
        """Convert a value to float safely."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
