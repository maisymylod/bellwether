"""Provider implementations and the factory that picks one from settings."""

from __future__ import annotations

from bellwether.config import Settings
from bellwether.providers.base import (
    Chunk,
    ModelRequest,
    Provider,
    ProviderError,
    TextChunk,
    UsageChunk,
    cache_key,
)
from bellwether.providers.cassette import RecordingProvider, ReplayProvider
from bellwether.providers.claude import ClaudeProvider, estimate_cost_usd
from bellwether.providers.stub import FaultProfile, StubProvider

__all__ = [
    "Chunk",
    "ClaudeProvider",
    "FaultProfile",
    "ModelRequest",
    "Provider",
    "ProviderError",
    "RecordingProvider",
    "ReplayProvider",
    "StubProvider",
    "TextChunk",
    "UsageChunk",
    "build_provider",
    "cache_key",
    "estimate_cost_usd",
]


def build_provider(settings: Settings) -> Provider:
    if settings.provider == "stub":
        return StubProvider(seed=settings.seed, token_delay_s=settings.stub_token_delay_s)
    if settings.provider == "replay":
        return ReplayProvider(
            settings.cassette_dir, settings.model, token_delay_s=settings.stub_token_delay_s
        )
    if settings.provider == "anthropic":
        return ClaudeProvider(settings.model)
    if settings.provider == "record":
        return RecordingProvider(
            ClaudeProvider(settings.model), settings.cassette_dir, settings.model
        )
    raise ValueError(f"unknown provider {settings.provider!r}")
