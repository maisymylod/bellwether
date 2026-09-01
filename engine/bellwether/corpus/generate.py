"""Deterministic generator for the synthetic filing corpus.

The corpus is invented. Nothing here is a real company and no number is a real
number. It exists so the engine has a body of text and figures with *stable
identifiers*: every risk factor, segment, and narrative paragraph carries an id
such as ``NVLN-2025Q3#RF-2``. Agents are required to cite those ids, which is
what turns "did the model make this up" into a decidable question the critic and
the eval harness can answer by lookup instead of by judgement.

Regenerate with ``python -m bellwether.corpus.generate``. The output is
committed so the corpus is stable across machines.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent / "data" / "filings.json"

COMPANIES = [
    ("NVLN", "Novaline Systems", "Payments infrastructure"),
    ("ARDX", "Ardent Exchange", "Market infrastructure"),
    ("KLTR", "Kiltar Capital Software", "Portfolio accounting"),
    ("PMBR", "Pembroke Data Group", "Reference data"),
    ("SOLV", "Solvent Treasury", "Corporate treasury"),
    ("HRVN", "Harrowvane Clearing", "Post-trade clearing"),
]

PEERS = {
    "NVLN": ["ARDX", "SOLV"],
    "ARDX": ["NVLN", "HRVN"],
    "KLTR": ["PMBR", "SOLV"],
    "PMBR": ["KLTR", "ARDX"],
    "SOLV": ["NVLN", "KLTR"],
    "HRVN": ["ARDX", "PMBR"],
}

PERIODS = ["2025Q1", "2025Q2", "2025Q3", "2025Q4"]
FILED = {
    "2025Q1": "2025-04-24",
    "2025Q2": "2025-07-25",
    "2025Q3": "2025-10-23",
    "2025Q4": "2026-02-12",
}

SEGMENT_SETS = {
    "NVLN": ["Acquiring", "Issuing", "Platform services"],
    "ARDX": ["Transaction fees", "Listings", "Data feeds"],
    "KLTR": ["Subscription", "Implementation", "Managed services"],
    "PMBR": ["Terminal", "Enterprise feeds", "Analytics"],
    "SOLV": ["Liquidity", "Payments", "Advisory"],
    "HRVN": ["Clearing fees", "Collateral", "Regulatory reporting"],
}

RISK_TEMPLATES = [
    (
        "Concentration of settlement volume",
        "A single sponsoring institution processed {pct}% of settlement volume in the "
        "period. Loss of that relationship would require re-papering the flow with an "
        "alternative sponsor, which management estimates would take {months} months.",
        "high",
    ),
    (
        "Interchange and fee schedule reform",
        "Proposed changes to the domestic fee schedule would compress take rate by an "
        "estimated {bps} basis points on affected volume. The proposal is in "
        "consultation and no effective date has been set.",
        "medium",
    ),
    (
        "Model risk in automated limit setting",
        "Credit limits for {count} accounts are set by an automated model. Model error "
        "has produced {incidents} limit-setting incidents in the period, none of which "
        "resulted in a realised loss above the reserve.",
        "medium",
    ),
    (
        "Third-party infrastructure dependency",
        "Core ledger workloads run in {regions} regions of a single cloud provider. A "
        "provider-wide outage would suspend authorisation, and the tested recovery "
        "objective is {rto} minutes.",
        "high",
    ),
    (
        "Foreign exchange translation",
        "{pct}% of revenue is denominated in currencies other than the reporting "
        "currency. A ten percent adverse move would reduce reported revenue by roughly "
        "${musd}m at constant volume.",
        "low",
    ),
    (
        "Retention of engineering staff",
        "Voluntary attrition in engineering was {pct}% annualised in the period. "
        "Replacement cost is carried at approximately ${musd}m in the period's "
        "operating expense.",
        "low",
    ),
    (
        "Pending regulatory examination",
        "An examination of the client asset segregation process opened in the period "
        "and remains open. No findings have been issued. The company has accrued "
        "${musd}m against expected remediation cost.",
        "medium",
    ),
]

MDNA_OPENERS = {
    "up": "Revenue grew {growth}% against the prior period, driven principally by {seg}.",
    "flat": "Revenue was broadly unchanged against the prior period, with growth in "
    "{seg} offset by softness elsewhere.",
    "down": "Revenue declined {growth}% against the prior period, principally in {seg}.",
}

MDNA_MIDDLES = [
    "Take rate was {take} basis points, {take_dir} the prior period.",
    "Operating expense grew {opexg}% as the company continued to invest in {invest}.",
    "Gross margin was {gm}%, and management expects it to remain in a similar range.",
]

# Deliberately hedged sentences. The tone agent is scored on whether it detects
# hedging density, so the generator has to actually vary it.
MDNA_HEDGES = [
    "Management believes, though cannot assure, that the trend continues.",
    "Results may not be indicative of future periods.",
    "The estimate is subject to revision as additional information becomes available.",
    "Timing of recognition could shift between periods.",
]

MDNA_CLOSERS = [
    "The company reaffirms its guidance for the coming period.",
    "The company has revised its guidance for the coming period downward.",
    "The company has raised its guidance for the coming period.",
]

INVEST_AREAS = [
    "the settlement platform",
    "regulatory reporting",
    "the data pipeline",
    "fraud modelling",
    "the client onboarding flow",
]


def _money(rng: random.Random, base: float, drift: float) -> float:
    return round(base * (1 + drift) * rng.uniform(0.97, 1.03), 1)


def build() -> dict[str, Any]:
    rng = random.Random(20260901)
    companies = [{"ticker": t, "name": n, "sector": s, "peers": PEERS[t]} for t, n, s in COMPANIES]
    filings: list[dict[str, Any]] = []

    for ticker, name, _sector in COMPANIES:
        base_rev = rng.uniform(180.0, 940.0)
        trajectory = rng.choice(["up", "up", "flat", "down"])
        per_q_drift = {"up": 0.052, "flat": 0.004, "down": -0.038}[trajectory]

        for qi, period in enumerate(PERIODS):
            fid = f"{ticker}-{period}"
            drift = per_q_drift * qi
            revenue = _money(rng, base_rev, drift)
            cost_of_revenue = round(revenue * rng.uniform(0.28, 0.44), 1)
            opex = round(revenue * rng.uniform(0.36, 0.52), 1)
            net_income = round(revenue - cost_of_revenue - opex, 1)
            fcf = round(net_income * rng.uniform(0.7, 1.35), 1)

            seg_names = SEGMENT_SETS[ticker]
            weights = [rng.uniform(0.2, 1.0) for _ in seg_names]
            total_w = sum(weights)
            segments = [
                {
                    "id": f"{fid}#SEG-{i + 1}",
                    "name": sn,
                    "revenue_musd": round(revenue * w / total_w, 1),
                }
                for i, (sn, w) in enumerate(zip(seg_names, weights, strict=True))
            ]

            picked = rng.sample(RISK_TEMPLATES, k=rng.randint(3, 5))
            risk_factors = []
            for i, (title, template, severity) in enumerate(picked):
                text = template.format(
                    pct=round(rng.uniform(11, 63), 1),
                    months=rng.randint(4, 18),
                    bps=rng.randint(3, 24),
                    count=rng.randint(1200, 48000),
                    incidents=rng.randint(1, 9),
                    regions=rng.randint(1, 3),
                    rto=rng.choice([15, 30, 45, 90]),
                    musd=round(rng.uniform(1.2, 38.0), 1),
                )
                risk_factors.append(
                    {
                        "id": f"{fid}#RF-{i + 1}",
                        "title": title,
                        "severity": severity,
                        "text": text,
                    }
                )

            direction = "up" if drift > 0.01 else ("down" if drift < -0.01 else "flat")
            growth = abs(round(per_q_drift * 100 * max(qi, 1), 1))
            paras = [
                MDNA_OPENERS[direction].format(growth=growth, seg=rng.choice(seg_names)),
                rng.choice(MDNA_MIDDLES).format(
                    take=round(rng.uniform(18, 96), 1),
                    take_dir=rng.choice(["above", "below", "in line with"]),
                    opexg=round(rng.uniform(1.0, 22.0), 1),
                    invest=rng.choice(INVEST_AREAS),
                    gm=round(100 * (revenue - cost_of_revenue) / revenue, 1),
                ),
            ]
            for _ in range(rng.randint(0, 3)):
                paras.append(rng.choice(MDNA_HEDGES))
            paras.append(rng.choice(MDNA_CLOSERS))
            rng.shuffle(paras)
            mdna = [{"id": f"{fid}#MD-{i + 1}", "text": p} for i, p in enumerate(paras)]

            filings.append(
                {
                    "id": fid,
                    "ticker": ticker,
                    "company": name,
                    "period": period,
                    "filed": FILED[period],
                    "financials": {
                        "revenue_musd": revenue,
                        "cost_of_revenue_musd": cost_of_revenue,
                        "opex_musd": opex,
                        "net_income_musd": net_income,
                        "free_cash_flow_musd": fcf,
                        "customers": rng.randint(400, 26000),
                    },
                    "segments": segments,
                    "risk_factors": risk_factors,
                    "mdna": mdna,
                    "guidance": {
                        "next_period_revenue_musd_low": round(revenue * 1.00, 1),
                        "next_period_revenue_musd_high": round(revenue * 1.09, 1),
                    },
                }
            )

    return {"companies": companies, "filings": filings}


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
