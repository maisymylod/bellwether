"""Decides which specialists run, and tells each one what to look at."""

from __future__ import annotations

from dataclasses import dataclass

from bellwether.agents import prompts, schemas
from bellwether.agents.subject import Subject
from bellwether.runtime.agent import AgentResult, NodeContext


@dataclass
class Planner:
    subject: Subject
    question: str
    name: str = "planner"
    deps: tuple[str, ...] = ()

    async def run(self, ctx: NodeContext) -> AgentResult:
        outcome = await ctx.call(
            tag="plan",
            system=prompts.PLANNER_SYSTEM,
            prompt=prompts.planner_prompt(
                self.question, self.subject.ticker, self.subject.period, self.subject.company
            ),
            schema=schemas.PLAN,
            context={"ticker": self.subject.ticker, "period": self.subject.period},
        )
        return AgentResult(
            payload=outcome.payload,
            confidence=1.0,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            repairs=outcome.repairs,
            retries=outcome.retries,
        )
