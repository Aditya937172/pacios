"""Async NVIDIA NeMo chat client."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _clean_secret(value: str | None) -> str:
    """Normalize env-provided credentials so quotes or CRLF do not leak into headers."""
    if not isinstance(value, str):
        return ""
    return value.strip().strip("\"'").replace("\r", "").replace("\n", "").strip()


class NeMoClient:
    """Async client for NVIDIA NeMo chat completions."""

    COMPLETIONS_PATH = "/chat/completions"

    def __init__(self) -> None:
        """Initialize the NeMo client from environment variables."""
        api_key = _clean_secret(os.getenv("NEMO_API_KEY"))
        if not api_key:
            raise ValueError("NEMO_API_KEY is not set")
        self.api_key = api_key
        self.base_url = _clean_secret(
            os.getenv("NEMO_API_BASE_URL", "https://integrate.api.nvidia.com/v1")
        ).rstrip("/")
        raw_model_name = _clean_secret(os.getenv("NEMO_MODEL_NAME", "nemotron-3-super-120b-a12b"))
        self.model_name = self._normalize_model_name(raw_model_name)

    async def chat_json(
        self, system_prompt: str, user_prompt: str, max_tokens: int = 512
    ) -> dict[str, Any]:
        """Call NeMo chat completion and parse a JSON object from the assistant response."""
        url = f"{self.base_url}{self.COMPLETIONS_PATH}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
            data: Any = response.json()
            assistant_text = self._extract_content(data)
            try:
                parsed = self._parse_json_content(assistant_text)
            except json.JSONDecodeError:
                return {"error": "Invalid JSON from NeMo", "raw": assistant_text}
            if isinstance(parsed, dict):
                return parsed
            return {"error": "Invalid JSON from NeMo", "raw": assistant_text}
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            logger.warning("NeMo transport failed for %s: %s", url, exc)
            return {"error": str(exc)}
        except Exception as exc:
            logger.warning("NeMo chat request failed for %s: %s", url, exc)
            return {"error": str(exc)}

    def _extract_content(self, payload: Any) -> str:
        """Extract assistant message content from a completion payload."""
        if not isinstance(payload, dict):
            raise ValueError("Invalid NeMo response payload")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("NeMo response missing choices")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ValueError("NeMo response choice is invalid")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("NeMo response missing message")
        content = message.get("content")
        if not isinstance(content, str):
            raise ValueError("NeMo response missing content")
        return content

    def _normalize_model_name(self, model_name: str) -> str:
        """Normalize NVIDIA hosted model names to the expected provider-qualified form."""
        if "/" in model_name:
            return model_name
        return f"nvidia/{model_name}"

    def _parse_json_content(self, content: str) -> Any:
        """Parse a JSON object from assistant content, tolerating wrapper text."""
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.lower().startswith("json"):
                stripped = stripped[4:].strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start == -1 or end == -1 or end < start:
                raise
            return json.loads(stripped[start : end + 1])
