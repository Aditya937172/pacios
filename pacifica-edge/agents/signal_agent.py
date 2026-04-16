"""Signal aggregation agent for PacificaEdge."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from agents.funding_agent import FundingAgent
from agents.liquidation_agent import LiquidationAgent
from agents.market_agent import MarketAgent
from agents.narrative_agent import NarrativeAgent
from agents.orderbook_agent import OrderBookAgent
from agents.sentiment_agent import SentimentAgent

logger = logging.getLogger(__name__)


class SignalAgent:
    """Combine agent outputs into a single trading signal."""

    def __init__(
        self,
        market_agent: MarketAgent,
        funding_agent: FundingAgent,
        liquidation_agent: LiquidationAgent,
        sentiment_agent: SentimentAgent,
        narrative_agent: NarrativeAgent,
        orderbook_agent: OrderBookAgent,
    ) -> None:
        """Initialize the signal agent with its dependencies."""
        self.market_agent = market_agent
        self.funding_agent = funding_agent
        self.liquidation_agent = liquidation_agent
        self.sentiment_agent = sentiment_agent
        self.narrative_agent = narrative_agent
        self.orderbook_agent = orderbook_agent
        self.signal_scores: dict[str, int] = {
            "BULLISH": 1,
            "BEARISH": -1,
            "NEUTRAL": 0,
        }

    async def analyze(self, symbol: str) -> dict[str, Any]:
        """Aggregate all underlying agent signals into a final decision."""
        timestamp = self._timestamp()

        try:
            results = await asyncio.gather(
                asyncio.wait_for(self.market_agent.run(symbol), timeout=6.0),
                asyncio.wait_for(self.funding_agent.run(symbol), timeout=6.0),
                asyncio.wait_for(self.liquidation_agent.run(symbol), timeout=6.0),
                asyncio.wait_for(self.sentiment_agent.run(symbol), timeout=6.0),
                asyncio.wait_for(self.narrative_agent.run(symbol), timeout=6.0),
                asyncio.wait_for(self.orderbook_agent.run(symbol), timeout=6.0),
                return_exceptions=True,
            )
            neutral_agents = self._neutral_agents(symbol, timestamp)
            agent_keys = ["market", "funding", "liquidation", "sentiment", "narrative", "orderbook"]
            agent_results = {
                agent_key: (
                    neutral_agents[agent_key]
                    if isinstance(result, Exception) or not isinstance(result, dict)
                    else result
                )
                for agent_key, result in zip(agent_keys, results, strict=False)
            }

            score = self._calculate_score(agent_results)
            final_signal = self._final_signal(score)
            confidence_pct = (abs(score) / 6) * 100.0
            reasoning = self._build_reasoning(agent_results)

            return {
                "agent": "SignalAgent",
                "symbol": symbol,
                "final_signal": final_signal,
                "score": score,
                "confidence_pct": confidence_pct,
                "agents": agent_results,
                "reasoning": reasoning,
                "timestamp": timestamp,
            }
        except Exception as exc:
            logger.exception("Signal analysis failed for %s", symbol)
            return {
                "agent": "SignalAgent",
                "symbol": symbol,
                "final_signal": "HOLD",
                "score": 0,
                "confidence_pct": 0.0,
                "agents": self._neutral_agents(symbol, timestamp),
                "reasoning": "Insufficient signal quality across agents.",
                "timestamp": timestamp,
                "error": str(exc),
            }

    async def analyze_all_markets(self) -> dict[str, Any]:
        """Run analyze() across supported markets and derive a macro alert."""
        timestamp = self._timestamp()

        try:
            btc_result, eth_result, sol_result = await asyncio.gather(
                self.analyze("BTC-USDC"),
                self.analyze("ETH-USDC"),
                self.analyze("SOL-USDC"),
            )
            markets = {
                "BTC-USDC": btc_result,
                "ETH-USDC": eth_result,
                "SOL-USDC": sol_result,
            }
            macro_alert = self._macro_alert(markets)
            return {
                "markets": markets,
                "macro_alert": macro_alert,
                "timestamp": timestamp,
            }
        except Exception as exc:
            logger.exception("Macro signal analysis failed")
            return {
                "markets": {
                    "BTC-USDC": self._neutral_signal("BTC-USDC", timestamp),
                    "ETH-USDC": self._neutral_signal("ETH-USDC", timestamp),
                    "SOL-USDC": self._neutral_signal("SOL-USDC", timestamp),
                },
                "macro_alert": None,
                "timestamp": timestamp,
                "error": str(exc),
            }

    def _calculate_score(self, agent_results: dict[str, dict[str, Any]]) -> int:
        """Calculate the combined score across all agents."""
        return sum(
            self.signal_scores.get(result.get("signal", "NEUTRAL"), 0)
            for result in agent_results.values()
        )

    def _final_signal(self, score: int) -> str:
        """Convert a score into the final trading action."""
        if score >= 2:
            return "BUY"
        if score <= -2:
            return "SELL"
        return "HOLD"

    def _build_reasoning(self, agent_results: dict[str, dict[str, Any]]) -> str:
        """Build a human-readable summary of the combined agent view."""
        bullish_count = sum(1 for result in agent_results.values() if result.get("signal") == "BULLISH")
        bearish_count = sum(1 for result in agent_results.values() if result.get("signal") == "BEARISH")
        neutral_count = sum(1 for result in agent_results.values() if result.get("signal") == "NEUTRAL")

        parts: list[str] = [
            f"{bullish_count} of 6 agents are BULLISH, {bearish_count} are BEARISH, and {neutral_count} are NEUTRAL."
        ]

        market_result = agent_results["market"]
        liquidation_result = agent_results["liquidation"]
        sentiment_result = agent_results["sentiment"]
        narrative_result = agent_results["narrative"]
        orderbook_result = agent_results["orderbook"]

        if market_result.get("signal") == "BULLISH":
            parts.append("Price trending up with rising OI.")
        elif market_result.get("signal") == "BEARISH":
            parts.append("Price trend remains weak.")

        if liquidation_result.get("signal") == "BULLISH":
            parts.append("Short liquidations dominating.")
        elif liquidation_result.get("signal") == "BEARISH":
            parts.append("Long liquidations dominating.")

        if sentiment_result.get("signal") == "BULLISH":
            parts.append("Strong social buzz on Elfa AI.")
        elif sentiment_result.get("signal") == "BEARISH":
            parts.append("Social buzz is weak.")

        if narrative_result.get("signal") == "BULLISH":
            parts.append(
                narrative_result.get("narrative_summary", "Narrative momentum is constructive.")
            )
        elif narrative_result.get("signal") == "BEARISH":
            parts.append(
                narrative_result.get("narrative_summary", "Narrative momentum is weakening.")
            )

        if orderbook_result.get("signal") == "BULLISH":
            parts.append("Orderbook microstructure shows buyers dominating.")
        elif orderbook_result.get("signal") == "BEARISH":
            parts.append("Orderbook microstructure shows sellers dominating.")
        elif orderbook_result.get("wall_alert"):
            parts.append(str(orderbook_result.get("wall_alert")))

        bearish_reasons = self._bearish_reasons(agent_results)
        if bearish_reasons:
            parts.append(f"Concern: {' '.join(bearish_reasons)}")

        return " ".join(parts)

    def _bearish_reasons(self, agent_results: dict[str, dict[str, Any]]) -> list[str]:
        """Collect bearish concerns from agent outputs."""
        concerns: list[str] = []

        if agent_results["market"].get("signal") == "BEARISH":
            concerns.append("market trend is bearish.")
        if agent_results["funding"].get("signal") == "BEARISH":
            concerns.append(
                agent_results["funding"].get("reason", "funding rate elevated (longs paying).")
            )
        if agent_results["liquidation"].get("signal") == "BEARISH":
            concerns.append(
                agent_results["liquidation"].get(
                    "reason",
                    "long liquidations are dominating.",
                )
            )
        if agent_results["sentiment"].get("signal") == "BEARISH":
            concerns.append(agent_results["sentiment"].get("reason", "social sentiment is weak."))
        if agent_results["narrative"].get("signal") == "BEARISH":
            concerns.append(agent_results["narrative"].get("reason", "narrative tone is bearish."))
        if agent_results["orderbook"].get("signal") == "BEARISH":
            concerns.append(agent_results["orderbook"].get("reason", "orderbook is ask-heavy."))

        return concerns

    def _macro_alert(self, markets: dict[str, dict[str, Any]]) -> str | None:
        """Determine whether a coordinated macro pattern is present."""
        btc_signal = markets["BTC-USDC"].get("final_signal")
        eth_signal = markets["ETH-USDC"].get("final_signal")
        sol_signal = markets["SOL-USDC"].get("final_signal")

        if btc_signal == "BUY" and eth_signal == "BUY" and sol_signal == "BUY":
            return "MACRO RISK-ON: BTC, ETH, SOL all showing BUY - broad market momentum."
        if btc_signal == "SELL" and eth_signal == "SELL" and sol_signal == "SELL":
            return "MACRO RISK-OFF: BTC, ETH, SOL all showing SELL - coordinated selling."
        if (btc_signal == "BUY" and eth_signal == "SELL" and sol_signal == "SELL") or (
            btc_signal == "SELL" and eth_signal == "BUY" and sol_signal == "BUY"
        ):
            return "DIVERGENCE: BTC decoupling from altcoins - rotation signal."
        return None

    def _neutral_agents(self, symbol: str, timestamp: str) -> dict[str, dict[str, Any]]:
        """Build fallback neutral agent payloads for SignalAgent failures."""
        token = symbol.split("-")[0].upper()
        return {
            "market": {
                "agent": "MarketAgent",
                "symbol": symbol,
                "price": 0.0,
                "change_24h": 0.0,
                "volume_24h": 0.0,
                "open_interest": 0.0,
                "trend": "NEUTRAL",
                "signal": "NEUTRAL",
                "timestamp": timestamp,
            },
            "funding": {
                "agent": "FundingAgent",
                "symbol": symbol,
                "funding_rate": 0.0,
                "annualized_rate_pct": 0.0,
                "next_funding_rate": 0.0,
                "signal": "NEUTRAL",
                "reason": "Funding data unavailable",
                "timestamp": timestamp,
            },
            "liquidation": {
                "agent": "LiquidationAgent",
                "symbol": symbol,
                "long_liquidations_usd": 0.0,
                "short_liquidations_usd": 0.0,
                "total_liquidations_usd": 0.0,
                "dominant_side": "BALANCED",
                "signal": "NEUTRAL",
                "reason": "Liquidation data unavailable",
                "timestamp": timestamp,
            },
            "sentiment": {
                "agent": "SentimentAgent",
                "symbol": symbol,
                "token": token,
                "sentiment_score": 50,
                "mention_count_24h": 0,
                "is_trending": False,
                "rank_in_trending": None,
                "signal": "NEUTRAL",
                "reason": "Insufficient social data",
                "powered_by": "Elfa AI",
                "timestamp": timestamp,
            },
            "narrative": {
                "agent": "NarrativeAgent",
                "symbol": symbol,
                "token": token,
                "bullish_hits": 0,
                "bearish_hits": 0,
                "narrative_summary": "No narrative data available",
                "signal": "NEUTRAL",
                "confidence": "LOW",
                "reason": "Insufficient data from Elfa AI or NeMo 120B.",
                "powered_by": "Elfa AI + NeMo 120B",
                "timestamp": timestamp,
            },
            "orderbook": {
                "agent": "OrderBookAgent",
                "symbol": symbol,
                "bid_total_usd": 0.0,
                "ask_total_usd": 0.0,
                "imbalance_ratio": 0.5,
                "bid_wall": None,
                "ask_wall": None,
                "wall_alert": None,
                "signal": "NEUTRAL",
                "reason": "Orderbook data unavailable",
                "depth_data": [],
                "timestamp": timestamp,
            },
        }

    def _neutral_signal(self, symbol: str, timestamp: str) -> dict[str, Any]:
        """Build a neutral SignalAgent payload for macro fallbacks."""
        return {
            "agent": "SignalAgent",
            "symbol": symbol,
            "final_signal": "HOLD",
            "score": 0,
            "confidence_pct": 0.0,
            "agents": self._neutral_agents(symbol, timestamp),
            "reasoning": "Insufficient signal quality across agents.",
            "timestamp": timestamp,
        }

    def _timestamp(self) -> str:
        """Return the current UTC timestamp in ISO 8601 format."""
        return datetime.now(timezone.utc).isoformat()
