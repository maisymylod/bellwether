"""The golden set, derived from the corpus rather than written by hand.

Hand-written expected answers rot the moment the corpus changes, and nobody
notices until the gate has been meaningless for a month. These cases are
computed from the same JSON the agents read, so the ground truth cannot drift
away from the data.

That is only possible because the questions asked here have arithmetic answers.
Nothing in this file pretends to grade prose. What it grades is: did the run
resolve the right subject, did it extract the right numbers, did it cite things
that exist, and did it say so when it could not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bellwether.corpus import Corpus, load_corpus

QUESTION_TEMPLATES = [
    "How did {company} perform in {period} and what is the main risk?",
    "Summarise {ticker} for {period}, including how it compares to its peers.",
    "What should I know about {ticker} in {period}?",
]


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    question: str
    ticker: str
    period: str
    # metric name -> value computed directly from the corpus
    expected_metrics: dict[str, float] = field(default_factory=dict)
    expected_severities: set[str] = field(default_factory=set)


def _expected_metrics(corpus: Corpus, ticker: str, period: str) -> dict[str, float]:
    history = [f for f in corpus.history(ticker) if f.period <= period]
    latest = history[-1]
    prior = history[-2] if len(history) > 1 else latest
    fin = latest.financials
    revenue = fin["revenue_musd"]
    return {
        "revenue": revenue,
        "net_income": fin["net_income_musd"],
        "free_cash_flow": fin["free_cash_flow_musd"],
        "gross_margin": round((revenue - fin["cost_of_revenue_musd"]) / revenue * 100, 1),
        "revenue_growth_qoq": round(
            (revenue - prior.financials["revenue_musd"])
            / max(prior.financials["revenue_musd"], 1e-9)
            * 100,
            1,
        ),
    }


def build_cases(corpus: Corpus | None = None) -> list[GoldenCase]:
    corpus = corpus or load_corpus()
    cases: list[GoldenCase] = []
    for index, ticker in enumerate(corpus.tickers()):
        company = str(corpus.company(ticker)["name"])
        for period in corpus.periods(ticker)[1:]:  # skip Q1: no prior quarter to compare
            template = QUESTION_TEMPLATES[(index + len(cases)) % len(QUESTION_TEMPLATES)]
            filing = corpus.filing(ticker, period)
            cases.append(
                GoldenCase(
                    case_id=f"{ticker}-{period}",
                    question=template.format(company=company, ticker=ticker, period=period),
                    ticker=ticker,
                    period=period,
                    expected_metrics=_expected_metrics(corpus, ticker, period),
                    expected_severities={r["severity"] for r in filing.risk_factors},
                )
            )
    return cases
