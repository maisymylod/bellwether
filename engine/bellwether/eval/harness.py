"""Runs the golden set and scores it.

Every case runs at its own seed, so the set covers a spread of injected-fault
draws rather than one lucky one. The same seed always produces the same run,
which is the only reason a number from this harness can be compared to a number
from last week.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from bellwether.config import Settings
from bellwether.corpus import Corpus, load_corpus
from bellwether.eval.golden import GoldenCase, build_cases
from bellwether.eval.metrics import NUMERIC_TOLERANCE, Report, RunScore, aggregate
from bellwether.events import NodeResult
from bellwether.providers import FaultProfile, StubProvider
from bellwether.providers.base import Provider
from bellwether.run import run_to_completion


def score_case(case: GoldenCase, report: dict[str, Any], corpus: Corpus) -> RunScore:
    answer = report.get("answer") or {}
    critique = report.get("critique") or {}
    notes: list[str] = []

    completed = bool(answer.get("key_points"))
    if not completed:
        notes.append("no answer produced")

    # Numeric accuracy is checked against the fundamentals finding, which is what
    # the figures in the answer are drawn from.
    reported: dict[str, float] = {}
    for node_payload in (report.get("nodes_payloads") or {}).values():
        for metric in node_payload.get("metrics", []):
            reported[str(metric.get("name"))] = float(metric.get("value", float("nan")))

    hits = 0
    total = 0
    for name, truth in case.expected_metrics.items():
        if name not in reported:
            continue
        total += 1
        scale = max(abs(truth), 1e-9)
        if abs(reported[name] - truth) / scale <= NUMERIC_TOLERANCE:
            hits += 1
        else:
            notes.append(f"{name}: reported {reported[name]}, expected {truth}")

    grounding = float((report.get("grounding") or {}).get("grounding_rate", 0.0))
    if grounding < 1.0:
        notes.append(f"{(report.get('grounding') or {}).get('unresolved', 0)} broken citations")

    return RunScore(
        case_id=case.case_id,
        completed=completed,
        grounding_rate=grounding,
        numeric_hits=hits,
        numeric_total=total,
        contradictions=len(critique.get("unsupported", [])),
        key_points=len(answer.get("key_points", [])),
        repairs=int(report.get("repairs", 0)),
        retries=int(report.get("retries", 0)),
        revisions=int(report.get("revisions", 0)),
        ttft_ms=(report.get("timing") or {}).get("ttft_ms"),
        total_ms=int((report.get("timing") or {}).get("total_ms", 0)),
        cost_usd=float((report.get("usage") or {}).get("cost_usd", 0.0)),
        notes=notes,
    )


async def run_case(
    case: GoldenCase,
    *,
    settings: Settings,
    corpus: Corpus,
    provider: Provider | None = None,
    seed: int = 0,
) -> RunScore:
    case_settings = replace(settings, seed=seed)
    provider = provider or StubProvider(seed=seed, token_delay_s=0.0)
    try:
        board, report = await run_to_completion(
            question=case.question,
            provider=provider,
            settings=case_settings,
            corpus=corpus,
        )
    except Exception as exc:
        return RunScore(
            case_id=case.case_id,
            completed=False,
            grounding_rate=0.0,
            numeric_hits=0,
            numeric_total=len(case.expected_metrics),
            contradictions=0,
            key_points=0,
            repairs=0,
            retries=0,
            revisions=0,
            ttft_ms=None,
            total_ms=0,
            cost_usd=0.0,
            notes=[f"run raised {type(exc).__name__}: {exc}"],
        )

    # The scorer needs the raw specialist payloads, not just the written answer.
    report = dict(report)
    report["nodes_payloads"] = dict(board.findings)
    results = [e for e in board.snapshot() if isinstance(e, NodeResult)]
    report["repairs"] = sum(e.repairs for e in results)
    report["retries"] = sum(e.retries for e in results)
    return score_case(case, report, corpus)


async def run_suite(
    *,
    settings: Settings | None = None,
    faults: FaultProfile | None = None,
    limit: int | None = None,
    base_seed: int = 4001,
) -> Report:
    settings = replace(settings or Settings(provider="stub"), stub_token_delay_s=0.0)
    corpus = load_corpus()
    cases = build_cases(corpus)
    if limit is not None:
        cases = cases[:limit]

    async def one(index: int, case: GoldenCase) -> RunScore:
        seed = base_seed + index
        provider = StubProvider(
            seed=seed,
            token_delay_s=0.0,
            faults=faults if faults is not None else FaultProfile(),
            corpus=corpus,
        )
        return await run_case(case, settings=settings, corpus=corpus, provider=provider, seed=seed)

    scores = await asyncio.gather(*(one(i, c) for i, c in enumerate(cases)))
    return aggregate(list(scores))
