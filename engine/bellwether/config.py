"""Process configuration, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent

VALID_PROVIDERS = ("stub", "replay", "record", "anthropic")


@dataclass(frozen=True)
class Settings:
    """Everything the engine needs to know before it starts a run.

    ``provider`` selects where model output comes from:

    ``stub``       deterministic synthetic output, no network, no key (the default)
    ``replay``     replay recorded cassettes, no network, no key
    ``record``     call the live API and write cassettes as a side effect
    ``anthropic``  call the live API
    """

    provider: str = "stub"
    model: str = "claude-opus-5"
    seed: int = 1729
    port: int = 8788
    cassette_dir: Path = PACKAGE_ROOT / "cassettes"
    # Wall-clock pacing for the stub provider, so the console has something to render.
    # Tests and evals set this to 0.0.
    stub_token_delay_s: float = 0.012
    # A single agent call may not exceed this many output tokens.
    max_output_tokens: int = 4096
    # How many times a schema-invalid response is handed back to the model to repair.
    max_repairs: int = 1
    # Samples used for self-consistency on numeric extraction.
    consistency_samples: int = 3
    # How many times the synthesizer may be re-run after the critic rejects it.
    max_revisions: int = 1

    @classmethod
    def from_env(cls) -> Settings:
        provider = os.environ.get("BELLWETHER_PROVIDER", "stub").strip().lower()
        if provider not in VALID_PROVIDERS:
            raise ValueError(
                f"BELLWETHER_PROVIDER={provider!r} is not one of {list(VALID_PROVIDERS)}"
            )
        return cls(
            provider=provider,
            model=os.environ.get("BELLWETHER_MODEL", "claude-opus-5"),
            seed=int(os.environ.get("BELLWETHER_SEED", "1729")),
            port=int(os.environ.get("BELLWETHER_PORT", "8788")),
            stub_token_delay_s=float(os.environ.get("BELLWETHER_STUB_DELAY", "0.012")),
        )

    def requires_api_key(self) -> bool:
        return self.provider in ("anthropic", "record")
