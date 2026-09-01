"""The four specialists. They fan out in parallel and never see each other.

Each one reads a different slice of the same filing, so the run is bounded by
the slowest specialist rather than by their sum. Only the fundamentals agent
pays for self-consistency sampling: it is the only one extracting numbers, and
a median over three samples is worth three times the tokens there and nowhere
else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bellwether.agents import prompts, schemas
from bellwether.agents.subject import Subject
from bellwether.runtime.agent import LOW_CONFIDENCE, AgentResult, NodeContext


def _flag_low_confidence(ctx: NodeContext, confidence: float) -> None:
    if confidence < LOW_CONFIDENCE:
        ctx.warn(
            "low_confidence",
            f"{ctx.node} returned confidence {confidence:.2f}; downweighted in synthesis",
        )


@dataclass
class Fundamentals:
    subject: Subject
    question: str
    name: str = "fundamentals"
    deps: tuple[str, ...] = ("planner",)

    async def run(self, ctx: NodeContext) -> AgentResult:
        history = [
            {
                "id": f.id,
                "period": f.period,
                "financials": f.financials,
                "segments": f.segments,
            }
            for f in ctx.corpus.history(self.subject.ticker)
            if f.period <= self.subject.period
        ]
        consensus, outcome = await ctx.call_consistent(
            tag="fundamentals",
            system=prompts.BASE_SYSTEM,
            prompt=prompts.fundamentals_prompt(self.question, history),
            schema=schemas.FUNDAMENTALS,
            context={"history": history},
        )
        # Sample disagreement is evidence about the answer, so it is folded into
        # the confidence the synthesiser sees rather than reported beside it.
        stated = float(consensus.payload.get("confidence", 0.5))
        confidence = round(stated * consensus.agreement, 3)
        payload: dict[str, Any] = dict(consensus.payload)
        payload["confidence"] = confidence
        payload["sample_agreement"] = consensus.agreement
        _flag_low_confidence(ctx, confidence)
        return AgentResult(
            payload=payload,
            confidence=confidence,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            repairs=outcome.repairs,
            retries=outcome.retries,
        )


@dataclass
class RiskFactors:
    subject: Subject
    question: str
    name: str = "risk"
    deps: tuple[str, ...] = ("planner",)

    async def run(self, ctx: NodeContext) -> AgentResult:
        filing = ctx.corpus.filing(self.subject.ticker, self.subject.period)
        outcome = await ctx.call(
            tag="risk",
            system=prompts.BASE_SYSTEM,
            prompt=prompts.risk_prompt(self.question, filing.id, filing.risk_factors),
            schema=schemas.RISK,
            context={"filing_id": filing.id, "risk_factors": filing.risk_factors},
        )
        confidence = float(outcome.payload.get("confidence", 0.5))
        _flag_low_confidence(ctx, confidence)
        return AgentResult(
            payload=outcome.payload,
            confidence=confidence,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            repairs=outcome.repairs,
            retries=outcome.retries,
        )


@dataclass
class NarrativeTone:
    subject: Subject
    question: str
    name: str = "tone"
    deps: tuple[str, ...] = ("planner",)

    async def run(self, ctx: NodeContext) -> AgentResult:
        filing = ctx.corpus.filing(self.subject.ticker, self.subject.period)
        outcome = await ctx.call(
            tag="tone",
            system=prompts.BASE_SYSTEM,
            prompt=prompts.tone_prompt(self.question, filing.id, filing.mdna),
            schema=schemas.TONE,
            context={"filing_id": filing.id, "mdna": filing.mdna},
        )
        confidence = float(outcome.payload.get("confidence", 0.5))
        _flag_low_confidence(ctx, confidence)
        return AgentResult(
            payload=outcome.payload,
            confidence=confidence,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            repairs=outcome.repairs,
            retries=outcome.retries,
        )


@dataclass
class PeerComparison:
    subject: Subject
    question: str
    name: str = "peers"
    deps: tuple[str, ...] = ("planner",)

    async def run(self, ctx: NodeContext) -> AgentResult:
        filing = ctx.corpus.filing(self.subject.ticker, self.subject.period)
        subject_block = {"id": filing.id, "ticker": filing.ticker, "financials": filing.financials}
        peers = []
        for peer_ticker in ctx.corpus.company(self.subject.ticker)["peers"]:
            try:
                peer_filing = ctx.corpus.filing(peer_ticker, self.subject.period)
            except KeyError:
                continue
            peers.append(
                {
                    "id": peer_filing.id,
                    "ticker": peer_filing.ticker,
                    "financials": peer_filing.financials,
                }
            )
        outcome = await ctx.call(
            tag="peers",
            system=prompts.BASE_SYSTEM,
            prompt=prompts.peers_prompt(self.question, subject_block, peers),
            schema=schemas.PEERS,
            context={"subject": subject_block, "peers": peers},
        )
        confidence = float(outcome.payload.get("confidence", 0.5))
        _flag_low_confidence(ctx, confidence)
        return AgentResult(
            payload=outcome.payload,
            confidence=confidence,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            repairs=outcome.repairs,
            retries=outcome.retries,
        )


WORKER_NAMES = ("fundamentals", "risk", "tone", "peers")
