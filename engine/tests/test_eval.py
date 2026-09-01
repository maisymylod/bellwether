from __future__ import annotations

import pytest

from bellwether.config import Settings
from bellwether.eval.golden import build_cases
from bellwether.eval.harness import run_suite
from bellwether.eval.metrics import Thresholds, aggregate
from bellwether.providers import FaultProfile


def test_golden_cases_are_derived_from_the_corpus(corpus):
    cases = build_cases(corpus)
    assert cases
    for case in cases:
        assert case.period in corpus.periods(case.ticker)
        assert set(case.expected_metrics) == {
            "revenue",
            "net_income",
            "free_cash_flow",
            "gross_margin",
            "revenue_growth_qoq",
        }
        # The expected figures must be the corpus figures, not a copy that can drift.
        filing = corpus.filing(case.ticker, case.period)
        assert case.expected_metrics["revenue"] == filing.financials["revenue_musd"]


@pytest.mark.parametrize("base_seed", [4001, 9001, 15001])
async def test_the_gate_holds_across_seeds(base_seed):
    """The thresholds are floors under measured behaviour, not aspirations."""
    report = await run_suite(base_seed=base_seed)
    assert report.failures(Thresholds()) == []


async def test_a_clean_provider_hits_the_ceilings():
    """With faults off, the machinery must not be what is losing the points."""
    report = await run_suite(faults=FaultProfile.clean(), base_seed=4001)
    assert report.completion_rate == 1.0
    assert report.grounding_rate == 1.0
    assert report.contradiction_rate == 0.0
    assert report.repairs_per_run == 0.0
    assert report.retries_per_run == 0.0


async def test_broken_citations_alone_breach_the_gate():
    """Isolate the fault the grounding check exists for and confirm it is caught."""
    faults = FaultProfile(
        schema_violation=0.0,
        bad_citation=1.0,
        transient_error=0.0,
        low_confidence=0.0,
        numeric_outlier=0.0,
    )
    report = await run_suite(faults=faults, base_seed=4001)
    assert report.grounding_rate < 0.8
    assert "grounding_rate" in " ".join(report.failures(Thresholds()))


async def test_the_revision_loop_measurably_improves_grounding():
    """The critic sending the synthesiser back has to be worth its tokens."""
    faults = FaultProfile(
        schema_violation=0.0,
        bad_citation=1.0,
        transient_error=0.0,
        low_confidence=0.0,
        numeric_outlier=0.0,
    )
    without = await run_suite(
        faults=faults,
        base_seed=4001,
        settings=Settings(provider="stub", max_revisions=0),
    )
    with_revision = await run_suite(
        faults=faults,
        base_seed=4001,
        settings=Settings(provider="stub", max_revisions=1),
    )
    assert with_revision.grounding_rate > without.grounding_rate
    assert with_revision.contradiction_rate < without.contradiction_rate


async def test_every_content_fault_at_once_still_produces_an_answer():
    """A pileup must degrade, not crash. The repair turn does most of the work
    here: it re-rolls the whole response, which clears the injected citation
    error along with the schema error that triggered it."""
    faults = FaultProfile(
        schema_violation=1.0,
        bad_citation=1.0,
        transient_error=0.0,
        low_confidence=1.0,
        numeric_outlier=1.0,
    )
    report = await run_suite(faults=faults, base_seed=4001, limit=6)
    assert report.completion_rate == 1.0
    assert report.repairs_per_run > 0
    # Outliers at 100% defeat a median over three samples, and the suite says so.
    assert report.numeric_accuracy < 0.6


async def test_total_transport_failure_is_reported_not_swallowed():
    faults = FaultProfile(
        schema_violation=0.0,
        bad_citation=0.0,
        transient_error=1.0,
        low_confidence=0.0,
        numeric_outlier=0.0,
    )
    report = await run_suite(faults=faults, base_seed=4001, limit=3)
    assert report.completion_rate == 0.0
    assert report.schema_failure_rate == 1.0


async def test_more_samples_does_not_hurt_numeric_accuracy():
    """The claim self-consistency makes, checked rather than assumed."""
    one = await run_suite(settings=Settings(provider="stub", consistency_samples=1), base_seed=4001)
    five = await run_suite(
        settings=Settings(provider="stub", consistency_samples=5), base_seed=4001
    )
    assert five.numeric_accuracy >= one.numeric_accuracy
    assert five.cost_usd_per_run > one.cost_usd_per_run


def test_aggregate_rejects_an_empty_suite():
    with pytest.raises(ValueError):
        aggregate([])
