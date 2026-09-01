"""The seam between the agent runtime and whatever is actually producing tokens.

Everything above this line is provider-agnostic. Swapping ``stub`` for
``anthropic`` changes where tokens come from and nothing else, which is what
lets the same code path be exercised offline in CI and against the live API in
production.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ModelRequest:
    """One constrained generation.

    ``schema`` is a JSON Schema the output must satisfy. Providers are expected
    to constrain generation to it where the backend supports that, but the
    runtime validates the result regardless: a constraint that is only enforced
    server-side is a constraint you cannot test.
    """

    tag: str
    system: str
    prompt: str
    schema: dict[str, Any]
    max_tokens: int = 4096
    # Structured facts behind ``prompt``. The live provider ignores this (the
    # facts are already rendered into the prompt text); the stub reads it so it
    # can synthesise an answer grounded in the same evidence.
    context: dict[str, Any] = field(default_factory=dict)
    # Identity of this call for deterministic replay and seeded stub output.
    seed: int = 0
    sample: int = 0
    attempt: int = 1


@dataclass(frozen=True)
class TextChunk:
    text: str


@dataclass(frozen=True)
class UsageChunk:
    input_tokens: int
    output_tokens: int


Chunk = TextChunk | UsageChunk


class ProviderError(RuntimeError):
    """A call failed in a way the runtime is allowed to retry."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@runtime_checkable
class Provider(Protocol):
    name: str

    def stream(self, request: ModelRequest) -> AsyncIterator[Chunk]:
        """Yield chunks of one response. Must be safe to call concurrently."""
        ...


def cache_key(request: ModelRequest, model: str) -> str:
    """Stable identity for a call, used as the cassette filename.

    Deliberately excludes ``attempt``: a retry of a failed call should replay
    the same recorded response rather than miss the cassette.
    """
    import hashlib
    import json

    material = json.dumps(
        {
            "model": model,
            "tag": request.tag,
            "system": request.system,
            "prompt": request.prompt,
            "schema": request.schema,
            "sample": request.sample,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{request.tag}-{hashlib.sha256(material.encode()).hexdigest()[:16]}"
