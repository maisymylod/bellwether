"""What a run is scored on, and the thresholds CI enforces.

The four that matter, in the order they were added:

``grounding_rate``    citations that resolve to real corpus evidence
``numeric_accuracy``  extracted figures within tolerance of the arithmetic truth
``completion_rate``   runs that produced an answer at all
``contradiction_rate`` claims the critic could not support, in the delivered answer

Latency is tracked but not gated. Under the stub it measures the harness rather
than the model, and gating a number that does not mean anything is worse than
not gating it.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

# A metric counts as correct within this fraction of the true value. Wide enough
# to survive median-reduced sampling, tight enough that a wrong figure fails.
NUMERIC_TOLERANCE = 0.02


@dataclass
class Thresholds:
    """CI gates. Set from measured behaviour, not from ambition.

    These are the floors for the stub provider at its default fault rate. That
    is a deliberately hostile setting: roughly one call in ten is returned
    schema-invalid and one in eight with a broken citation on purpose. The
    numbers here are therefore what the *recovery machinery* achieves, not what
    a clean model would.

    Each floor sits below the worst value measured over three base seeds at the
    default three consistency samples, so the gate catches a regression rather
    than the ordinary spread. ``numeric_accuracy`` has the widest margin because
    it is the noisiest: a median over three samples loses when two of the three
    are wrong, which the fault profile makes happen. Raising the floor is a
    matter of raising the sample count, and the tradeoff is measured by
    ``bellwether sweep``.
    """

    completion_rate: float = 1.0
    grounding_rate: float = 0.94
    numeric_accuracy: float = 0.88
    contradiction_rate_max: float = 0.08
    schema_failure_rate_max: float = 0.02


@dataclass
class RunScore:
    case_id: str
    completed: bool
    grounding_rate: float
    numeric_hits: int
    numeric_total: int
    contradictions: int
    key_points: int
    repairs: int
    retries: int
    revisions: int
    ttft_ms: int | None
    total_ms: int
    cost_usd: float
    notes: list[str] = field(default_factory=list)


@dataclass
class Report:
    runs: int
    completion_rate: float
    grounding_rate: float
    numeric_accuracy: float
    contradiction_rate: float
    schema_failure_rate: float
    repairs_per_run: float
    retries_per_run: float
    revisions_per_run: float
    ttft_ms_p50: float
    total_ms_p50: float
    total_ms_p95: float
    cost_usd_per_run: float
    scores: list[RunScore] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scores"] = [asdict(s) for s in self.scores]
        return payload

    def failures(self, thresholds: Thresholds) -> list[str]:
        checks = [
            ("completion_rate", self.completion_rate, thresholds.completion_rate, "min"),
            ("grounding_rate", self.grounding_rate, thresholds.grounding_rate, "min"),
            ("numeric_accuracy", self.numeric_accuracy, thresholds.numeric_accuracy, "min"),
            (
                "contradiction_rate",
                self.contradiction_rate,
                thresholds.contradiction_rate_max,
                "max",
            ),
            (
                "schema_failure_rate",
                self.schema_failure_rate,
                thresholds.schema_failure_rate_max,
                "max",
            ),
        ]
        out = []
        for name, value, bound, direction in checks:
            if direction == "min" and value < bound:
                out.append(f"{name} {value:.3f} below floor {bound:.3f}")
            if direction == "max" and value > bound:
                out.append(f"{name} {value:.3f} above ceiling {bound:.3f}")
        return out


def _p(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(q * (len(ordered) - 1)))
    return round(ordered[index], 1)


def aggregate(scores: list[RunScore]) -> Report:
    if not scores:
        raise ValueError("no scores to aggregate")
    n = len(scores)
    completed = [s for s in scores if s.completed]
    numeric_total = sum(s.numeric_total for s in scores)
    numeric_hits = sum(s.numeric_hits for s in scores)
    key_points = sum(s.key_points for s in scores)
    contradictions = sum(s.contradictions for s in scores)
    grounding = [s.grounding_rate for s in completed] or [0.0]
    totals = [float(s.total_ms) for s in scores]
    ttfts = [float(s.ttft_ms) for s in scores if s.ttft_ms is not None]

    return Report(
        runs=n,
        completion_rate=round(len(completed) / n, 3),
        grounding_rate=round(statistics.mean(grounding), 3),
        numeric_accuracy=round(numeric_hits / numeric_total, 3) if numeric_total else 0.0,
        contradiction_rate=round(contradictions / key_points, 3) if key_points else 0.0,
        schema_failure_rate=round(sum(1 for s in scores if not s.completed) / n, 3),
        repairs_per_run=round(sum(s.repairs for s in scores) / n, 2),
        retries_per_run=round(sum(s.retries for s in scores) / n, 2),
        revisions_per_run=round(sum(s.revisions for s in scores) / n, 2),
        ttft_ms_p50=_p(ttfts, 0.5),
        total_ms_p50=_p(totals, 0.5),
        total_ms_p95=_p(totals, 0.95),
        cost_usd_per_run=round(sum(s.cost_usd for s in scores) / n, 6),
        scores=scores,
    )
