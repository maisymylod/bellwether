"""Self-consistency: ask more than once, then decide what the answer was.

Asking a model for a number twice does not give you the same number twice. The
cheap, boring fix is to sample n times and take the median, which is robust to a
single wild sample in a way the mean is not. What comes back with the value is
an agreement score, so a caller can tell "three samples said the same thing"
apart from "three samples disagreed and the middle one won".

Deliberately narrow: this applies to numeric extraction, where a median is
meaningful. Prose is reduced by picking the sample closest to the consensus
numbers, not by averaging text.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

# A sample counts as agreeing if it is within this fraction of the median.
AGREEMENT_TOLERANCE = 0.05


@dataclass(frozen=True)
class Consensus:
    payload: dict[str, Any]
    agreement: float
    dropped_outliers: int
    samples: int


def _metric_key(metric: dict[str, Any]) -> tuple[str, str]:
    return (str(metric.get("name", "")), str(metric.get("period", "")))


def reduce_metrics(samples: list[dict[str, Any]]) -> Consensus:
    """Median-reduce the ``metrics`` list across samples of one extraction."""
    if not samples:
        raise ValueError("no samples to reduce")
    if len(samples) == 1:
        return Consensus(samples[0], agreement=1.0, dropped_outliers=0, samples=1)

    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for sample in samples:
        for metric in sample.get("metrics", []):
            if isinstance(metric.get("value"), int | float):
                buckets.setdefault(_metric_key(metric), []).append(metric)

    base = max(samples, key=lambda s: len(s.get("metrics", [])))
    merged: list[dict[str, Any]] = []
    agreements: list[float] = []
    dropped = 0

    for key, group in buckets.items():
        values = [float(m["value"]) for m in group]
        median = statistics.median(values)
        scale = max(abs(median), 1e-9)
        in_band = [v for v in values if abs(v - median) / scale <= AGREEMENT_TOLERANCE]
        dropped += len(values) - len(in_band)
        agreements.append(len(in_band) / len(values))
        template = dict(group[0])
        template["value"] = round(median, 4)
        template["agreement"] = round(len(in_band) / len(values), 3)
        template["samples"] = len(values)
        merged.append(template)
        del key

    # Preserve the ordering of the richest sample rather than dict insertion order.
    order = {_metric_key(m): i for i, m in enumerate(base.get("metrics", []))}
    merged.sort(key=lambda m: order.get(_metric_key(m), len(order)))

    payload = dict(base)
    if merged:
        payload["metrics"] = merged
    agreement = round(statistics.mean(agreements), 3) if agreements else 1.0

    # Non-numeric fields come from the sample whose numbers were closest to the
    # consensus, so the prose and the figures cannot disagree with each other.
    payload.update(_closest_sample_text(samples, merged))
    return Consensus(payload, agreement=agreement, dropped_outliers=dropped, samples=len(samples))


def _closest_sample_text(
    samples: list[dict[str, Any]], merged: list[dict[str, Any]]
) -> dict[str, Any]:
    if not merged:
        return {}
    consensus = {_metric_key(m): float(m["value"]) for m in merged}

    def distance(sample: dict[str, Any]) -> float:
        total = 0.0
        for metric in sample.get("metrics", []):
            target = consensus.get(_metric_key(metric))
            if target is None or not isinstance(metric.get("value"), int | float):
                continue
            total += abs(float(metric["value"]) - target) / max(abs(target), 1e-9)
        return total

    best = min(samples, key=distance)
    return {
        key: value
        for key, value in best.items()
        if key not in ("metrics",) and not isinstance(value, int | float)
    }
