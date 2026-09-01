"""Record and replay, so a run against the live API can be reproduced exactly.

A recorded run is the only artefact that makes a nondeterministic system
debuggable after the fact. ``record`` wraps a live provider and writes each
call's chunk sequence to disk keyed by the request; ``replay`` serves those
chunks back with no network. A replayed run is byte-identical to the recorded
one, which is what lets a UI regression be reproduced from a bug report and lets
a prompt change be diffed against a fixed baseline instead of against noise.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

from bellwether.providers.base import (
    Chunk,
    ModelRequest,
    Provider,
    ProviderError,
    TextChunk,
    UsageChunk,
    cache_key,
)


class RecordingProvider:
    """Delegates to ``inner`` and writes what came back."""

    name = "record"

    def __init__(self, inner: Provider, directory: Path, model: str) -> None:
        self.inner = inner
        self.directory = directory
        self.model = model
        self.directory.mkdir(parents=True, exist_ok=True)

    async def stream(self, request: ModelRequest) -> AsyncIterator[Chunk]:
        captured: list[dict[str, object]] = []
        async for chunk in self.inner.stream(request):
            if isinstance(chunk, TextChunk):
                captured.append({"t": "text", "text": chunk.text})
            else:
                captured.append(
                    {
                        "t": "usage",
                        "input_tokens": chunk.input_tokens,
                        "output_tokens": chunk.output_tokens,
                    }
                )
            yield chunk
        path = self.directory / f"{cache_key(request, self.model)}.json"
        path.write_text(
            json.dumps({"tag": request.tag, "sample": request.sample, "chunks": captured}, indent=2)
            + "\n"
        )


class ReplayProvider:
    name = "replay"

    def __init__(self, directory: Path, model: str, *, token_delay_s: float = 0.0) -> None:
        self.directory = directory
        self.model = model
        self.token_delay_s = token_delay_s

    async def stream(self, request: ModelRequest) -> AsyncIterator[Chunk]:
        import asyncio

        path = self.directory / f"{cache_key(request, self.model)}.json"
        if not path.exists():
            # Non-retryable on purpose. A missing cassette is a gap in the
            # recording, not a flake, and retrying it just hides the gap.
            raise ProviderError(
                f"no cassette for {request.tag} at {path.name}; "
                "re-record with BELLWETHER_PROVIDER=record",
                retryable=False,
            )
        data = json.loads(path.read_text())
        for chunk in data["chunks"]:
            if chunk["t"] == "text":
                if self.token_delay_s:
                    await asyncio.sleep(self.token_delay_s)
                yield TextChunk(str(chunk["text"]))
            else:
                yield UsageChunk(
                    input_tokens=int(chunk["input_tokens"]),
                    output_tokens=int(chunk["output_tokens"]),
                )
