"""Prompt text, kept in one file so it can be diffed and versioned on its own.

Two rules run through all of them. Cite by id or do not make the claim, because
an uncited claim is one the critic cannot check. And say when the evidence does
not support an answer, because a model that will not say "not disclosed"
produces a confident wrong answer instead of a useful gap.
"""

from __future__ import annotations

import json
from typing import Any

BASE_SYSTEM = (
    "You are one agent in an analysis pipeline reading a corpus of quarterly "
    "filings. Work only from the evidence given to you in the prompt. Never use "
    "outside knowledge about any company named here.\n\n"
    "Cite every factual claim with the evidence id it came from, exactly as it "
    "appears (for example NVLN-2025Q3#RF-2). If the evidence does not support an "
    "answer, say so in the field provided rather than estimating. A gap you "
    "report is useful; a number you invent is not.\n\n"
    "Set confidence to reflect the evidence, not your fluency: high when the "
    "figures are stated directly, low when you are inferring."
)

PLANNER_SYSTEM = (
    BASE_SYSTEM + "\n\nYou are the planner. Decide which specialists should look at this "
    "question and what each should focus on. Do not answer the question yourself."
)

CRITIC_SYSTEM = (
    BASE_SYSTEM + "\n\nYou are the critic. A deterministic pre-check has already resolved "
    "every citation against the corpus and told you which ones point at nothing. "
    "Do not repeat that work. Your job is the part code cannot do: decide whether "
    "each claim is actually supported by the text it cites, as opposed to merely "
    "citing something that exists. Mark a claim unsupported when the cited "
    "evidence does not say what the claim says. Be specific about why."
)


def _block(label: str, value: Any) -> str:
    return f"<{label}>\n{json.dumps(value, indent=2, sort_keys=True)}\n</{label}>"


def planner_prompt(question: str, ticker: str, period: str, company: str) -> str:
    return (
        f"Question: {question}\n\n"
        f"Subject: {company} ({ticker}), most recent filed period {period}.\n\n"
        "Produce the plan."
    )


def fundamentals_prompt(question: str, history: list[dict[str, Any]]) -> str:
    return (
        f"Question: {question}\n\n"
        "Extract the headline financials for the most recent period and describe "
        "the trend across the periods given. Every metric must cite the filing it "
        "came from.\n\n" + _block("filings", history)
    )


def risk_prompt(question: str, filing_id: str, risk_factors: list[dict[str, Any]]) -> str:
    return (
        f"Question: {question}\n\n"
        f"These are the risk factors disclosed in {filing_id}. Rank the ones that "
        "bear on the question and say why each matters, in one sentence.\n\n"
        + _block("risk_factors", risk_factors)
    )


def tone_prompt(question: str, filing_id: str, mdna: list[dict[str, str]]) -> str:
    return (
        f"Question: {question}\n\n"
        f"This is the management narrative from {filing_id}. Judge its posture and "
        "estimate what fraction of the paragraphs are hedged. Quote the paragraphs "
        "driving your read.\n\n" + _block("narrative", mdna)
    )


def peers_prompt(question: str, subject: dict[str, Any], peers: list[dict[str, Any]]) -> str:
    return (
        f"Question: {question}\n\n"
        "Compare the subject against each disclosed peer on the metrics given. "
        "Only compare figures that appear in the evidence.\n\n"
        + _block("subject", subject)
        + "\n"
        + _block("peers", peers)
    )


def synthesis_prompt(
    question: str,
    ticker: str,
    period: str,
    findings: dict[str, Any],
    missing: list[str],
    revision_note: str | None = None,
) -> str:
    parts = [
        f"Question: {question}\n",
        f"Subject: {ticker}, period {period}.\n",
        "Write the answer. Every key point must carry the evidence ids it rests "
        "on, taken from the findings below. Do not introduce a fact that is not "
        "in the findings.\n",
        _block("findings", findings),
    ]
    if missing:
        parts.append(
            "\nThese specialists did not return: "
            + ", ".join(missing)
            + ". Say plainly in the summary what the answer is missing as a result. "
            "Do not fill the gap."
        )
    if revision_note:
        parts.append(
            "\nThis is a revision. A reviewer rejected the previous answer:\n"
            + revision_note
            + "\nWithdraw or re-evidence each rejected point. Do not restate it with "
            "a different citation unless that citation genuinely supports it."
        )
    return "\n".join(parts)


def critique_prompt(claims: list[dict[str, Any]], evidence: dict[str, str]) -> str:
    return (
        "Check each claim against the evidence text it cites.\n\n"
        + _block("claims", claims)
        + "\n"
        + _block("cited_evidence", evidence)
    )
