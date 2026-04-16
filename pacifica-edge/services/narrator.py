"""NeMo-based narration helpers for PacificaEdge."""

from __future__ import annotations

import json
import logging
import os
import asyncio
from typing import Any, Dict

from services.nemo_llm import NeMoClient

logger = logging.getLogger(__name__)


class SignalNarrator:
    """NeMo 120B-based narrator for PacificaEdge signals."""

    def __init__(self, nemo_client: NeMoClient | None) -> None:
        """Initialize the narrator with a NeMo client dependency."""
        self.nemo_client = nemo_client
        self.signal_fallback = (
            "Signal explanation is using the latest desk evidence."
        )
        self.answer_fallback = (
            "Using the latest desk evidence already on screen."
        )

    async def narrate_signal(
        self,
        symbol: str,
        signal_data: Dict[str, Any],
        agent_outputs: Dict[str, Any],
    ) -> str:
        """Generate a 2-sentence natural language explanation of the final signal."""
        final_signal = str(signal_data.get("final_signal", "HOLD"))
        score = self._to_int(signal_data.get("score"))
        confidence_pct = self._to_float(signal_data.get("confidence_pct"))
        fallback = self._signal_fallback_from_context(symbol, signal_data, agent_outputs)

        market_summary = self._market_summary(agent_outputs.get("market", {}))
        funding_summary = self._funding_summary(agent_outputs.get("funding", {}))
        liquidation_summary = self._liquidation_summary(agent_outputs.get("liquidation", {}))
        sentiment_summary = self._sentiment_summary(agent_outputs.get("sentiment", {}))
        narrative_summary = self._narrative_summary(agent_outputs.get("narrative", {}))
        orderbook_summary = self._orderbook_summary(agent_outputs.get("orderbook", {}))
        altfins_summary = self._altfins_summary(signal_data.get("altfins", {}))
        news_summary = self._news_summary(signal_data.get("news_context", {}))
        backtest_summary = self._backtest_summary(signal_data.get("backtest", {}))

        system_prompt = (
            "You are a senior crypto trading analyst for active traders. "
            "Return exactly one JSON object with keys: summary, supporting_agents, disagreement. "
            "supporting_agents must be an array containing agent names from market, funding, liquidation, sentiment, narrative, orderbook. "
            "summary must be one sentence. disagreement must be a short phrase or none. "
            "If altFINS is unavailable or marked no_data, say the explanation is based on PacificaEdge and Elfa data only. "
            "Never mention JSON, instructions, or reasoning steps."
        )
        user_prompt = (
            f"{symbol} final signal {final_signal}, score {score}/6, confidence {confidence_pct:.1f}%.\n"
            f"Market {market_summary}\n"
            f"Funding {funding_summary}\n"
            f"Liquidations {liquidation_summary}\n"
            f"Sentiment {sentiment_summary}\n"
            f"Narrative {narrative_summary}\n"
            f"Orderbook {orderbook_summary}\n"
            f"altFINS {altfins_summary}\n"
            f"News {news_summary}\n"
            f"Backtest {backtest_summary}\n"
            "Use the altFINS bullish_signal_count, bearish_signal_count, alignment_with_signal, and altfins_conviction when judging confirmation or conflict. "
            "When news_context is available, use the top themes and headlines as extra macro or narrative context. "
            "Identify the main driver sentence, the strongest supporting agents, and any disagreement. "
            'Return only {"summary":"...","supporting_agents":["..."],"disagreement":"..."}'
        )

        return await self._call_nemo(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=220,
            fallback=fallback,
        )

    async def answer_market_question(self, question: str, all_markets_state: Dict[str, Any]) -> str:
        """Answer a trader's question about the current PacificaEdge market state."""
        fallback = self._analyst_fallback(question, all_markets_state)
        if fallback is not None:
            return fallback

        system_prompt = self._build_analyst_system_prompt()
        user_prompt = self._build_analyst_user_prompt(question, all_markets_state)

        return await self._answer_market_question_with_rescue(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            question=question,
            all_markets_state=all_markets_state,
        )

    async def _answer_market_question_with_rescue(
        self,
        system_prompt: str,
        user_prompt: str,
        question: str,
        all_markets_state: Dict[str, Any],
    ) -> str:
        """Call NeMo for analyst Q&A and fall back to a context-derived answer before the generic safe message."""
        model_answer = await self._call_nemo(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=300,
            fallback=self.answer_fallback,
        )
        if model_answer != self.answer_fallback and not self._should_rescue_answer(model_answer):
            return model_answer

        rescued_answer = self._analyst_rescue_from_context(question, all_markets_state)
        if rescued_answer:
            return rescued_answer
        return self.answer_fallback

    async def answer_dashboard_agent_question(
        self,
        question: str,
        agent_chat_context: Dict[str, Any],
    ) -> str:
        """Answer a dashboard agent question using team context and the selected agent persona."""
        fallback = self._agent_chat_rescue_from_context(question, agent_chat_context)
        if not self._has_meaningful_agent_chat_context(agent_chat_context):
            return fallback
        use_llm_chat = os.getenv("ENABLE_AGENT_LLM_CHAT", "false").strip().lower() == "true"
        if self.nemo_client is None or not use_llm_chat:
            return fallback

        try:
            model_answer = await asyncio.wait_for(
                self._call_nemo(
                    system_prompt=self._build_dashboard_agent_system_prompt(),
                    user_prompt=self._build_dashboard_agent_user_prompt(question, agent_chat_context),
                    max_tokens=220,
                    fallback=fallback,
                    attempts=2,
                ),
                timeout=float(os.getenv("AGENT_LLM_TIMEOUT_SECONDS", "4.8")),
            )
        except Exception:
            logger.warning("Agent chat LLM timed out or failed; using grounded fallback")
            return fallback
        if model_answer == fallback:
            return fallback
        if not self._is_grounded_agent_answer(model_answer, agent_chat_context):
            return fallback
        return model_answer

    async def _call_nemo(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        fallback: str,
        attempts: int = 2,
    ) -> str:
        """Helper to call NeMo and return a best-effort plain text response."""
        if self.nemo_client is None:
            return fallback

        try:
            for _ in range(max(1, attempts)):
                result = await self.nemo_client.chat_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                )
                extracted = self._extract_text_result(result, fallback)
                if extracted != fallback:
                    return extracted
            return fallback
        except Exception:
            logger.exception("NeMo narration helper failed")
            return fallback

    def _extract_text_result(self, result: Dict[str, Any], fallback: str) -> str:
        """Extract a plain-text answer from a NeMo JSON response."""
        if "error" in result:
            raw = result.get("raw")
            if isinstance(raw, str):
                extracted_raw_text = self._extract_raw_text_value(raw)
                if self._is_usable_text(extracted_raw_text):
                    return extracted_raw_text
                cleaned_raw = self._clean_text(raw)
                if self._is_usable_text(cleaned_raw):
                    return cleaned_raw
            return fallback

        sentence_one = result.get("sentence_one")
        sentence_two = result.get("sentence_two")
        if isinstance(sentence_one, str) and isinstance(sentence_two, str):
            combined = f"{self._clean_text(sentence_one)} {self._clean_text(sentence_two)}".strip()
            if self._is_usable_text(combined):
                return combined

        summary = result.get("summary")
        supporting_agents = result.get("supporting_agents")
        disagreement = result.get("disagreement")
        if isinstance(summary, str) and isinstance(supporting_agents, list):
            cleaned_summary = self._clean_text(summary)
            agent_names = [
                self._agent_label(agent)
                for agent in supporting_agents
                if isinstance(agent, str) and self._agent_label(agent)
            ]
            if self._is_usable_text(cleaned_summary) and agent_names:
                agent_text = self._join_items(agent_names[:4])
                disagreement_text = self._clean_text(str(disagreement)) if disagreement is not None else ""
                if disagreement_text and disagreement_text.lower() != "none":
                    second_sentence = (
                        f"The strongest support comes from {agent_text}, while {disagreement_text}."
                    )
                else:
                    second_sentence = (
                        f"The strongest support comes from {agent_text}, while the remaining agents are mostly neutral."
                    )
                return f"{cleaned_summary} {second_sentence}"

        verdict = result.get("verdict", result.get("call"))
        why = result.get("why", result.get("data"))
        team = result.get("team")
        next_step = result.get("next_step", result.get("action"))
        research = result.get("research", result.get("web"))
        if any(
            isinstance(value, str) and self._clean_text(value)
            for value in (verdict, why, team, next_step, research)
        ):
            return self._format_agent_chat_answer(
                verdict=str(verdict or ""),
                why=str(why or ""),
                team=str(team or ""),
                next_step=str(next_step or ""),
                research=str(research or ""),
            )

        for key in ("text", "message", "answer", "response"):
            value = result.get(key)
            if isinstance(value, str):
                cleaned = self._clean_text(value)
                if self._is_usable_text(cleaned):
                    return cleaned

        raw = result.get("raw")
        if isinstance(raw, str):
            extracted_raw_text = self._extract_raw_text_value(raw)
            if self._is_usable_text(extracted_raw_text):
                return extracted_raw_text
            cleaned_raw = self._clean_text(raw)
            if self._is_usable_text(cleaned_raw):
                return cleaned_raw

        return fallback

    def _market_summary(self, market: Dict[str, Any]) -> str:
        """Build a compact market summary."""
        trend = str(market.get("trend", market.get("signal", "NEUTRAL")))
        price = self._to_float(market.get("price"))
        change_24h = self._to_float(market.get("change_24h"))
        return f"trend {trend}, price {price:.2f}, 24h change {change_24h:.2f}%."

    def _funding_summary(self, funding: Dict[str, Any]) -> str:
        """Build a compact funding summary."""
        signal = str(funding.get("signal", "NEUTRAL"))
        funding_rate = self._to_float(funding.get("funding_rate"))
        annualized_rate = self._to_float(funding.get("annualized_rate_pct"))
        reason = self._clean_text(str(funding.get("reason", "")))
        parts = [
            f"signal {signal}",
            f"funding_rate {funding_rate:.6f}",
        ]
        if annualized_rate != 0.0:
            parts.append(f"annualized {annualized_rate:.2f}%")
        if reason:
            parts.append(reason)
        return ", ".join(parts) + "."

    def _liquidation_summary(self, liquidation: Dict[str, Any]) -> str:
        """Build a compact liquidation summary."""
        dominant_side = str(liquidation.get("dominant_side", "BALANCED"))
        signal = str(liquidation.get("signal", "NEUTRAL"))
        reason = self._clean_text(str(liquidation.get("reason", "")))
        return f"signal {signal}, dominant_side {dominant_side}, {reason or 'no strong liquidation skew'}."

    def _sentiment_summary(self, sentiment: Dict[str, Any]) -> str:
        """Build a compact sentiment summary."""
        signal = str(sentiment.get("signal", "NEUTRAL"))
        score = self._to_float(sentiment.get("sentiment_score"))
        rank = sentiment.get("rank_in_trending")
        rank_text = f", trending rank {rank}" if rank is not None else ""
        return f"signal {signal}, sentiment_score {score:.1f}{rank_text}."

    def _narrative_summary(self, narrative: Dict[str, Any]) -> str:
        """Build a compact narrative summary."""
        signal = str(narrative.get("signal", "NEUTRAL"))
        summary = self._clean_text(str(narrative.get("narrative_summary", "")))
        return f"signal {signal}, summary {summary or 'no narrative summary available'}."

    def _orderbook_summary(self, orderbook: Dict[str, Any]) -> str:
        """Build a compact orderbook summary."""
        signal = str(orderbook.get("signal", "NEUTRAL"))
        imbalance_ratio = self._to_float(orderbook.get("imbalance_ratio"))
        wall_alert = self._clean_text(str(orderbook.get("wall_alert", "")))
        if wall_alert:
            return f"signal {signal}, imbalance_ratio {imbalance_ratio:.2f}, wall {wall_alert}."
        return f"signal {signal}, imbalance_ratio {imbalance_ratio:.2f}, no one-sided wall alert."

    def _altfins_summary(self, altfins: Dict[str, Any]) -> str:
        """Build a compact altFINS summary for narration."""
        if not isinstance(altfins, dict):
            return "altFINS unavailable."

        available = bool(altfins.get("available"))
        derived = bool(altfins.get("derived"))
        trend = altfins.get("trend", {})
        momentum = altfins.get("momentum", {})
        volatility = altfins.get("volatility", {})
        volume = altfins.get("volume", {})
        on_chain = altfins.get("on_chain", {})
        technical_analysis = altfins.get("technical_analysis", {})
        signals = altfins.get("signals", [])
        bullish_signal_count = self._to_int(altfins.get("bullish_signal_count"))
        bearish_signal_count = self._to_int(altfins.get("bearish_signal_count"))
        alignment = self._clean_text(str(altfins.get("alignment_with_signal", "")))
        conviction = self._clean_text(str(altfins.get("altfins_conviction", "")))

        if derived:
            summary_block = altfins.get("summary_block", {})
            altfins_view = self._clean_text(str(summary_block.get("altfins_view", "")))
            source_status = self._clean_text(str(altfins.get("source_status", "")))
            return (
                f"altFINS direct coverage is thin, so the confirmation view is being derived from live PacificaEdge inputs. "
                f"{altfins_view or source_status or 'Use it as a market-structure cross-check rather than direct altFINS confirmation'}."
            )

        if not available or (
            not trend and not momentum and not volatility and not volume and not on_chain and not technical_analysis and not signals
        ):
            return (
                "altFINS does not provide meaningful analytics for this asset in the current response, "
                "so this explanation should rely on PacificaEdge and Elfa data only."
            )

        short_term = self._clean_text(str(trend.get("short_term", "")))
        medium_term = self._clean_text(str(trend.get("medium_term", "")))
        long_term = self._clean_text(str(trend.get("long_term", "")))
        rsi = momentum.get("rsi")
        outlook = self._clean_text(str(technical_analysis.get("near_term_outlook", "")))

        signal_text = "no active altFINS signals"
        if isinstance(signals, list) and signals:
            top_signal = signals[0] if isinstance(signals[0], dict) else {}
            name = self._clean_text(str(top_signal.get("name", "")))
            direction = self._clean_text(str(top_signal.get("direction", "")))
            timeframe = self._clean_text(str(top_signal.get("timeframe", "")))
            joined = " ".join(part for part in [name, direction, timeframe] if part)
            if joined:
                signal_text = joined

        parts = []
        if short_term or medium_term or long_term:
            parts.append(
                f"trend short {short_term or 'n/a'}, medium {medium_term or 'n/a'}, long {long_term or 'n/a'}"
            )
        if rsi is not None:
            parts.append(f"RSI {self._to_float(rsi):.2f}")
        if outlook:
            parts.append(f"outlook {outlook}")
        if bullish_signal_count or bearish_signal_count:
            parts.append(
                f"signals {bullish_signal_count} bullish and {bearish_signal_count} bearish"
            )
        if alignment:
            parts.append(f"alignment {alignment}")
        if conviction:
            parts.append(f"conviction {conviction}")
        parts.append(signal_text)
        return ", ".join(parts) + "."

    def _backtest_summary(self, backtest: Dict[str, Any]) -> str:
        """Build a compact backtest summary for narration."""
        if not isinstance(backtest, dict):
            return "backtest unavailable."
        pattern_matches = self._to_int(backtest.get("pattern_matches"))
        accuracy_pct = self._to_float(backtest.get("accuracy_pct"))
        avg_move_pct = self._to_float(backtest.get("avg_move_pct"))
        label = self._clean_text(str(backtest.get("backtest_label", "")))
        if pattern_matches > 0:
            return (
                f"pattern_matches {pattern_matches}, accuracy {accuracy_pct:.1f}%, avg_move {avg_move_pct:.2f}%."
            )
        if label:
            return f"{label}."
        return "backtest unavailable."

    def _news_summary(self, news_context: Dict[str, Any]) -> str:
        """Build a compact current-affairs summary for narration."""
        if not isinstance(news_context, dict) or not news_context.get("available"):
            return "recent news context unavailable."

        top_themes = news_context.get("top_themes", [])
        headlines = news_context.get("headlines", [])
        themes_text = ", ".join(
            str(theme) for theme in top_themes[:3] if isinstance(theme, str) and theme
        )
        headline_text = ""
        if isinstance(headlines, list) and headlines:
            first_headline = headlines[0] if isinstance(headlines[0], dict) else {}
            title = self._clean_text(str(first_headline.get("title", "")))
            if title:
                headline_text = f"headline {title}"

        parts = []
        if themes_text:
            parts.append(f"themes {themes_text}")
        if headline_text:
            parts.append(headline_text)
        if parts:
            return ", ".join(parts) + "."
        return "recent news context available but sparse."

    def _clean_text(self, text: str) -> str:
        """Normalize model output into a single plain-text line."""
        cleaned = " ".join(text.replace("\n", " ").split())
        return cleaned.strip().strip('"')

    def _line_text(self, text: str) -> str:
        """Normalize a short agent-chat line and ensure it ends cleanly."""
        cleaned = self._clean_text(text)
        if not cleaned:
            return ""
        if cleaned[-1] in ".!?":
            return cleaned
        return f"{cleaned}."

    def _format_agent_chat_answer(
        self,
        verdict: str,
        why: str,
        team: str,
        next_step: str,
        research: str = "",
    ) -> str:
        """Format a concise agent-chat answer as structured point-form guidance."""
        parts: list[str] = []
        seen: set[str] = set()

        def _push(label: str, text: str) -> None:
            cleaned = self._clean_text(text)
            if not cleaned:
                return
            normalized = cleaned.lower()
            if normalized in seen:
                return
            seen.add(normalized)
            parts.append(f"- {label}: {self._line_text(cleaned)}")

        _push("Call", verdict)
        _push("Why", why)
        _push("Team", team)
        _push("Current Affairs", research)
        if self._clean_text(next_step):
            cleaned_next = self._clean_text(next_step)
            if cleaned_next:
                _push("Next", cleaned_next)
        return "\n".join(parts[:5])

    def _strip_named_prefix(self, text: str) -> str:
        """Drop a leading '<Agent Name>:' prefix from a teammate explanation."""
        cleaned = self._clean_text(text)
        if ": " not in cleaned:
            return cleaned
        prefix, remainder = cleaned.split(": ", 1)
        if prefix.endswith("Agent"):
            return remainder.strip()
        return cleaned

    def _compact_team_metric_snapshot(self, team_reports: Dict[str, Any]) -> str:
        """Build a short cross-agent metric snapshot for frontdesk data answers."""
        metric_parts: list[str] = []
        for agent_key in ("market", "funding", "orderbook", "liquidation", "sentiment", "narrative"):
            report_payload = team_reports.get(agent_key, {})
            if not isinstance(report_payload, dict):
                continue
            label = self._clean_text(str(report_payload.get("key_metric_label", "")))
            value = self._clean_text(str(report_payload.get("key_metric_value", "")))
            agent_name = self._clean_text(str(report_payload.get("agent_label", agent_key)))
            if label and value:
                metric_parts.append(f"{agent_name} {label.lower()} {value}")
            if len(metric_parts) >= 3:
                break
        return self._join_items(metric_parts)

    def _extract_raw_text_value(self, raw: str) -> str | None:
        """Best-effort extraction of a text field from malformed JSON-like raw output."""
        if '"text"' not in raw:
            return None

        marker = raw.find('"text"')
        colon = raw.find(":", marker)
        if colon == -1:
            return None

        remainder = raw[colon + 1 :].lstrip()
        if not remainder.startswith('"'):
            return None

        text_chars: list[str] = []
        escape = False
        for char in remainder[1:]:
            if escape:
                text_chars.append(char)
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                break
            text_chars.append(char)

        extracted = self._clean_text("".join(text_chars))
        return extracted or None

    def _looks_like_meta_output(self, text: str) -> bool:
        """Detect prompt-leak or chain-of-thought style output that should be rejected."""
        lowered = text.lower()
        disallowed_phrases = (
            "we need to output",
            "we need to answer",
            "we need to produce",
            "respond with exactly",
            "must explain",
            "task:",
            "schema",
            "reasoning process",
            "the data:",
            "using only the data above",
            "checking git status",
            "answer should reflect",
            "use the example style",
            "we'll say",
            "i'll say",
            "the response should",
            "based on the example",
            "draft the answer",
            "here is the answer:",
            "output should",
            "example input context",
            "example answer",
            "return only",
            "return exactly",
        )
        return any(phrase in lowered for phrase in disallowed_phrases)

    def _is_usable_text(self, text: str | None) -> bool:
        """Check whether extracted model text is substantive enough to return."""
        if not text:
            return False
        cleaned = self._clean_text(text)
        if len(cleaned) < 12:
            return False
        if len(cleaned.split()) < 3:
            return False
        if cleaned.startswith("{") or cleaned.endswith("{") or cleaned.endswith("}"):
            return False
        if '\\"' in cleaned or "\\n" in cleaned or "\\t" in cleaned:
            return False
        if cleaned.count("{") > 0 or cleaned.count("}") > 0:
            return False
        if cleaned.count('"text"') > 1 or cleaned.count("'text'") > 1:
            return False
        return not self._looks_like_meta_output(cleaned)

    def _should_rescue_answer(self, text: str) -> bool:
        """Force the deterministic rescue path when the model answer still looks malformed."""
        cleaned = self._clean_text(text)
        if not cleaned:
            return True
        if not self._is_usable_text(cleaned):
            return True
        suspicious_fragments = (
            '{"text"',
            "Checking git status",
            "Use the example style",
            "answer should reflect",
            "We'll say",
            "I’ll say",
            "I will say",
        )
        return any(fragment.lower() in cleaned.lower() for fragment in suspicious_fragments)

    def _compact_all_markets_state(self, all_markets_state: Dict[str, Any]) -> Dict[str, Any]:
        """Reduce the full three-market payload into a compact LLM context."""
        if "markets" in all_markets_state and isinstance(all_markets_state.get("markets"), dict):
            compact_state: Dict[str, Any] = {
                "markets": {},
                "session_accuracy": all_markets_state.get("session_accuracy", {}),
            }
            for symbol, market_state in all_markets_state["markets"].items():
                if isinstance(market_state, dict):
                    compact_state["markets"][symbol] = self._compact_market_state(market_state)
            return compact_state

        compact_state = {}
        for symbol, market_state in all_markets_state.items():
            if isinstance(market_state, dict):
                compact_state[symbol] = self._compact_market_state(market_state)
        return compact_state

    def _compact_market_state(self, market_state: Dict[str, Any]) -> Dict[str, Any]:
        """Reduce a single signal payload to the fields most useful for Q&A."""
        if "signal_engine" in market_state:
            return self._compact_analysis_context_market_state(market_state)

        agents = market_state.get("agents", {})
        market = agents.get("market", {}) if isinstance(agents, dict) else {}
        funding = agents.get("funding", {}) if isinstance(agents, dict) else {}
        liquidation = agents.get("liquidation", {}) if isinstance(agents, dict) else {}
        sentiment = agents.get("sentiment", {}) if isinstance(agents, dict) else {}
        narrative = agents.get("narrative", {}) if isinstance(agents, dict) else {}
        orderbook = agents.get("orderbook", {}) if isinstance(agents, dict) else {}

        return {
            "final_signal": market_state.get("final_signal", "HOLD"),
            "score": market_state.get("score", 0),
            "confidence_pct": market_state.get("confidence_pct", 0.0),
            "reasoning": market_state.get("reasoning", ""),
            "agents": {
                "market": {
                    "signal": market.get("signal", "NEUTRAL"),
                    "trend": market.get("trend", "NEUTRAL"),
                    "price": market.get("price", 0.0),
                    "change_24h": market.get("change_24h", 0.0),
                },
                "funding": {
                    "signal": funding.get("signal", "NEUTRAL"),
                    "funding_rate": funding.get("funding_rate", 0.0),
                    "reason": funding.get("reason", ""),
                },
                "liquidation": {
                    "signal": liquidation.get("signal", "NEUTRAL"),
                    "dominant_side": liquidation.get("dominant_side", "BALANCED"),
                    "reason": liquidation.get("reason", ""),
                },
                "sentiment": {
                    "signal": sentiment.get("signal", "NEUTRAL"),
                    "sentiment_score": sentiment.get("sentiment_score", 50),
                    "rank_in_trending": sentiment.get("rank_in_trending"),
                },
                "narrative": {
                    "signal": narrative.get("signal", "NEUTRAL"),
                    "summary": narrative.get("narrative_summary", ""),
                },
                "orderbook": {
                    "signal": orderbook.get("signal", "NEUTRAL"),
                    "imbalance_ratio": orderbook.get("imbalance_ratio", 0.5),
                    "wall_alert": orderbook.get("wall_alert"),
                },
            },
        }

    def _compact_analysis_context_market_state(self, market_state: Dict[str, Any]) -> Dict[str, Any]:
        """Reduce a prompt-ready analysis context for Q&A."""
        signal_engine = market_state.get("signal_engine", {})
        altfins = market_state.get("altfins", {})
        backtest = market_state.get("backtest", {})
        technical_analysis = altfins.get("technical_analysis", {}) if isinstance(altfins, dict) else {}

        return {
            "signal_engine": {
                "final_signal": signal_engine.get("final_signal", "HOLD"),
                "score": signal_engine.get("score", 0),
                "confidence_pct": signal_engine.get("confidence_pct", 0.0),
                "macro_alert": signal_engine.get("macro_alert"),
            },
            "market_agent": {
                "trend": market_state.get("market_agent", {}).get("trend", "NEUTRAL"),
                "price": market_state.get("market_agent", {}).get("price", 0.0),
                "change_24h": market_state.get("market_agent", {}).get("change_24h", 0.0),
                "open_interest": market_state.get("market_agent", {}).get("open_interest", 0.0),
            },
            "funding_agent": {
                "signal": market_state.get("funding_agent", {}).get("signal", "NEUTRAL"),
                "funding_rate": market_state.get("funding_agent", {}).get("funding_rate", 0.0),
                "annualized_rate_pct": market_state.get("funding_agent", {}).get("annualized_rate_pct", 0.0),
            },
            "liquidation_agent": {
                "signal": market_state.get("liquidation_agent", {}).get("signal", "NEUTRAL"),
                "dominant_side": market_state.get("liquidation_agent", {}).get("dominant_side", "BALANCED"),
                "long_liquidations_usd": market_state.get("liquidation_agent", {}).get("long_liquidations_usd", 0.0),
                "short_liquidations_usd": market_state.get("liquidation_agent", {}).get("short_liquidations_usd", 0.0),
            },
            "sentiment_agent": {
                "signal": market_state.get("sentiment_agent", {}).get("signal", "NEUTRAL"),
                "sentiment_score": market_state.get("sentiment_agent", {}).get("sentiment_score", 50),
            },
            "narrative_agent": {
                "signal": market_state.get("narrative_agent", {}).get("signal", "NEUTRAL"),
                "narrative_summary": market_state.get("narrative_agent", {}).get("narrative_summary", ""),
                "bullish_hits": market_state.get("narrative_agent", {}).get("bullish_hits", 0),
                "bearish_hits": market_state.get("narrative_agent", {}).get("bearish_hits", 0),
            },
            "orderbook_agent": {
                "signal": market_state.get("orderbook_agent", {}).get("signal", "NEUTRAL"),
                "imbalance_ratio": market_state.get("orderbook_agent", {}).get("imbalance_ratio", 0.5),
                "wall_alert": market_state.get("orderbook_agent", {}).get("wall_alert"),
            },
            "backtest": {
                "pattern_matches": backtest.get("pattern_matches", 0),
                "accuracy_pct": backtest.get("accuracy_pct", 0.0),
                "avg_move_pct": backtest.get("avg_move_pct", 0.0),
            },
            "altfins": {
                "available": altfins.get("available", False),
                "trend": altfins.get("trend", {}),
                "momentum": altfins.get("momentum", {}),
                "volatility": altfins.get("volatility", {}),
                "volume": altfins.get("volume", {}),
                "on_chain": altfins.get("on_chain", {}),
                "signals": altfins.get("signals", []),
                "bullish_signal_count": altfins.get("bullish_signal_count", 0),
                "bearish_signal_count": altfins.get("bearish_signal_count", 0),
                "alignment_with_signal": altfins.get("alignment_with_signal", "no_data"),
                "altfins_conviction": altfins.get("altfins_conviction", "unknown"),
                "summary_block": altfins.get("summary_block", {}),
                "technical_analysis": {
                    "near_term_outlook": technical_analysis.get("near_term_outlook"),
                    "pattern_type": technical_analysis.get("pattern_type"),
                },
            },
            "news_context": {
                "available": market_state.get("news_context", {}).get("available", False),
                "top_themes": market_state.get("news_context", {}).get("top_themes", []),
                "headlines": [
                    {
                        "title": headline.get("title"),
                        "source": headline.get("source"),
                    }
                    for headline in market_state.get("news_context", {}).get("headlines", [])[:3]
                    if isinstance(headline, dict)
                ],
            },
        }

    def _build_analyst_system_prompt(self) -> str:
        """Build the system prompt for NeMo market-question answering."""
        return (
            "You are the PacificaEdge Analyst, a collaborative crypto desk analyst. "
            "You answer in a friendly but professional way, using plain English first and evidence second. "
            "You do not act like a raw data reporter. You explain what matters, why it matters, and how the specialist agents line up. "
            "You answer user questions about one or more crypto symbols using structured PacificaEdge context only. "
            "The context contains these blocks: "
            "market_agent for price action, trend, and key levels; "
            "funding_agent for funding rates and perp skew; "
            "liquidation_agent for long/short liquidations and squeeze pressure; "
            "sentiment_agent for social sentiment and positioning tone; "
            "narrative_agent for the current story around the asset; "
            "orderbook_agent for depth, walls, and imbalance; "
            "signal_engine for the current BUY, SELL, or HOLD meta signal; "
            "backtest for historical pattern quality; "
            "session_accuracy for live session performance; "
            "altfins for higher-timeframe external trend, momentum, signals, and conviction; "
            "news_context for current headlines and themes. "
            "Use all available context fields to answer. Reference specific fields when useful, such as final_signal, confidence_pct, funding_rate, long_liquidations_usd, short_liquidations_usd, imbalance_ratio, bullish_signal_count, bearish_signal_count, alignment_with_signal, altfins_conviction, accuracy_pct, and top_themes. "
            "When the user asks what to explore next, compare markets and recommend the strongest candidates with reasons. "
            "When the user asks about one agent, still use the full-team context and mention whether the other agents confirm or disagree. "
            "Do not give financial or legal advice. Frame the answer as informational market analysis only. "
            "Do not hallucinate missing symbols or values. If coverage is thin, say so explicitly rather than inventing context. "
            "If altfins.available is false and news_context.available is false and the signal context is weak, a cautious limited-data answer is acceptable. "
            'Return exactly one JSON object with one key: {"text":"..."} and keep the text to at most 5 sentences with no markdown.\n'
            "Example input context:\n"
            '{"markets":{"BTC-USDC":{"signal_engine":{"final_signal":"BUY","confidence_pct":50.0},"altfins":{"available":true,"summary_block":{"altfins_view":"Conflicted / Low conviction"},"bullish_signal_count":6,"bearish_signal_count":4},"news_context":{"available":true,"top_themes":["ETF inflows"]}}}}\n'
            'Example answer:\n{"text":"BTC-USDC is currently a BUY on PacificaEdge, but it is not a clean high-conviction trend because altFINS is conflicted and low conviction. The supporting macro context is still constructive because recent news themes include ETF inflows, so the setup looks positive but not one-sided. This is informational analysis, not trading advice."}\n'
            "Example input context:\n"
            '{"markets":{"NOTREAL-USDC":{"signal_engine":{"final_signal":"HOLD","confidence_pct":0.0},"altfins":{"available":false},"news_context":{"available":false}}}}\n'
            'Example answer:\n{"text":"NOTREAL-USDC currently looks like a neutral HOLD with limited coverage. PacificaEdge does not have strong supporting confirmation here, and both altFINS and news coverage are unavailable in the current context. This is informational analysis only."}'
        )

    def _build_analyst_user_prompt(self, question: str, all_markets_state: Dict[str, Any]) -> str:
        """Build the user prompt for NeMo market-question answering."""
        serialized_context = json.dumps(all_markets_state, separators=(",", ":"), ensure_ascii=True)
        return (
            "Current PacificaEdge analysis context:\n\n"
            f"{serialized_context}\n\n"
            f"Trader question: {question}\n\n"
            "Answer the trader directly using the context above. "
            "Use the six agents, signal_engine, backtest, session_accuracy, altfins, and news_context when they are available. "
            "Make the answer feel like a desk analyst speaking to a human, not a raw report dump. "
            "Mention uncertainty only where the context is actually thin. "
            'Return exactly {"text":"<plain text answer>"}'
        )

    def _build_dashboard_agent_system_prompt(self) -> str:
        """Build the system prompt for the dashboard agent-chat experience."""
        return (
            "You are PacificaEdge Agent Chat. "
            "You speak as the selected desk member, but you are allowed to reference the full desk. "
            "You must sound like a fast collaborative analyst, not a reporting template. "
            "Answer in plain trader language that a non-technical user can scan quickly. "
            "When the selected agent is frontdesk, speak like the head of the desk combining six specialists into one call. "
            "When the user is in all-markets mode, rank the best 2 or 3 markets to explore, explain why using the market board and any attached news blocks, and tell the user to drill into the symbol for the full six-agent read. "
            "When the user asks one specialist agent a question, keep that agent's voice but still use the whole-team context. "
            "Use current affairs when available, but do not force them if coverage is thin. "
            "Do not hallucinate metrics or markets that are not present in the context. "
            "Keep the answer concise, friendly, professional, and easy to read. "
            "Each field must be short, concrete, and grounded in the supplied numbers and teammate views. "
            "Tailor the answer to the user's intent: "
            "if they ask for data, focus on metrics; "
            "if they ask for team view, focus on which agents support or disagree; "
            "if they ask for web or research, focus on live current-affairs coverage; "
            "if they ask what to do next, give the practical next move instead of repeating the market ranking. "
            'Return exactly one JSON object with one key: {"text":"..."} '
            "Format the text as 4 or 5 short point-form lines using this shape when possible: "
            "Call, Why, Team, optional Current Affairs, Next. "
            "Keep every line short and understandable by a normal user."
        )

    def _build_dashboard_agent_user_prompt(self, question: str, agent_chat_context: Dict[str, Any]) -> str:
        """Build the user prompt for the dashboard agent-chat experience."""
        serialized_context = json.dumps(agent_chat_context, separators=(",", ":"), ensure_ascii=True)
        return (
            "Current dashboard agent-chat context:\n\n"
            f"{serialized_context}\n\n"
            f"User question: {question}\n\n"
            "Answer as the selected agent using the chosen workspace, the team summaries, the market signal, and the news context. "
            "If teammates agree, say that clearly. If a teammate is the main disagreement, say that clearly too. "
            "If the user asks what to explore next, rank markets instead of giving a generic answer. "
            "Keep this tight enough to read in one glance, but make it sound like a human analyst talking. "
            "Use short point-form lines rather than one dense paragraph. "
            'Return exactly {"text":"<plain text answer>"}'
        )

    def _analyst_rescue_from_context(self, question: str, all_markets_state: Dict[str, Any]) -> str | None:
        """Build a deterministic analyst answer from context when the model response is unusable."""
        focus_symbol = self._focus_symbol_from_question(question, all_markets_state)
        if not focus_symbol:
            return None

        markets = all_markets_state.get("markets", {})
        market_state = markets.get(focus_symbol, {}) if isinstance(markets, dict) else {}
        if not isinstance(market_state, dict):
            return None

        signal_engine = market_state.get("signal_engine", {})
        final_signal = str(signal_engine.get("final_signal", "HOLD"))
        confidence_pct = self._to_float(signal_engine.get("confidence_pct"))
        market_agent = market_state.get("market_agent", {})
        funding_agent = market_state.get("funding_agent", {})
        liquidation_agent = market_state.get("liquidation_agent", {})
        sentiment_agent = market_state.get("sentiment_agent", {})
        narrative_agent = market_state.get("narrative_agent", {})
        orderbook_agent = market_state.get("orderbook_agent", {})
        altfins = market_state.get("altfins", {})
        news_context = market_state.get("news_context", {})
        backtest = market_state.get("backtest", {})
        session_accuracy = all_markets_state.get("session_accuracy", {})

        stance_map = {"BUY": "cautiously bullish", "SELL": "cautiously bearish", "HOLD": "neutral"}
        first_sentence = (
            f"{focus_symbol} currently reads as {final_signal} with {confidence_pct:.0f}% PacificaEdge confidence, "
            f"so the near-term stance is {stance_map.get(final_signal, 'neutral')}."
        )

        driver_parts: list[str] = []
        if market_agent.get("signal") == "BULLISH":
            driver_parts.append(
                f"market trend is bullish with price at {self._to_float(market_agent.get('price')):.2f}"
            )
        elif market_agent.get("signal") == "BEARISH":
            driver_parts.append("market trend is weak")
        if funding_agent.get("signal") != "NEUTRAL":
            driver_parts.append(
                f"funding is {str(funding_agent.get('signal', 'NEUTRAL')).lower()} at {self._to_float(funding_agent.get('funding_rate')):.6f}"
            )
        if sentiment_agent.get("signal") == "BULLISH":
            driver_parts.append(
                f"smart-account sentiment is strong at {self._to_float(sentiment_agent.get('sentiment_score')):.0f}"
            )
        elif sentiment_agent.get("signal") == "BEARISH":
            driver_parts.append(
                f"smart-account sentiment is weak at {self._to_float(sentiment_agent.get('sentiment_score')):.0f}"
            )
        if orderbook_agent.get("signal") != "NEUTRAL":
            driver_parts.append(
                f"order book imbalance is {self._to_float(orderbook_agent.get('imbalance_ratio')):.2f}"
            )
        if narrative_agent.get("signal") != "NEUTRAL":
            narrative_summary = self._clean_text(str(narrative_agent.get("narrative_summary", "")))
            if narrative_summary:
                driver_parts.append(narrative_summary)
        if liquidation_agent.get("signal") != "NEUTRAL":
            driver_parts.append(
                f"liquidations favor {str(liquidation_agent.get('dominant_side', 'BALANCED')).lower()}"
            )

        second_sentence = (
            f"The main PacificaEdge drivers are {self._join_items(driver_parts[:4])}."
            if driver_parts
            else "PacificaEdge is not seeing a strong directional catalyst across its six agents."
        )

        altfins_available = bool(altfins.get("available"))
        news_available = bool(news_context.get("available"))
        third_parts: list[str] = []
        if altfins_available:
            summary_block = altfins.get("summary_block", {})
            altfins_view = self._clean_text(str(summary_block.get("altfins_view", "")))
            htf_trend = self._clean_text(str(summary_block.get("htf_trend", "")))
            if altfins_view:
                third_parts.append(f"altFINS is {altfins_view.lower()}")
            if htf_trend:
                third_parts.append(f"with higher-timeframe trend {htf_trend}")
        else:
            third_parts.append("altFINS coverage is unavailable")
        if news_available:
            themes = [
                str(theme)
                for theme in news_context.get("top_themes", [])[:3]
                if isinstance(theme, str) and theme
            ]
            if themes:
                third_parts.append(f"news themes include {self._join_items(themes)}")
        else:
            third_parts.append("news coverage is thin")
        third_sentence = f"{focus_symbol} also has {' and '.join(third_parts)}."

        pattern_matches = self._to_int(backtest.get("pattern_matches"))
        accuracy_pct = self._to_float(backtest.get("accuracy_pct"))
        signals_scored = self._to_int(session_accuracy.get("signals_scored"))
        session_accuracy_pct = self._to_float(session_accuracy.get("accuracy_pct"))
        if pattern_matches > 0:
            fourth_sentence = (
                f"The current pattern has a {accuracy_pct:.1f}% 30-day hit rate across {pattern_matches} matches"
            )
        else:
            fourth_sentence = "The current pattern does not have strong backtest depth"
        if signals_scored > 0:
            fourth_sentence += (
                f", and live session accuracy is {session_accuracy_pct:.1f}% over {signals_scored} scored signals"
            )
        fourth_sentence += "; this is informational analysis, not trading advice."

        return " ".join([first_sentence, second_sentence, third_sentence, fourth_sentence])

    def _agent_chat_rescue_from_context(self, question: str, agent_chat_context: Dict[str, Any]) -> str:
        """Build a deterministic desk-style agent answer when the LLM path is unavailable."""
        mode = str(agent_chat_context.get("mode", "single_market"))
        question_lower = question.lower()
        question_is_research = any(term in question_lower for term in ("news", "research", "tavily", "verify", "current affairs", "headline", "web", "theme", "story", "narrative"))
        question_is_reason = any(term in question_lower for term in ("why", "proof", "reason", "explain", "summary", "summarize"))
        question_is_consensus = any(term in question_lower for term in ("agree", "disagree", "team", "multi", "agents", "other agents", "consensus", "confirm", "confirmation"))
        question_is_risk = any(term in question_lower for term in ("risk", "watch", "danger", "invalidate", "invalid", "downside", "fail", "wrong"))
        question_is_data = any(term in question_lower for term in ("data", "metric", "metrics", "number", "numbers", "seeing", "signal value", "price", "open interest", "oi", "volume", "funding", "liquidation", "imbalance"))
        question_is_action = any(term in question_lower for term in ("best move", "what should", "from here", "next move", "action", "do now", "move for me", "step now", "buy now", "sell now", "long", "short", "enter", "exit", "trade"))
        question_is_summary = any(term in question_lower for term in ("summary", "summarize", "simple", "plain english", "quick read"))
        question_is_levels = any(term in question_lower for term in ("level", "levels", "wall", "support", "resistance", "book"))

        if mode == "all_markets":
            board = agent_chat_context.get("all_markets_board", [])
            if not isinstance(board, list) or not board:
                return self._format_agent_chat_answer(
                    verdict="All-markets scan is still forming.",
                    why="The broader board has not populated enough live rows yet.",
                    team="Frontdesk needs the active market board before ranking fresh opportunities.",
                    research="Current-affairs coverage is still thin across the board.",
                    next_step="Check the board again after more live rows arrive.",
                )

            top_rows = board[:3]
            best = top_rows[0] if isinstance(top_rows[0], dict) else {}
            best_symbol = str(best.get("symbol", "the top board name"))
            best_signal = str(best.get("quick_signal", "HOLD"))
            best_oi = best.get("open_interest", 0.0)
            best_volume = best.get("volume_24h", 0.0)
            best_funding = best.get("funding_apy", 0.0)
            top_names = ", ".join(
                str(row.get("symbol"))
                for row in top_rows
                if isinstance(row, dict) and row.get("symbol")
            )
            current_affairs = agent_chat_context.get("current_affairs", [])
            headline_text = ""
            if isinstance(current_affairs, list) and current_affairs:
                first_headline = current_affairs[0] if isinstance(current_affairs[0], dict) else {}
                title = self._clean_text(str(first_headline.get("title", "")))
                if title:
                    headline_text = title
            summary_rows = [
                f"{str(row.get('symbol'))}: {str(row.get('quick_signal', 'HOLD'))}, OI {self._format_compact_currency(row.get('open_interest'))}, volume {self._format_compact_currency(row.get('volume_24h'))}, funding {self._format_percent(row.get('funding_apy'), 2)}"
                for row in top_rows
                if isinstance(row, dict)
            ]
            board_data_line = (
                f"{best_symbol} has the cleanest board mix right now: {best_signal}, "
                f"open interest near {self._format_compact_currency(best_oi)}, "
                f"volume near {self._format_compact_currency(best_volume)}, "
                f"and funding around {self._format_percent(best_funding, 2)} annualized"
            )
            team_line = (
                f"The next board names to compare are {top_names}."
                if top_names
                else "Frontdesk is waiting for more board depth."
            )
            research_line = (
                f"Live current-affairs coverage is flagging {headline_text}"
                if headline_text
                else "Board-wide current-affairs coverage is light right now"
            )
            next_line = f"Open {best_symbol} first, then compare it with {str(top_rows[1].get('symbol')) if len(top_rows) > 1 and isinstance(top_rows[1], dict) else best_symbol} on the full six-agent desk."
            if any(term in question_lower for term in ("active", "broader markets", "most active", "what's moving", "activity")):
                return self._format_agent_chat_answer(
                    verdict=f"The most active board names are {top_names or best_symbol}.",
                    why=" | ".join(summary_rows[:3]),
                    team="This board is a fast scan only; it is not the full multi-agent verdict yet.",
                    research=research_line,
                    next_step=next_line,
                )
            if question_is_action:
                return self._format_agent_chat_answer(
                    verdict=f"Do not act on the board scan alone; start with {best_symbol} as the first market to inspect.",
                    why=board_data_line,
                    team="The board is for discovery. You still need the full six-agent read before making a move.",
                    research=research_line,
                    next_step=next_line,
                )
            if question_is_data:
                return self._format_agent_chat_answer(
                    verdict=f"{best_symbol} is the strongest board candidate right now.",
                    why=" | ".join(summary_rows[:3]),
                    team=team_line,
                    research=research_line,
                    next_step=next_line,
                )
            if question_is_research:
                return self._format_agent_chat_answer(
                    verdict=f"Board-wide research is light, with {best_symbol} still leading the scan.",
                    why=board_data_line,
                    team="Market ranking is still coming from live board activity, not a single headline.",
                    research=research_line,
                    next_step=next_line,
                )
            if any(term in question_lower for term in ("recommend", "explore", "new market", "which market")):
                return self._format_agent_chat_answer(
                    verdict=f"Explore {best_symbol} first.",
                    why=board_data_line,
                    team=team_line,
                    research=research_line,
                    next_step=next_line,
                )
            return self._format_agent_chat_answer(
                verdict=f"The board is most active in {top_names or best_symbol}.",
                why=board_data_line,
                team=team_line,
                research=research_line,
                next_step=next_line,
            )

        workspace = agent_chat_context.get("workspace", {})
        team_reports = agent_chat_context.get("team_reports", {})
        market_signal = agent_chat_context.get("market_signal", {})
        agent_label = str(workspace.get("agent_label", "Desk Agent"))
        agent_key = str(workspace.get("agent", "frontdesk"))
        symbol = str(workspace.get("symbol", "this market"))
        verdict = str(workspace.get("verdict", "NEUTRAL"))
        overall_verdict = str(workspace.get("overall_verdict", market_signal.get("final_signal", "HOLD")))
        overall_confidence = self._to_float(
            workspace.get("overall_confidence_pct", market_signal.get("confidence_pct", 0.0))
        )
        report = self._clean_text(str(workspace.get("report", "")))
        metric_label = self._clean_text(str(workspace.get("key_metric_label", "key metric")))
        metric_value = self._clean_text(str(workspace.get("key_metric_value", "n/a")))
        reasoning_details = workspace.get("reasoning_details", {})
        if not isinstance(reasoning_details, dict):
            reasoning_details = {}
        local_thesis = self._clean_text(str(reasoning_details.get("thesis", "")))
        local_risk = self._clean_text(str(reasoning_details.get("risk", "")))
        local_evidence = [
            self._clean_text(str(item))
            for item in reasoning_details.get("evidence", [])
            if self._clean_text(str(item))
        ] if isinstance(reasoning_details.get("evidence", []), list) else []
        next_steps = workspace.get("next_steps", [])
        next_step = next_steps[0] if isinstance(next_steps, list) and next_steps else "watch the next refresh for confirmation"
        top_themes = workspace.get("top_themes", [])
        theme_text = self._join_items([str(theme) for theme in top_themes[:3] if isinstance(theme, str) and theme])

        support_lines: list[str] = []
        pushback_lines: list[str] = []
        neutral_lines: list[str] = []
        support_names: list[str] = []
        pushback_names: list[str] = []
        neutral_names: list[str] = []
        support_reasons: list[str] = []
        pushback_reasons: list[str] = []
        neutral_reasons: list[str] = []
        for team_agent_key, report_payload in team_reports.items():
            if not isinstance(report_payload, dict):
                continue
            if team_agent_key == workspace.get("agent"):
                continue
            agent_name = str(report_payload.get("agent_label", team_agent_key))
            report_text = self._clean_text(str(report_payload.get("report", "")))
            line = (
                f"{agent_name} is {report_payload.get('verdict', 'NEUTRAL')} "
                f"with {report_payload.get('key_metric_label', 'metric')} at {report_payload.get('key_metric_value', 'n/a')}"
            )
            verdict_value = str(report_payload.get("verdict", "NEUTRAL")).upper()
            relation = self._classify_team_alignment(selected_verdict=verdict, teammate_verdict=verdict_value)
            if relation == "support":
                support_lines.append(line)
                support_names.append(agent_name)
                if report_text:
                    support_reasons.append(f"{agent_name}: {report_text}")
            elif relation == "pushback":
                pushback_lines.append(line)
                pushback_names.append(agent_name)
                if report_text:
                    pushback_reasons.append(f"{agent_name}: {report_text}")
            else:
                neutral_lines.append(line)
                neutral_names.append(agent_name)
                if report_text:
                    neutral_reasons.append(f"{agent_name}: {report_text}")

        current_affairs = workspace.get("current_affairs", [])
        verified_headlines = [
            self._clean_text(str(item.get("title", "")))
            for item in current_affairs[:2]
            if isinstance(item, dict) and item.get("title")
        ] if isinstance(current_affairs, list) else []
        research_line = ""
        if verified_headlines:
            research_line = f"Live current-affairs coverage is highlighting {self._join_items(verified_headlines)}"
        elif theme_text:
            research_line = f"Current market themes are {theme_text}"
        include_research = question_is_research or question_is_reason

        support_name_text = self._join_items(support_names[:3]) if support_names else "the broader team"
        pushback_name_text = self._join_items(pushback_names[:2]) if pushback_names else ""
        neutral_name_text = self._join_items(neutral_names[:3]) if neutral_names else ""
        support_reason_text = self._join_items(support_reasons[:2]) if support_reasons else ""
        pushback_reason_text = self._join_items(pushback_reasons[:2]) if pushback_reasons else ""
        neutral_reason_text = self._join_items(neutral_reasons[:2]) if neutral_reasons else ""
        lead_support_name = support_names[0] if support_names else ""
        lead_pushback_name = pushback_names[0] if pushback_names else ""
        lead_support_reason = self._strip_named_prefix(support_reasons[0]) if support_reasons else ""
        lead_pushback_reason = self._strip_named_prefix(pushback_reasons[0]) if pushback_reasons else ""
        team_metric_snapshot = self._compact_team_metric_snapshot(team_reports)
        if agent_key == "frontdesk":
            verdict_line = f"{symbol} desk bias is {overall_verdict} with {overall_confidence:.0f}% confidence"
            data_line = report or "The desk evidence is mixed."
            team_line = (
                f"Lead support comes from {support_name_text}; main pushback is {pushback_name_text}."
                if pushback_name_text
                else f"Lead support comes from {support_name_text}; the rest of the desk is mostly neutral."
            )
            if question_is_reason or question_is_summary:
                if overall_verdict == "HOLD":
                    verdict_line = f"{symbol} is HOLD because the desk is mixed and no side has clean conviction."
                    data_line = (
                        lead_pushback_reason
                        or lead_support_reason
                        or report
                        or "The setup is directionally mixed."
                    )
                    team_line = (
                        f"The strongest push is {lead_support_name or 'limited support'}, but {lead_pushback_name or 'the rest of the desk'} keeps the call neutral."
                    )
                else:
                    verdict_line = (
                        f"{symbol} is {overall_verdict} because {lead_support_name or 'support is limited'} is driving the call."
                    )
                    data_line = lead_support_reason or report or "The setup is directionally mixed."
                    if lead_pushback_reason:
                        team_line = f"{lead_pushback_name} is the main drag because {lead_pushback_reason}"
            elif question_is_consensus:
                verdict_line = f"The desk is not fully aligned on {symbol}."
                data_line = (
                    f"Support comes from {support_name_text or 'limited support'}."
                )
                team_line = (
                    f"Pushback comes from {pushback_name_text or 'no strong bear case'}, while {neutral_name_text or 'the rest'} stay neutral."
                )
            elif question_is_risk:
                verdict_line = f"The main risk on {symbol} is {lead_pushback_name or 'low team alignment'}."
                data_line = lead_pushback_reason or neutral_reason_text or "Conviction is still thin across the desk."
                team_line = f"Desk confidence is only {overall_confidence:.0f}%, so this is not a clean one-way setup."
            elif question_is_data:
                verdict_line = f"{symbol} desk score is {self._to_float(market_signal.get('score')):.2f} with {overall_confidence:.0f}% confidence."
                data_line = team_metric_snapshot or f"{metric_label} is {metric_value}."
                team_line = (
                    f"{lead_support_name or 'No clear leader'} is doing most of the work, while {lead_pushback_name or 'the rest of the desk'} is the main offset."
                )
            elif question_is_research:
                verdict_line = f"Current research matters, but the {symbol} call is still mostly data-driven."
                data_line = research_line or "Current-affairs coverage is light, so the desk is leaning on live market structure."
                team_line = (
                    f"Desk support is led by {support_name_text or 'limited support'} with {pushback_name_text or 'no strong pushback'} as the offset."
                )
            elif question_is_action:
                verdict_line = (
                    f"Treat {symbol} as a watchlist {overall_verdict.lower()} bias, not a strong entry yet."
                    if overall_verdict == "HOLD" or overall_confidence < 34
                    else f"You can lean {overall_verdict.lower()} on {symbol}, but only with confirmation from the lead agents."
                )
                data_line = f"Lead support is {support_name_text or 'limited'}, and confidence is {overall_confidence:.0f}%."
                team_line = f"The main thing to watch is {lead_pushback_name or 'whether more agents join the move'}."
            if question_is_research and not research_line:
                research_line = "No strong current-affairs catalyst is dominating the tape right now"
            return self._format_agent_chat_answer(
                verdict=verdict_line,
                why=data_line,
                team=team_line,
                research=research_line if include_research else "",
                next_step=next_step,
            )

        metric_sentence = self._clean_text(f"{metric_label} is {metric_value}")
        why_line = local_thesis or report or metric_sentence
        data_gap = any(
            term in why_line.lower()
            for term in ("unavailable", "insufficient", "not available", "no trustworthy")
        )
        team_line = (
            f"Support comes from {support_name_text}; main disagreement is {pushback_name_text or 'limited'}."
            if support_names
            else f"No strong teammate is backing this read yet; main counterweight is {pushback_name_text or neutral_name_text or 'limited'}."
        )
        verdict_line = f"{agent_label} is {verdict}; full desk is {overall_verdict} with {overall_confidence:.0f}% confidence"
        if question_is_reason or question_is_summary:
            verdict_line = f"{agent_label} is {verdict} on {symbol}."
            why_line = local_thesis or report or metric_sentence
            if local_evidence and not data_gap:
                why_line = f"{why_line} Key evidence is {self._join_items(local_evidence[:2])}"
            team_line = (
                f"{lead_support_name or 'No strong teammate'} confirms this read, while {lead_pushback_name or 'the rest of the desk'} is the main offset."
            )
        elif question_is_data:
            verdict_line = f"{agent_label} data on {symbol} is straightforward."
            why_line = why_line if data_gap else metric_sentence
            if local_evidence and not data_gap:
                why_line = f"{metric_sentence}. Key evidence is {self._join_items(local_evidence[:3])}"
            team_line = (
                f"This lines up best with {lead_support_name or 'limited support'} and clashes most with {lead_pushback_name or 'no major pushback'}."
            )
        elif question_is_risk:
            verdict_line = f"The main risk in the {agent_label.lower()} read is {lead_pushback_name or 'low conviction'}."
            why_line = local_risk or lead_pushback_reason or "This agent does not have strong standalone edge right now."
            team_line = f"Desk pushback is coming from {pushback_name_text or neutral_name_text or 'the rest of the desk'}."
        elif question_is_consensus:
            verdict_line = f"{agent_label} is not operating in isolation on {symbol}."
            why_line = f"Support comes from {support_name_text or 'limited support'}."
            team_line = f"Main disagreement comes from {pushback_name_text or 'no strong pushback'}, with {neutral_name_text or 'the rest'} mostly neutral."
        elif question_is_research:
            verdict_line = f"{agent_label} is still mainly using live market context on {symbol}."
            why_line = research_line or "Current-affairs coverage is light, so this read is being driven by local market data."
            team_line = (
                f"My read lines up best with {support_name_text or 'the broader desk'} while {pushback_name_text or 'the rest stays balanced'} is the main offset."
            )
        elif question_is_levels and agent_key == "orderbook":
            verdict_line = f"Orderbook levels on {symbol} are not strongly one-sided right now."
            why_line = local_thesis or report or metric_sentence
            if local_evidence and not data_gap:
                why_line = f"{why_line} Key book evidence is {self._join_items(local_evidence[:2])}"
            team_line = f"The book read is mostly a confirmation layer while {lead_pushback_name or 'the broader desk'} sets the bigger tone."
        elif question_is_action:
            verdict_line = (
                f"Do not trade on {agent_label} alone; use it as a confirmation layer for the desk."
                if overall_verdict == "HOLD" or overall_confidence < 34
                else f"Use {agent_label} as a confirming agent for the desk's {overall_verdict} bias."
            )
            why_line = f"{metric_sentence}. {local_thesis or report or 'This is the main evidence from this agent.'}"
            team_line = f"{lead_support_name or 'No strong teammate'} is the best cross-check, while {lead_pushback_name or 'the rest of the desk'} is the main offset."
        elif not research_line and theme_text:
            research_line = f"Key live themes are {theme_text}"
        return self._format_agent_chat_answer(
            verdict=verdict_line,
            why=why_line,
            team=team_line,
            research=research_line if include_research else "",
            next_step=next_step,
        )

    def _analyst_fallback(self, question: str, all_markets_state: Dict[str, Any]) -> str | None:
        """Return a fallback answer when the analyst question is clearly unsupported."""
        if self._is_out_of_scope_question(question):
            return self.answer_fallback
        if not self._has_meaningful_analyst_context(all_markets_state):
            return self.answer_fallback
        return None

    def _is_out_of_scope_question(self, question: str) -> bool:
        """Return whether the user question is outside market-analysis scope."""
        lowered = question.lower()
        in_scope_terms = (
            "btc",
            "eth",
            "sol",
            "crypto",
            "market",
            "signal",
            "bullish",
            "bearish",
            "buy",
            "sell",
            "hold",
            "funding",
            "liquidation",
            "orderbook",
            "altfins",
            "news",
            "trend",
            "momentum",
            "backtest",
            "accuracy",
            "price",
            "valuation",
            "overextended",
            "trader",
            "usdc",
            "perp",
        )
        return not any(term in lowered for term in in_scope_terms)

    def _has_meaningful_analyst_context(self, all_markets_state: Dict[str, Any]) -> bool:
        """Return whether the analyst state contains enough market context to answer."""
        markets = all_markets_state.get("markets", {}) if isinstance(all_markets_state, dict) else {}
        if not isinstance(markets, dict) or not markets:
            return False
        return any(self._market_context_has_signal(market_state) for market_state in markets.values())

    def _has_meaningful_agent_chat_context(self, agent_chat_context: Dict[str, Any]) -> bool:
        """Return whether the dashboard agent-chat context contains usable information."""
        if not isinstance(agent_chat_context, dict):
            return False
        mode = str(agent_chat_context.get("mode", "single_market"))
        if mode == "all_markets":
            board = agent_chat_context.get("all_markets_board", [])
            return isinstance(board, list) and bool(board)
        workspace = agent_chat_context.get("workspace", {})
        if not isinstance(workspace, dict):
            return False
        return bool(workspace.get("report")) or bool(workspace.get("key_metric_value"))

    def _is_grounded_agent_answer(self, text: str, agent_chat_context: Dict[str, Any]) -> bool:
        """Return whether a model answer is grounded enough to show in the dashboard chat."""
        cleaned = self._clean_text(text)
        if self._should_rescue_answer(cleaned):
            return False
        lowered = cleaned.lower()
        if len(cleaned) < 32:
            return False
        if len(cleaned) < 90 and not any(cleaned.endswith(char) for char in (".", "!", "?")):
            return False
        if cleaned.endswith("%") or cleaned.endswith("$") or cleaned.endswith(":"):
            return False
        if cleaned.startswith('"') or " - " in cleaned and "quote" in lowered:
            return False
        if any(token in lowered for token in ("chris rock", "wealth is not about", "we are not able")):
            return False

        mode = str(agent_chat_context.get("mode", "single_market"))
        if mode == "all_markets":
            board = agent_chat_context.get("all_markets_board", [])
            symbols = [
                str(row.get("symbol", "")).lower()
                for row in board[:5]
                if isinstance(row, dict) and row.get("symbol")
            ]
            return any(symbol in lowered for symbol in symbols) or "market" in lowered

        workspace = agent_chat_context.get("workspace", {})
        if not isinstance(workspace, dict):
            return False
        symbol = str(workspace.get("symbol", "")).lower()
        agent_label = str(workspace.get("agent_label", "")).lower()
        keyword_hits = sum(
            1
            for token in (symbol, agent_label, "desk", "team", "verdict")
            if token and token in lowered
        )
        return keyword_hits >= 1

    def _classify_team_alignment(self, selected_verdict: str, teammate_verdict: str) -> str:
        """Classify whether a teammate supports, pushes back on, or is neutral to the selected agent."""
        selected = selected_verdict.upper()
        teammate = teammate_verdict.upper()
        bullish_values = {"BUY", "BULLISH"}
        bearish_values = {"SELL", "BEARISH"}
        neutral_values = {"HOLD", "NEUTRAL", "SCAN"}

        if selected in bullish_values:
            if teammate in bullish_values:
                return "support"
            if teammate in bearish_values:
                return "pushback"
            return "neutral"

        if selected in bearish_values:
            if teammate in bearish_values:
                return "support"
            if teammate in bullish_values:
                return "pushback"
            return "neutral"

        if selected in neutral_values:
            if teammate in neutral_values:
                return "support"
            return "pushback"

        return "neutral"

    def _format_compact_currency(self, value: Any) -> str:
        """Format a numeric value into a compact currency string."""
        numeric = self._to_float(value)
        absolute = abs(numeric)
        if absolute >= 1_000_000_000:
            return f"${numeric / 1_000_000_000:.2f}B"
        if absolute >= 1_000_000:
            return f"${numeric / 1_000_000:.2f}M"
        if absolute >= 1_000:
            return f"${numeric / 1_000:.2f}K"
        return f"${numeric:.2f}"

    def _format_percent(self, value: Any, digits: int = 1) -> str:
        """Format a numeric value as a percentage string."""
        return f"{self._to_float(value):.{digits}f}%"

    def _market_context_has_signal(self, market_state: Any) -> bool:
        """Return whether a single market context has enough signal-bearing data."""
        if not isinstance(market_state, dict):
            return False

        signal_engine = market_state.get("signal_engine", {})
        if isinstance(signal_engine, dict) and signal_engine.get("final_signal") in {"BUY", "SELL", "HOLD"}:
            return True

        altfins = market_state.get("altfins", {})
        if isinstance(altfins, dict) and altfins.get("available"):
            return True

        news_context = market_state.get("news_context", {})
        if isinstance(news_context, dict) and news_context.get("available"):
            return True

        for key in (
            "market_agent",
            "funding_agent",
            "liquidation_agent",
            "sentiment_agent",
            "narrative_agent",
            "orderbook_agent",
        ):
            payload = market_state.get(key, {})
            if isinstance(payload, dict) and payload.get("signal") in {"BULLISH", "BEARISH", "NEUTRAL"}:
                return True

        return False

    def _focus_symbol_from_question(self, question: str, all_markets_state: Dict[str, Any]) -> str | None:
        """Return the market symbol that the user is asking about."""
        markets = all_markets_state.get("markets", {}) if isinstance(all_markets_state, dict) else {}
        if not isinstance(markets, dict) or not markets:
            return None

        normalized_question = question.upper()
        for symbol in markets:
            if symbol.upper() in normalized_question:
                return symbol

        for symbol in markets:
            base_symbol = symbol.split("-")[0].upper()
            if base_symbol and base_symbol in normalized_question:
                return symbol

        return next(iter(markets), None)

    def _signal_fallback_from_context(
        self,
        symbol: str,
        signal_data: Dict[str, Any],
        agent_outputs: Dict[str, Any],
    ) -> str:
        """Build a deterministic two-sentence explanation when NeMo narration fails."""
        final_signal = str(signal_data.get("final_signal", "HOLD"))
        supportive_agents = self._supportive_agents(final_signal, agent_outputs)
        opposing_agents = self._opposing_agents(final_signal, agent_outputs)

        if supportive_agents:
            first_sentence = (
                f"{symbol} is {final_signal} because {self._join_items(supportive_agents)} are providing the strongest directional support."
            )
        else:
            first_sentence = (
                f"{symbol} is {final_signal} because the six-agent mix is not aligned strongly enough to create higher conviction."
            )

        if opposing_agents:
            second_sentence = (
                f"The main disagreement comes from {self._join_items(opposing_agents)}, while the remaining agents are mostly neutral."
            )
        else:
            second_sentence = "Funding and liquidation signals remain mostly neutral, so conviction comes from the stronger directional agents."

        return f"{first_sentence} {second_sentence}"

    def _supportive_agents(
        self,
        final_signal: str,
        agent_outputs: Dict[str, Any],
    ) -> list[str]:
        """Return the agents that support the current final signal."""
        if final_signal == "BUY":
            target_signal = "BULLISH"
        elif final_signal == "SELL":
            target_signal = "BEARISH"
        else:
            target_signal = ""

        if not target_signal:
            active_agents = [
                self._agent_label(name)
                for name, payload in agent_outputs.items()
                if isinstance(payload, dict) and payload.get("signal") in {"BULLISH", "BEARISH"}
            ]
            return [agent for agent in active_agents if agent][:3]

        agents = [
            self._agent_label(name)
            for name, payload in agent_outputs.items()
            if isinstance(payload, dict) and payload.get("signal") == target_signal
        ]
        return [agent for agent in agents if agent][:4]

    def _opposing_agents(
        self,
        final_signal: str,
        agent_outputs: Dict[str, Any],
    ) -> list[str]:
        """Return the agents that disagree with the current final signal."""
        if final_signal == "BUY":
            target_signal = "BEARISH"
        elif final_signal == "SELL":
            target_signal = "BULLISH"
        else:
            target_signal = ""

        if not target_signal:
            return []

        agents = [
            self._agent_label(name)
            for name, payload in agent_outputs.items()
            if isinstance(payload, dict) and payload.get("signal") == target_signal
        ]
        return [agent for agent in agents if agent][:3]

    def _agent_label(self, agent_name: str) -> str:
        """Convert an internal agent key into a trader-facing label."""
        labels = {
            "market": "market trend",
            "funding": "funding",
            "liquidation": "liquidations",
            "sentiment": "sentiment",
            "narrative": "narrative",
            "orderbook": "orderbook",
        }
        return labels.get(agent_name.lower(), "")

    def _join_items(self, items: list[str]) -> str:
        """Join labels into a natural-language list."""
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        if len(items) == 2:
            return f"{items[0]} and {items[1]}"
        return f"{', '.join(items[:-1])}, and {items[-1]}"

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
