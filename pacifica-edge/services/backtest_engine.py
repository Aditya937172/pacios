"""Backtest engine for recent PacificaEdge signal patterns."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

from agents.signal_agent import SignalAgent
from services.pacifica import PacificaClient

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Backtests recent signal patterns against 30 days of historical Pacifica data."""

    def __init__(self, pacifica_client: PacificaClient, signal_agent: SignalAgent) -> None:
        """Initialize the backtest engine with Pacifica and signal dependencies."""
        self.pacifica_client = pacifica_client
        self.signal_agent = signal_agent
        self.allowed_symbols = {"BTC-USDC", "ETH-USDC", "SOL-USDC"}

    async def backtest_current_pattern(
        self,
        symbol: str,
        current_signal: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Backtest the current signal pattern for the given symbol over the last 30 days."""
        normalized_symbol = symbol.upper()
        if normalized_symbol not in self.allowed_symbols:
            return self._result_with_label("Unsupported symbol for backtest.")

        try:
            klines = await self.pacifica_client.get_klines(normalized_symbol, interval="1h", limit=720)
            closes = self._extract_closes(klines)
            if len(closes) < 48:
                return self._result_with_label("Insufficient historical data for this pattern.")

            signal_payload = current_signal or await self.signal_agent.analyze(normalized_symbol)
            current_final = str(signal_payload.get("final_signal", "HOLD"))
            current_score = self._to_int(signal_payload.get("score"))

            pattern_matches = 0
            correct_predictions = 0
            correct_moves: List[float] = []

            for index in range(3, len(closes) - 4):
                pattern_score = self._pattern_score(closes, index)
                if not self._matches_current_pattern(pattern_score, current_score):
                    continue

                entry_price = closes[index]
                exit_price = closes[index + 4]
                if entry_price <= 0 or exit_price <= 0:
                    continue

                pattern_matches += 1

                if current_final == "BUY":
                    if exit_price > entry_price:
                        correct_predictions += 1
                        correct_moves.append(((exit_price - entry_price) / entry_price) * 100)
                elif current_final == "SELL":
                    if exit_price < entry_price:
                        correct_predictions += 1
                        correct_moves.append(((exit_price - entry_price) / entry_price) * 100)

            if pattern_matches == 0 or current_final == "HOLD":
                return {
                    "pattern_matches": pattern_matches,
                    "correct_predictions": correct_predictions,
                    "accuracy_pct": 0.0,
                    "avg_move_pct": 0.0,
                    "backtest_label": "Recent pattern context is still building for this setup.",
                }

            accuracy_pct = (correct_predictions / pattern_matches) * 100 if pattern_matches > 0 else 0.0
            avg_move_pct = (
                sum(correct_moves) / len(correct_moves)
                if correct_moves
                else 0.0
            )
            return {
                "pattern_matches": pattern_matches,
                "correct_predictions": correct_predictions,
                "accuracy_pct": accuracy_pct,
                "avg_move_pct": avg_move_pct,
                "backtest_label": (
                    f"This pattern has been correct {correct_predictions}/{pattern_matches} times over "
                    f"the last 30 days ({accuracy_pct:.1f}% hit rate, {avg_move_pct:.2f}% avg move)."
                ),
            }
        except Exception:
            logger.exception("Backtest failed for %s", normalized_symbol)
            return self._result_with_label("Pattern sample is still building from recent market history.")

    def _extract_closes(self, payload: Dict[str, Any]) -> List[float]:
        """Extract and sort close prices from the Pacifica kline payload."""
        data = payload.get("data")
        if not isinstance(data, list):
            return []

        candles = [item for item in data if isinstance(item, dict)]
        candles.sort(key=lambda item: self._to_int(item.get("t")))
        closes: List[float] = []
        for candle in candles:
            close_price = self._to_float(candle.get("c"))
            if close_price > 0:
                closes.append(close_price)
        return closes

    def _pattern_score(self, closes: List[float], index: int) -> int:
        """Approximate a simple directional pattern score from nearby price action."""
        score = 0
        if closes[index] > closes[index - 1]:
            score += 1
        elif closes[index] < closes[index - 1]:
            score -= 1

        if closes[index] > closes[index - 3]:
            score += 1
        elif closes[index] < closes[index - 3]:
            score -= 1

        return score

    def _matches_current_pattern(self, pattern_score: int, current_score: int) -> bool:
        """Check whether a historical pattern has the same broad sign as the current score."""
        if current_score >= 2:
            return pattern_score > 0
        if current_score <= -2:
            return pattern_score < 0
        return pattern_score == 0

    def _result_with_label(self, label: str) -> Dict[str, Any]:
        """Build a safe default backtest response."""
        return {
            "pattern_matches": 0,
            "correct_predictions": 0,
            "accuracy_pct": 0.0,
            "avg_move_pct": 0.0,
            "backtest_label": label,
        }

    def _to_float(self, value: Any) -> float:
        """Convert a value to float safely."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _to_int(self, value: Any) -> int:
        """Convert a value to int safely."""
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0
