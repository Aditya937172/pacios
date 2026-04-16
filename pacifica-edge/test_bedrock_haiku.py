"""Standalone Bedrock Claude Haiku 4.5 sanity test."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

from services.bedrock_claude import BedrockClaudeClient


async def main() -> None:
    """Call Claude Haiku 4.5 on Bedrock and print the response."""
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    try:
        client = BedrockClaudeClient()
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "message": "Set BEDROCK_API_KEY in your environment or .env before running this test.",
                },
                indent=2,
            )
        )
        return
    response = await client.generate_text(
        system_prompt="You are a concise assistant.",
        user_prompt="Reply with one short sentence confirming Claude Haiku 4.5 is reachable.",
        max_tokens=120,
    )
    print(
        json.dumps(
            {
                "model_id": client.model_id,
                "region": client.region,
                "response": response,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
