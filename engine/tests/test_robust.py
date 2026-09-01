from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from bellwether.providers.base import (
    Chunk,
    ModelRequest,
    ProviderError,
    TextChunk,
    UsageChunk,
)
from bellwether.runtime.robust import (
    UnrecoverableCall,
    call_structured,
    extract_json_object,
)

SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "integer"}, "b": {"type": "string"}},
    "required": ["a", "b"],
    "additionalProperties": False,
}


class Scripted:
    """Returns one scripted response per attempt."""

    name = "scripted"

    def __init__(self, script: list[str | Exception]) -> None:
        self.script = script
        self.calls: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[Chunk]:
        self.calls.append(request)
        step = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(step, Exception):
            raise step
        for char in step:
            yield TextChunk(char)
        yield UsageChunk(input_tokens=10, output_tokens=len(step))


def _request(**kwargs) -> ModelRequest:
    return ModelRequest(tag="t", system="s", prompt="p", schema=SCHEMA, **kwargs)


async def test_happy_path_returns_payload_and_usage():
    provider = Scripted(['{"a": 1, "b": "x"}'])
    outcome = await call_structured(provider, _request(), backoff_base_s=0.0)
    assert outcome.payload == {"a": 1, "b": "x"}
    assert outcome.output_tokens == len('{"a": 1, "b": "x"}')
    assert outcome.retries == 0 and outcome.repairs == 0


async def test_transient_error_is_retried():
    provider = Scripted([ProviderError("overloaded"), '{"a": 2, "b": "y"}'])
    outcome = await call_structured(provider, _request(), backoff_base_s=0.0)
    assert outcome.payload["a"] == 2
    assert outcome.retries == 1
    assert ("retry", "overloaded") in outcome.warnings


async def test_retries_are_bounded():
    provider = Scripted([ProviderError("always down")])
    with pytest.raises(UnrecoverableCall, match="always down"):
        await call_structured(provider, _request(), max_retries=2, backoff_base_s=0.0)
    assert len(provider.calls) == 3  # the first attempt plus two retries


async def test_non_retryable_error_is_not_retried():
    provider = Scripted([ProviderError("refused", retryable=False)])
    with pytest.raises(UnrecoverableCall):
        await call_structured(provider, _request(), max_retries=5, backoff_base_s=0.0)
    assert len(provider.calls) == 1


async def test_schema_violation_triggers_a_repair_not_a_retry():
    provider = Scripted(['{"a": "not an int", "b": "x"}', '{"a": 3, "b": "x"}'])
    outcome = await call_structured(provider, _request(), backoff_base_s=0.0)
    assert outcome.payload["a"] == 3
    assert outcome.repairs == 1
    assert outcome.retries == 0
    # The repair turn must carry the validator's own error, not a generic nudge.
    assert "Validation error" in provider.calls[1].prompt
    assert "'not an int' is not of type 'integer'" in provider.calls[1].prompt


async def test_repairs_are_bounded():
    provider = Scripted(['{"a": "bad", "b": "x"}'])
    with pytest.raises(UnrecoverableCall, match="schema invalid"):
        await call_structured(provider, _request(), max_repairs=1, backoff_base_s=0.0)
    assert len(provider.calls) == 2


async def test_tokens_from_discarded_attempts_still_stream():
    """The console shows what happened, including the attempt that was thrown away."""
    seen: list[str] = []
    provider = Scripted(['{"a": "bad", "b": "x"}', '{"a": 4, "b": "x"}'])
    await call_structured(provider, _request(), backoff_base_s=0.0, on_token=seen.append)
    assert "".join(seen) == '{"a": "bad", "b": "x"}{"a": 4, "b": "x"}'


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1, "b": "x"}',
        '```json\n{"a": 1, "b": "x"}\n```',
        'Here you go:\n{"a": 1, "b": "x"}\nHope that helps.',
        '  \n {"a": 1, "b": "x"}  ',
    ],
)
def test_extract_json_object_tolerates_wrapping(raw):
    assert extract_json_object(raw) == {"a": 1, "b": "x"}


@pytest.mark.parametrize("raw", ["", "   ", "no json here", "[1, 2, 3]", "42"])
def test_extract_json_object_rejects_non_objects(raw):
    with pytest.raises(ValueError):
        extract_json_object(raw)
