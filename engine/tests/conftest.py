from __future__ import annotations

import pytest

from bellwether.config import Settings
from bellwether.corpus import load_corpus
from bellwether.providers import FaultProfile, StubProvider


@pytest.fixture
def corpus():
    return load_corpus()


@pytest.fixture
def settings():
    """Test settings: no wall-clock delay anywhere, so backoff does not sleep."""
    return Settings(provider="stub", stub_token_delay_s=0.0, seed=99)


@pytest.fixture
def clean_provider(corpus):
    return StubProvider(seed=99, token_delay_s=0.0, faults=FaultProfile.clean(), corpus=corpus)
