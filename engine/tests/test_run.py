from __future__ import annotations

import json
from dataclasses import replace

import pytest

from bellwether.agents.subject import SubjectNotFound, parse_subject
from bellwether.providers import FaultProfile, StubProvider
from bellwether.run import run_to_completion

QUESTION = "How did Novaline Systems perform in 2025Q3 and what is the main risk?"


def _provider(seed: int, faults: FaultProfile | None = None) -> StubProvider:
    return StubProvider(seed=seed, token_delay_s=0.0, faults=faults)


async def test_a_clean_run_answers_and_grounds_everything(settings, clean_provider):
    board, report = await run_to_completion(
        question=QUESTION, provider=clean_provider, settings=settings
    )
    assert report["answer"]["key_points"]
    assert report["grounding"]["grounding_rate"] == 1.0
    assert report["critique"]["verdict"] == "accept"
    assert report["degraded"] is False
    assert set(report["nodes"].values()) == {"done"}
    assert board.ttft_ms is not None


async def test_the_same_seed_reproduces_the_same_run(settings):
    """Without this the eval numbers are noise and a bug report is unreproducible."""
    runs = []
    for _ in range(2):
        _, report = await run_to_completion(
            question=QUESTION, provider=_provider(seed=settings.seed), settings=settings
        )
        report["timing"] = None  # wall clock is the one thing that legitimately varies
        runs.append(json.dumps(report, sort_keys=True))
    assert runs[0] == runs[1]


async def test_different_seeds_produce_different_runs(settings):
    _, first = await run_to_completion(
        question=QUESTION, provider=_provider(seed=1), settings=replace(settings, seed=1)
    )
    _, second = await run_to_completion(
        question=QUESTION, provider=_provider(seed=2), settings=replace(settings, seed=2)
    )
    assert first["answer"] != second["answer"]


async def test_every_citation_in_a_clean_answer_resolves(settings, clean_provider, corpus):
    _, report = await run_to_completion(
        question=QUESTION, provider=clean_provider, settings=settings
    )
    for point in report["answer"]["key_points"]:
        for ref in point["source_refs"]:
            assert corpus.resolves(ref), ref


async def test_a_broken_citation_is_caught_and_the_point_is_flagged(settings):
    """The end-to-end version of the grounding check, driven by an injected fault."""
    faults = FaultProfile(
        schema_violation=0.0,
        bad_citation=1.0,
        transient_error=0.0,
        low_confidence=0.0,
        numeric_outlier=0.0,
    )
    _, report = await run_to_completion(
        question=QUESTION, provider=_provider(seed=5, faults=faults), settings=settings
    )
    assert report["grounding"]["unresolved"] > 0
    assert report["critique"]["verdict"] == "revise"
    assert report["degraded"] is True
    flagged = [p for p in report["answer"]["key_points"] if not p.get("supported", True)]
    assert flagged and all("rejected_because" in p for p in flagged)


async def test_the_revision_limit_is_enforced(settings):
    faults = FaultProfile(
        schema_violation=0.0,
        bad_citation=1.0,
        transient_error=0.0,
        low_confidence=0.0,
        numeric_outlier=0.0,
    )
    board, report = await run_to_completion(
        question=QUESTION,
        provider=_provider(seed=5, faults=faults),
        settings=replace(settings, max_revisions=1),
    )
    assert report["revisions"] <= 1
    assert not any(n.startswith("synthesis@r2") for n in report["nodes"])
    del board


async def test_total_provider_failure_degrades_rather_than_hangs(settings):
    """Everything below the synthesiser dying must still terminate the run."""
    faults = FaultProfile(
        schema_violation=0.0,
        bad_citation=0.0,
        transient_error=1.0,
        low_confidence=0.0,
        numeric_outlier=0.0,
    )
    board, report = await run_to_completion(
        question=QUESTION, provider=_provider(seed=3, faults=faults), settings=settings
    )
    assert report["failed_nodes"]
    finished = [e for e in board.snapshot() if e.type == "run.finished"]
    assert len(finished) == 1


async def test_events_are_emitted_in_a_usable_order(settings, clean_provider):
    board, _ = await run_to_completion(
        question=QUESTION, provider=clean_provider, settings=settings
    )
    events = board.snapshot()
    assert events[0].type == "run.started"
    assert events[-1].type == "run.finished"
    assert [e.seq for e in events] == list(range(1, len(events) + 1))

    # A node's tokens must never arrive before it is marked running, or the
    # console has nowhere to put them.
    running_at = {e.node: e.seq for e in events if e.type == "node.state" and e.state == "running"}
    for event in events:
        if event.type == "token":
            assert event.seq > running_at[event.node]


async def test_token_indices_are_contiguous_per_node(settings, clean_provider):
    board, _ = await run_to_completion(
        question=QUESTION, provider=clean_provider, settings=settings
    )
    per_node: dict[str, list[int]] = {}
    for event in board.snapshot():
        if event.type == "token":
            per_node.setdefault(event.node, []).append(event.index)
    assert per_node
    for node, indices in per_node.items():
        assert indices == list(range(len(indices))), node


def test_subject_resolution(corpus):
    assert parse_subject("what about NVLN", corpus).ticker == "NVLN"
    assert parse_subject("tell me about Novaline Systems", corpus).ticker == "NVLN"
    assert parse_subject("NVLN in 2025 Q2", corpus).period == "2025Q2"
    assert parse_subject("NVLN", corpus).period == "2025Q4"  # defaults to latest


def test_subject_resolution_refuses_to_guess(corpus):
    with pytest.raises(SubjectNotFound):
        parse_subject("how is the market doing", corpus)
    with pytest.raises(SubjectNotFound, match="no filing for 2019Q1"):
        parse_subject("NVLN in 2019Q1", corpus)
