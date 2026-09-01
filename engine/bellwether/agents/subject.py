"""Work out what a question is about, without spending a model call on it.

Resolving a ticker out of a sentence is string matching. Doing it with a model
would add a network round trip, a failure mode, and a nondeterminism source to
a step that has exactly one right answer, so it is done here instead. The
planner is left to decide what to *do*, which is the part that needs judgement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bellwether.corpus import Corpus

PERIOD_RE = re.compile(r"\b(20\d{2})\s*[Qq]([1-4])\b")


class SubjectNotFound(ValueError):
    pass


@dataclass(frozen=True)
class Subject:
    ticker: str
    company: str
    period: str


def parse_subject(question: str, corpus: Corpus) -> Subject:
    lowered = question.lower()

    ticker = None
    for candidate in corpus.tickers():
        if re.search(rf"\b{candidate.lower()}\b", lowered):
            ticker = candidate
            break
    if ticker is None:
        for candidate in corpus.tickers():
            name = str(corpus.company(candidate)["name"]).lower()
            if name in lowered or name.split()[0] in lowered:
                ticker = candidate
                break
    if ticker is None:
        raise SubjectNotFound(
            "no company in the corpus is named in the question; "
            f"known tickers are {', '.join(corpus.tickers())}"
        )

    periods = corpus.periods(ticker)
    match = PERIOD_RE.search(question)
    if match:
        period = f"{match.group(1)}Q{match.group(2)}"
        if period not in periods:
            raise SubjectNotFound(
                f"{ticker} has no filing for {period}; available: {', '.join(periods)}"
            )
    else:
        period = periods[-1]

    return Subject(ticker=ticker, company=str(corpus.company(ticker)["name"]), period=period)
