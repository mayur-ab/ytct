"""Claude (Anthropic API) client for JSON metadata generation, via the official SDK."""
from __future__ import annotations

import json
from typing import Callable, Optional

import anthropic

from . import config


class AnthropicError(RuntimeError):
    pass


def chat_json(system: str, user: str, schema: dict, log: Optional[Callable[[str], None]] = None) -> dict:
    """Ask Claude for a JSON object that matches `schema` (structured outputs)."""
    key = config.anthropic_key()
    if not key:
        raise AnthropicError("ANTHROPIC_API_KEY is not set")
    # The SDK retries 429 / 5xx / connection errors with backoff.
    client = anthropic.Anthropic(api_key=key, max_retries=5, timeout=120.0)
    try:
        response = client.messages.create(
            model=config.anthropic_model(),
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={
                "effort": "low",  # short copywriting task
                "format": {"type": "json_schema", "schema": schema},
            },
        )
    except anthropic.AuthenticationError as exc:
        raise AnthropicError("Anthropic API key is invalid") from exc
    except anthropic.NotFoundError as exc:
        raise AnthropicError(f"Anthropic model not found: {config.anthropic_model()}") from exc
    except anthropic.RateLimitError as exc:
        raise AnthropicError("Anthropic rate limit reached - try again shortly") from exc
    except anthropic.APIStatusError as exc:
        raise AnthropicError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AnthropicError(f"Network error talking to Anthropic: {exc}") from exc

    if response.stop_reason == "refusal":
        reason = getattr(response.stop_details, "explanation", "") if response.stop_details else ""
        raise AnthropicError(f"Claude declined this clip{': ' + reason if reason else ''}")
    if response.stop_reason == "max_tokens":
        raise AnthropicError("Claude response was cut off (max_tokens)")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AnthropicError(f"Claude did not return valid JSON: {text[:300]}") from exc
