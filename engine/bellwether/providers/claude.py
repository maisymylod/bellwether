"""The live provider: Claude, with generation constrained to the agent's schema."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from bellwether.providers.base import Chunk, ModelRequest, ProviderError, TextChunk, UsageChunk

# Published per-million-token rates for the default model, used for the run cost
# readout. Override when pointing at a different model.
DEFAULT_RATES_USD_PER_MTOK = {"input": 5.00, "output": 25.00}

# Worker agents answer a narrow, well-specified question and do not need to
# deliberate; the synthesiser and critic do. Effort is the cheapest quality dial
# available, so it is set per role rather than globally.
EFFORT_BY_TAG = {
    "plan": "low",
    "fundamentals": "low",
    "risk": "medium",
    "tone": "medium",
    "peers": "low",
    "synthesis": "high",
    "critique": "high",
}


class ClaudeProvider:
    name = "anthropic"

    def __init__(self, model: str = "claude-opus-5", *, client: Any | None = None) -> None:
        self.model = model
        if client is not None:
            self._client = client
        else:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - dependency is declared
                raise ProviderError(
                    "the anthropic package is not installed", retryable=False
                ) from exc
            # Resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an `ant auth
            # login` profile, in that order. Nothing is hardcoded.
            self._client = anthropic.AsyncAnthropic()

    async def stream(self, request: ModelRequest) -> AsyncIterator[Chunk]:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.prompt}],
            "output_config": {
                "format": {"type": "json_schema", "schema": request.schema},
                "effort": EFFORT_BY_TAG.get(request.tag, "medium"),
            },
        }

        try:
            async with self._client.messages.stream(**params) as stream:
                async for event in stream:
                    if event.type != "content_block_delta":
                        continue
                    # Constrained output arrives as text deltas carrying JSON.
                    # Thinking deltas, if the model emits any, are not part of
                    # the answer and are dropped here rather than parsed.
                    if getattr(event.delta, "type", None) == "text_delta":
                        yield TextChunk(event.delta.text)
                final = await stream.get_final_message()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"claude stream failed: {exc}") from exc

        stop_reason = getattr(final, "stop_reason", None)
        if stop_reason == "refusal":
            raise ProviderError(
                f"refused: {getattr(getattr(final, 'stop_details', None), 'category', 'unknown')}",
                retryable=False,
            )
        if stop_reason == "max_tokens":
            raise ProviderError("response hit max_tokens and is truncated")

        usage = getattr(final, "usage", None)
        yield UsageChunk(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return round(
        input_tokens / 1e6 * DEFAULT_RATES_USD_PER_MTOK["input"]
        + output_tokens / 1e6 * DEFAULT_RATES_USD_PER_MTOK["output"],
        6,
    )
