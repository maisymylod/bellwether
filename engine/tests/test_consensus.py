from __future__ import annotations

import pytest

from bellwether.runtime.consensus import reduce_metrics


def _sample(revenue: float, net: float, commentary: str = "steady") -> dict:
    return {
        "metrics": [
            {
                "name": "revenue",
                "value": revenue,
                "unit": "musd",
                "period": "2025Q3",
                "source_ref": "X-2025Q3#FIN",
            },
            {
                "name": "net_income",
                "value": net,
                "unit": "musd",
                "period": "2025Q3",
                "source_ref": "X-2025Q3#FIN",
            },
        ],
        "trend": "stable",
        "commentary": commentary,
        "confidence": 0.8,
    }


def test_median_rejects_a_single_outlier():
    consensus = reduce_metrics([_sample(100.0, 20.0), _sample(101.0, 20.2), _sample(400.0, 20.1)])
    revenue = next(m for m in consensus.payload["metrics"] if m["name"] == "revenue")
    assert revenue["value"] == pytest.approx(101.0)
    assert consensus.dropped_outliers == 1
    assert revenue["agreement"] == pytest.approx(2 / 3, abs=1e-3)


def test_median_loses_when_the_majority_is_wrong():
    """The honest limit of n=3: two bad samples out of three beat the good one."""
    consensus = reduce_metrics([_sample(100.0, 20.0), _sample(400.0, 20.0), _sample(402.0, 20.0)])
    revenue = next(m for m in consensus.payload["metrics"] if m["name"] == "revenue")
    assert revenue["value"] == pytest.approx(400.0)


def test_full_agreement_scores_one_and_drops_nothing():
    consensus = reduce_metrics([_sample(100.0, 20.0)] * 3)
    assert consensus.agreement == 1.0
    assert consensus.dropped_outliers == 0


def test_single_sample_is_passed_through_untouched():
    sample = _sample(100.0, 20.0)
    consensus = reduce_metrics([sample])
    assert consensus.payload == sample
    assert consensus.agreement == 1.0
    assert consensus.samples == 1


def test_prose_comes_from_the_sample_closest_to_consensus():
    """The written commentary must not contradict the numbers that were kept."""
    consensus = reduce_metrics(
        [
            _sample(100.0, 20.0, commentary="near consensus"),
            _sample(101.0, 20.1, commentary="also near"),
            _sample(900.0, 90.0, commentary="wildly off"),
        ]
    )
    assert consensus.payload["commentary"] != "wildly off"


def test_no_samples_is_an_error():
    with pytest.raises(ValueError):
        reduce_metrics([])
