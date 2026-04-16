"""Async altFINS analytics client for PacificaEdge."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class AltFinsClient:
    """Fetch and normalize altFINS analytics for major PacificaEdge symbols."""

    def __init__(self) -> None:
        """Initialize the altFINS client from environment variables."""
        api_key = os.getenv("ALTFNS_API_KEY", "").strip() or os.getenv("ALTFINS_API_KEY", "").strip()
        if not api_key:
            raise ValueError("ALTFNS_API_KEY is not set")
        self.api_key = api_key
        self.base_url = os.getenv(
            "ALTFINS_API_BASE_URL",
            "https://altfins.com/api/v2/public",
        ).rstrip("/")
        self.timeout = 5.0

    async def get_asset_analytics(self, symbol: str) -> dict[str, Any]:
        """Return a normalized altFINS summary for an underlying asset symbol."""
        normalized_symbol = self._normalize_symbol(symbol)
        fallback = self._fallback_payload(normalized_symbol)

        if not normalized_symbol:
            fallback["error"] = "Unsupported altFINS symbol"
            return fallback

        try:
            screener_payload = await self._get_screener_snapshot(normalized_symbol)
            signals_payload = await self._get_trade_signals(normalized_symbol)
            technical_payload = await self._get_technical_analysis(normalized_symbol)

            screener_item = self._extract_first_record(screener_payload)
            screener_values = self._extract_metric_map(screener_item) if screener_item else {}
            technical_item = self._extract_first_record(technical_payload)
            signals = self._extract_signals(signals_payload, normalized_symbol)

            result = {
                "available": False,
                "symbol": normalized_symbol,
                "trend": self._build_trend(screener_values),
                "momentum": self._build_momentum(screener_values),
                "volatility": self._build_volatility(screener_values),
                "volume": self._build_volume(screener_values),
                "on_chain": self._build_on_chain(screener_values),
                "technical_analysis": self._build_technical_analysis(technical_item),
                "signals": signals,
                "timestamp": self._timestamp(),
            }

            has_usable_data = any(
                (
                    self._has_populated_values(result["trend"]),
                    self._has_populated_values(result["momentum"]),
                    self._has_populated_values(result["volatility"]),
                    self._has_populated_values(result["volume"]),
                    self._has_populated_values(result["on_chain"]),
                    self._has_populated_values(result["technical_analysis"]),
                    bool(result["signals"]),
                )
            )
            result["available"] = has_usable_data

            if not has_usable_data:
                result["error"] = "No altFINS analytics available"

            return result
        except Exception as exc:
            logger.warning("altFINS analytics failed for %s: %s", normalized_symbol, exc)
            fallback["error"] = str(exc)
            return fallback

    def summarize_for_signal(
        self,
        altfins: dict[str, Any],
        final_signal: str,
    ) -> dict[str, Any]:
        """Add derived altFINS summary fields for a PacificaEdge final signal."""
        normalized = dict(altfins)
        available = bool(normalized.get("available"))
        signals = normalized.get("signals", [])
        bullish_signal_count = self._count_signals(signals, bullish=True)
        bearish_signal_count = self._count_signals(signals, bullish=False)
        trend_bias, trend_strength = self._trend_bias(normalized.get("trend", {}))
        if available:
            alignment_with_signal = self._alignment_with_signal(
                final_signal=final_signal,
                trend_bias=trend_bias,
                bullish_signal_count=bullish_signal_count,
                bearish_signal_count=bearish_signal_count,
            )
            altfins_conviction = self._altfins_conviction(
                trend_strength=trend_strength,
                bullish_signal_count=bullish_signal_count,
                bearish_signal_count=bearish_signal_count,
                alignment_with_signal=alignment_with_signal,
            )
        else:
            alignment_with_signal = "no_data"
            altfins_conviction = "unknown"
        normalized["bullish_signal_count"] = bullish_signal_count
        normalized["bearish_signal_count"] = bearish_signal_count
        normalized["alignment_with_signal"] = alignment_with_signal
        normalized["altfins_conviction"] = altfins_conviction
        normalized["summary_block"] = {
            "htf_trend": self._trend_summary(normalized.get("trend", {})),
            "signals_overview": (
                f"{bullish_signal_count} bullish, {bearish_signal_count} bearish"
            ),
            "altfins_view": self._view_summary(alignment_with_signal, altfins_conviction),
        }
        return normalized

    async def _get_screener_snapshot(self, symbol: str) -> dict[str, Any]:
        """Fetch a screener snapshot with the metrics used by the analyst layer."""
        body = {
            "symbols": [symbol],
            "timeInterval": "DAILY",
            "displayType": [
                "SHORT_TERM_TREND",
                "MEDIUM_TERM_TREND",
                "LONG_TERM_TREND",
                "RSI14",
                "MACD",
                "MACD_SIGNAL_LINE",
                "MOM",
                "ATR",
                "TR_VS_ATR",
                "VOLUME",
                "VOLUME_RELATIVE",
                "OBV_TREND",
                "TVL",
                "MARKET_CAP_TVL",
                "MARKET_CAP_PR",
            ],
        }
        return await self._request(
            method="POST",
            path="/screener-data/search-requests",
            params={"page": 0, "size": 1},
            json_body=body,
        )

    async def _get_technical_analysis(self, symbol: str) -> dict[str, Any]:
        """Fetch curated technical-analysis commentary for a single symbol."""
        return await self._request(
            method="GET",
            path="/technical-analysis/data",
            params={"symbol": symbol, "page": 0, "size": 3},
        )

    async def _get_trade_signals(self, symbol: str) -> dict[str, Any]:
        """Fetch recent trade signals for a symbol from the signals feed."""
        return await self._request(
            method="POST",
            path="/signals-feed/search-requests",
            params={"page": 0, "size": 10},
            json_body={"symbols": [symbol]},
        )

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an altFINS API request and normalize failures."""
        url = f"{self.base_url}{path}"
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                if method == "GET":
                    response = await client.get(url, headers=headers, params=params)
                else:
                    response = await client.post(url, headers=headers, params=params, json=json_body)
                response.raise_for_status()
                payload: Any = response.json()
                if isinstance(payload, dict):
                    return payload
                return {"data": payload}
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            logger.warning("altFINS transport failed for %s: %r", url, exc)
            return {"error": str(exc) or exc.__class__.__name__}
        except Exception as exc:
            logger.warning("altFINS request failed for %s: %r", url, exc)
            return {"error": str(exc) or exc.__class__.__name__}

    def _extract_first_record(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Extract the first content record from a pageable altFINS payload."""
        if payload.get("error"):
            return None

        content = payload.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    return item

        data = payload.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    return item

        if isinstance(data, dict):
            nested_content = data.get("content")
            if isinstance(nested_content, list):
                for item in nested_content:
                    if isinstance(item, dict):
                        return item
            return data

        if isinstance(payload, dict):
            return payload
        return None

    def _extract_metric_map(self, record: dict[str, Any]) -> dict[str, Any]:
        """Flatten a screener record into a simple metric map keyed by field name."""
        metrics: dict[str, Any] = {}

        def consume(value: Any) -> None:
            if isinstance(value, dict):
                metric_name = self._to_string(
                    value.get("displayType")
                    or value.get("valueType")
                    or value.get("signalKey")
                    or value.get("id")
                    or value.get("key")
                )
                metric_value = (
                    value.get("value")
                    if value.get("value") is not None
                    else value.get("numericalValue")
                    if value.get("numericalValue") is not None
                    else value.get("nonNumericalValue")
                    if value.get("nonNumericalValue") is not None
                    else value.get("formattedValue")
                )
                if metric_name and metric_value is not None:
                    metrics[metric_name.upper()] = metric_value

                for key, nested in value.items():
                    if key in {
                        "displayType",
                        "valueType",
                        "signalKey",
                        "id",
                        "key",
                        "value",
                        "numericalValue",
                        "nonNumericalValue",
                        "formattedValue",
                    }:
                        continue
                    if isinstance(nested, (dict, list)):
                        consume(nested)
                    elif nested is not None and isinstance(nested, (str, int, float, bool)):
                        metrics.setdefault(str(key).upper(), nested)

            elif isinstance(value, list):
                for item in value:
                    consume(item)

        consume(record)
        return metrics

    def _extract_signals(self, payload: dict[str, Any], symbol: str) -> list[dict[str, Any]]:
        """Normalize recent signals from the altFINS signals feed."""
        if payload.get("error"):
            return []

        raw_items = payload.get("content")
        if not isinstance(raw_items, list):
            raw_items = payload.get("data")
        if not isinstance(raw_items, list):
            return []

        normalized: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            item_symbol = self._normalize_symbol(
                self._to_string(
                    item.get("symbol")
                    or item.get("asset")
                    or item.get("assetSymbol")
                    or item.get("coin")
                    or item.get("name")
                )
            )
            if item_symbol and item_symbol != symbol:
                continue

            signal_item: dict[str, Any] = {}
            self._assign_if_present(
                signal_item,
                "name",
                item,
                ["name", "signalName", "signalKey", "signalLabel"],
            )
            self._assign_if_present(
                signal_item,
                "direction",
                item,
                ["direction", "signalDirection", "marketDirection", "trendDirection"],
            )
            self._assign_if_present(
                signal_item,
                "timeframe",
                item,
                ["timeframe", "timeInterval", "interval"],
            )
            self._assign_if_present(
                signal_item,
                "status",
                item,
                ["status", "signalStatus"],
            )
            self._assign_if_present(
                signal_item,
                "probability",
                item,
                ["probability", "successProbability", "confidence", "probabilityPct"],
            )
            self._assign_if_present(
                signal_item,
                "expected_move_pct",
                item,
                ["expected_move_pct", "expectedMovePct", "expectedMove"],
            )
            self._assign_if_present(
                signal_item,
                "historical_win_rate",
                item,
                ["historical_win_rate", "historicalWinRate", "winRate", "successRate"],
            )
            self._assign_if_present(
                signal_item,
                "notes",
                item,
                ["notes", "description", "summary", "reason"],
            )
            self._assign_if_present(
                signal_item,
                "timestamp",
                item,
                ["timestamp", "createdAt", "updatedAt"],
            )

            if signal_item:
                normalized.append(signal_item)

        return normalized

    def _build_trend(self, metrics: dict[str, Any]) -> dict[str, Any]:
        """Build the normalized trend block."""
        trend: dict[str, Any] = {
            "short_term": None,
            "medium_term": None,
            "long_term": None,
        }
        self._put_metric(trend, "short_term", metrics, ["SHORT_TERM_TREND"])
        self._put_metric(trend, "medium_term", metrics, ["MEDIUM_TERM_TREND"])
        self._put_metric(trend, "long_term", metrics, ["LONG_TERM_TREND"])
        return trend

    def _build_momentum(self, metrics: dict[str, Any]) -> dict[str, Any]:
        """Build the normalized momentum block."""
        momentum: dict[str, Any] = {
            "rsi": None,
            "macd": None,
            "macd_signal": None,
            "momentum_score": None,
        }
        self._put_metric(momentum, "rsi", metrics, ["RSI14"])
        self._put_metric(momentum, "macd", metrics, ["MACD"])
        self._put_metric(momentum, "macd_signal", metrics, ["MACD_SIGNAL_LINE"])
        self._put_metric(momentum, "momentum_score", metrics, ["MOM"])
        return momentum

    def _build_volatility(self, metrics: dict[str, Any]) -> dict[str, Any]:
        """Build the normalized volatility block."""
        volatility: dict[str, Any] = {
            "atr": None,
            "volatility_score": None,
        }
        self._put_metric(volatility, "atr", metrics, ["ATR"])
        self._put_metric(volatility, "volatility_score", metrics, ["TR_VS_ATR"])
        return volatility

    def _build_volume(self, metrics: dict[str, Any]) -> dict[str, Any]:
        """Build the normalized volume block."""
        volume: dict[str, Any] = {
            "volume": None,
            "volume_score": None,
            "volume_trend": None,
        }
        self._put_metric(volume, "volume", metrics, ["VOLUME"])
        self._put_metric(volume, "volume_score", metrics, ["VOLUME_RELATIVE"])
        self._put_metric(volume, "volume_trend", metrics, ["OBV_TREND"])
        return volume

    def _build_on_chain(self, metrics: dict[str, Any]) -> dict[str, Any]:
        """Build the normalized on-chain / fundamental block."""
        on_chain: dict[str, Any] = {
            "tvl": None,
            "valuation_score": None,
            "profit_ratio": None,
        }
        self._put_metric(on_chain, "tvl", metrics, ["TVL"])
        self._put_metric(on_chain, "valuation_score", metrics, ["MARKET_CAP_PR"])
        self._put_metric(on_chain, "profit_ratio", metrics, ["MARKET_CAP_TVL"])
        return on_chain

    def _build_technical_analysis(self, record: dict[str, Any] | None) -> dict[str, Any]:
        """Build a compact technical-analysis summary block."""
        if not isinstance(record, dict):
            return {}

        technical_analysis: dict[str, Any] = {}
        self._assign_if_present(
            technical_analysis,
            "friendly_name",
            record,
            ["friendlyName"],
        )
        self._assign_if_present(
            technical_analysis,
            "updated_date",
            record,
            ["updatedDate"],
        )
        self._assign_if_present(
            technical_analysis,
            "near_term_outlook",
            record,
            ["nearTermOutlook"],
        )
        self._assign_if_present(
            technical_analysis,
            "pattern_type",
            record,
            ["patternType"],
        )
        self._assign_if_present(
            technical_analysis,
            "pattern_stage",
            record,
            ["patternStage"],
        )
        self._assign_if_present(
            technical_analysis,
            "description",
            record,
            ["description"],
        )
        return technical_analysis

    def _put_metric(
        self,
        destination: dict[str, Any],
        target_key: str,
        metrics: dict[str, Any],
        source_keys: list[str],
    ) -> None:
        """Copy a metric into a normalized block when present."""
        for source_key in source_keys:
            if source_key in metrics and metrics[source_key] is not None:
                normalized_value = self._normalize_metric_value(metrics[source_key])
                if normalized_value is not None:
                    destination[target_key] = normalized_value
                    return

    def _assign_if_present(
        self,
        destination: dict[str, Any],
        target_key: str,
        payload: dict[str, Any],
        source_keys: list[str],
    ) -> None:
        """Assign the first available value from a payload into a normalized key."""
        for source_key in source_keys:
            if source_key in payload and payload[source_key] is not None:
                destination[target_key] = payload[source_key]
                return

    def _normalize_symbol(self, symbol: str) -> str:
        """Normalize a Pacifica or altFINS symbol to its base ticker."""
        if not symbol:
            return ""
        normalized = str(symbol).split("-")[0].strip().upper()
        if not normalized or not normalized.isalnum():
            return ""
        return normalized

    def _fallback_payload(self, symbol: str) -> dict[str, Any]:
        """Return a safe empty altFINS analytics payload."""
        return {
            "available": False,
            "symbol": symbol,
            "trend": self._build_trend({}),
            "momentum": self._build_momentum({}),
            "volatility": self._build_volatility({}),
            "volume": self._build_volume({}),
            "on_chain": self._build_on_chain({}),
            "technical_analysis": {},
            "signals": [],
            "bullish_signal_count": 0,
            "bearish_signal_count": 0,
            "alignment_with_signal": "no_data",
            "altfins_conviction": "unknown",
            "summary_block": {
                "htf_trend": "No altFINS analytics available",
                "signals_overview": "0 bullish, 0 bearish",
                "altfins_view": "No altFINS analytics available / Unknown conviction",
            },
            "timestamp": self._timestamp(),
        }

    def _timestamp(self) -> str:
        """Return the current UTC timestamp in ISO 8601 format."""
        return datetime.now(timezone.utc).isoformat()

    def _to_string(self, value: Any) -> str:
        """Convert a value to string safely."""
        if value is None:
            return ""
        return str(value).strip()

    def _normalize_metric_value(self, value: Any) -> Any:
        """Normalize altFINS metric values into floats when possible."""
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned or cleaned == "-":
                return None
            numeric_candidate = cleaned.replace(",", "")
            if numeric_candidate.endswith("%"):
                numeric_candidate = numeric_candidate[:-1]
            try:
                return float(numeric_candidate)
            except ValueError:
                return cleaned
        return value

    def _has_populated_values(self, payload: Any) -> bool:
        """Return whether a normalized analytics block contains any real values."""
        if not isinstance(payload, dict):
            return False
        return any(
            value is not None and value != "" and value != [] and value != {}
            for value in payload.values()
        )

    def _count_signals(self, signals: Any, bullish: bool) -> int:
        """Count bullish or bearish signals from altFINS signal items."""
        if not isinstance(signals, list):
            return 0

        count = 0
        for signal in signals:
            if not isinstance(signal, dict):
                continue
            direction = self._to_string(signal.get("direction")).lower()
            name = self._to_string(signal.get("name")).lower()
            if bullish:
                if direction in {"long", "bullish", "buy"} or self._looks_bullish(name):
                    count += 1
            else:
                if direction in {"short", "bearish", "sell"} or self._looks_bearish(name):
                    count += 1
        return count

    def _looks_bullish(self, text: str) -> bool:
        """Check whether a signal name implies a bullish setup."""
        bullish_terms = (
            "bull",
            "breakout",
            "bounce",
            "support",
            "local high",
            "uptrend",
            "oversold",
            "accumulation",
            "reversal up",
            "golden cross",
            "long",
        )
        return any(term in text for term in bullish_terms)

    def _looks_bearish(self, text: str) -> bool:
        """Check whether a signal name implies a bearish setup."""
        bearish_terms = (
            "bear",
            "resistance",
            "overbought",
            "breakdown",
            "local low",
            "downtrend",
            "distribution",
            "reversal down",
            "death cross",
            "short",
        )
        return any(term in text for term in bearish_terms)

    def _trend_bias(self, trend: Any) -> tuple[str, int]:
        """Estimate overall altFINS trend bias and strength from trend labels."""
        if not isinstance(trend, dict):
            return "NEUTRAL", 0

        score = 0
        for value in trend.values():
            score += self._trend_value_score(value)

        if score >= 2:
            return "BULLISH", score
        if score <= -2:
            return "BEARISH", abs(score)
        return "NEUTRAL", abs(score)

    def _trend_value_score(self, value: Any) -> int:
        """Convert an altFINS trend label into a directional score."""
        text = self._to_string(value).lower()
        if not text:
            return 0
        if "strong down" in text:
            return -2
        if "strong up" in text:
            return 2
        if "down" in text or "bear" in text:
            return -1
        if "up" in text or "bull" in text:
            return 1
        return 0

    def _alignment_with_signal(
        self,
        final_signal: str,
        trend_bias: str,
        bullish_signal_count: int,
        bearish_signal_count: int,
    ) -> str:
        """Classify how well altFINS aligns with the final PacificaEdge signal."""
        normalized_signal = final_signal.upper()
        net_signal_bias = bullish_signal_count - bearish_signal_count

        if normalized_signal == "BUY":
            if trend_bias == "BULLISH" and net_signal_bias >= 2:
                return "strongly_aligned"
            if trend_bias != "BEARISH" and net_signal_bias >= 0:
                return "partially_aligned"
            if trend_bias == "BEARISH" or net_signal_bias < 0:
                return "conflicted"
            return "unclear"

        if normalized_signal == "SELL":
            if trend_bias == "BEARISH" and net_signal_bias <= -2:
                return "strongly_aligned"
            if trend_bias != "BULLISH" and net_signal_bias <= 0:
                return "partially_aligned"
            if trend_bias == "BULLISH" or net_signal_bias > 0:
                return "conflicted"
            return "unclear"

        if trend_bias == "NEUTRAL" and abs(net_signal_bias) <= 1:
            return "unclear"
        if trend_bias != "NEUTRAL" or abs(net_signal_bias) >= 2:
            return "conflicted"
        return "unclear"

    def _altfins_conviction(
        self,
        trend_strength: int,
        bullish_signal_count: int,
        bearish_signal_count: int,
        alignment_with_signal: str,
    ) -> str:
        """Estimate altFINS conviction level from trend strength and signal skew."""
        signal_skew = abs(bullish_signal_count - bearish_signal_count)
        if alignment_with_signal == "strongly_aligned" and (trend_strength >= 3 or signal_skew >= 4):
            return "high"
        if alignment_with_signal in {"strongly_aligned", "partially_aligned"} and (
            trend_strength >= 1 or signal_skew >= 2
        ):
            return "medium"
        return "low"

    def _trend_summary(self, trend: Any) -> str:
        """Build a concise higher-timeframe trend summary."""
        if not isinstance(trend, dict):
            return "Unavailable"
        short_term = self._to_string(trend.get("short_term")) or "n/a"
        medium_term = self._to_string(trend.get("medium_term")) or "n/a"
        long_term = self._to_string(trend.get("long_term")) or "n/a"
        return f"{short_term} / {medium_term} / {long_term}"

    def _view_summary(self, alignment_with_signal: str, altfins_conviction: str) -> str:
        """Build a one-line altFINS view string for UI display."""
        if alignment_with_signal == "no_data":
            return "No altFINS analytics available / Unknown conviction"
        alignment_text = alignment_with_signal.replace("_", " ").capitalize()
        conviction_text = altfins_conviction.capitalize()
        return f"{alignment_text} / {conviction_text} conviction"
