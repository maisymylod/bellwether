from __future__ import annotations

import json

from bellwether.corpus.generate import build
from bellwether.corpus.loader import DATA_PATH


def test_every_citable_id_resolves(corpus):
    """The grounding check is only as good as this invariant."""
    for filing in corpus.filings:
        for ref in filing.citable_ids():
            assert corpus.resolve(ref) is not None, ref


def test_unknown_refs_do_not_resolve(corpus):
    for ref in ("", "nonsense", "NVLN-2025Q3", "NVLN-2025Q3#RF-99", "ZZZZ-2025Q1#FIN"):
        assert corpus.resolve(ref) is None
        assert corpus.resolves(ref) is False


def test_generator_is_deterministic():
    assert json.dumps(build(), sort_keys=True) == json.dumps(build(), sort_keys=True)


def test_committed_data_matches_generator():
    """Catches a hand-edited corpus, which would silently invalidate the golden set."""
    assert json.loads(DATA_PATH.read_text()) == build()


def test_history_is_ordered_and_latest_is_last(corpus):
    history = corpus.history("NVLN")
    assert [f.period for f in history] == sorted(f.period for f in history)
    assert corpus.latest("NVLN").period == history[-1].period
