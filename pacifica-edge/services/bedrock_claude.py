"""Amazon Bedrock Claude Haiku 4.5 client for PacificaEdge."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

try:
    import boto3
    from botocore.config import Config
except ImportError:
    boto3 = None
    Config = None

logger = logging.getLogger(__name__)
DEFAULT_BEDROCK_REGION = "us-east-1"
DEFAULT_CLAUDE_HAIKU_MODEL_ID = "anthropic.claude-haiku-4-5-20251001-v1:0"


class BedrockClaudeClient:
    """Thin async wrapper for Claude Haiku 4.5 on Amazon Bedrock."""

    def __init__(self) -> None:
        """Initialize the Bedrock runtime client using a Bedrock API key."""
        if boto3 is None or Config is None:
            raise ValueError("boto3 is not installed")

        api_key = os.getenv("BEDROCK_API_KEY", "").strip() or os.getenv(
            "AWS_BEARER_TOKEN_BEDROCK", ""
        ).strip()
        if not api_key:
            raise ValueError("BEDROCK_API_KEY is not set")

        os.environ.setdefault("AWS_BEARER_TOKEN_BEDROCK", api_key)
        self.api_key = api_key
        self.region = os.getenv("BEDROCK_REGION", DEFAULT_BEDROCK_REGION).strip() or DEFAULT_BEDROCK_REGION
        self.model_id = (
            os.getenv("BEDROCK_MODEL_ID", DEFAULT_CLAUDE_HAIKU_MODEL_ID).strip()
            or DEFAULT_CLAUDE_HAIKU_MODEL_ID
        )
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=self.region,
            config=Config(
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )

    async def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 800,
        temperature: float = 0.2,
    ) -> str:
        """Generate plain text from Claude Haiku 4.5."""
        return await asyncio.to_thread(
            self._generate_text_sync,
            system_prompt,
            user_prompt,
            max_tokens,
            temperature,
        )

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 800,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """Generate a JSON object from Claude Haiku 4.5 with safe parsing."""
        text = await self.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if not text:
            return {"error": "Empty response from Bedrock Claude"}
        try:
            parsed = self._parse_json_content(text)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON from Bedrock Claude", "raw": text}
        if isinstance(parsed, dict):
            return parsed
        return {"error": "Invalid JSON from Bedrock Claude", "raw": text}

    def _generate_text_sync(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Run a synchronous Converse call and extract the text response."""
        logger.info("Calling Bedrock Claude model %s in %s", self.model_id, self.region)
        response = self.client.converse(
            modelId=self.model_id,
            system=[{"text": system_prompt}] if system_prompt else [],
            messages=[
                {
                    "role": "user",
                    "content": [{"text": user_prompt}],
                }
            ],
            inferenceConfig={
                "maxTokens": max_tokens,
                "temperature": temperature,
            },
        )
        return self._extract_text(response)

    def _extract_text(self, payload: Any) -> str:
        """Extract concatenated text blocks from a Converse response."""
        if not isinstance(payload, dict):
            return ""
        output = payload.get("output", {})
        if not isinstance(output, dict):
            return ""
        message = output.get("message", {})
        if not isinstance(message, dict):
            return ""
        content = message.get("content", [])
        if not isinstance(content, list):
            return ""
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text.strip())
        return " ".join(text_parts).strip()

    def _parse_json_content(self, content: str) -> Any:
        """Parse a JSON object from model text, tolerating wrapper text."""
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
