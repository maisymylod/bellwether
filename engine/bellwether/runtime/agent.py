"""What an agent is, and the only handle it gets on the rest of the system.

An agent is a name, a set of dependencies, and a coroutine. It never sees the
scheduler, the event stream, or another agent; everything it can do goes through
:class:`NodeContext`. Keeping that surface small is what makes the graph
rearrangeable - a node can be moved, duplicated for a revision pass, or run
against a different provider without touching the agent itself.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from bellwether.config import Settings
from bellwether.corpus import Corpus
from bellwether.events import Token, Warning_, WarningKind
from bellwether.providers.base import ModelRequest, Provider
from bellwether.runtime.blackboard import Blackboard
from bellwether.runtime.budget import Budget
from bellwether.runtime.consensus import Consensus, reduce_metrics
from bellwether.runtime.robust import CallOutcome, call_structured

# Below this, a finding is reported but flagged, and the synthesiser is told not
# to lean on it. Chosen to be visible rather than tuned; it is a display and
# weighting threshold, not a correctness gate.
LOW_CONFIDENCE = 0.45


@dataclass
class AgentResult:
    payload: dict[str, Any]
    confidence: float = 1.0
    input_tokens: int = 0
    output_tokens: int = 0
    repairs: int = 0
    retries: int = 0


@dataclass
class NodeContext:
    node: str
    board: Blackboard
    corpus: Corpus
    provider: Provider
    settings: Settings
    budget: Budget
    schedule: Callable[[str, Agent, tuple[str, ...]], None]
    _token_index: int = field(default=0, init=False)

    # -- emission -----------------------------------------------------------

    def emit_token(self, text: str) -> None:
        first = self.board.first_token_at is None
        self.board.note_first_token()
        self.board.emit(Token(node=self.node, index=self._token_index, text=text))
        self._token_index += 1
        if first:
            # Metrics are otherwise only emitted on a node state change, which
            # would leave time-to-first-token blank while output is visibly
            # streaming. It is the number this system is judged on; show it the
            # moment it exists.
            self.board.emit(self.board.metric_event(len(self.board.node_states)))

    def warn(self, kind: WarningKind, detail: str) -> None:
        self.board.warning_count += 1
        self.board.emit(Warning_(node=self.node, kind=kind, detail=detail))

    # -- reading other agents' work ----------------------------------------

    def finding(self, node: str) -> dict[str, Any]:
        return self.board.findings.get(node, {})

    def has(self, node: str) -> bool:
        return node in self.board.findings

    # -- model calls --------------------------------------------------------

    async def call(
        self,
        *,
        tag: str,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        context: dict[str, Any] | None = None,
        sample: int = 0,
    ) -> CallOutcome:
        self.budget.check(self.settings.max_output_tokens)
        outcome = await call_structured(
            self.provider,
            ModelRequest(
                tag=tag,
                system=system,
                prompt=prompt,
                schema=schema,
                max_tokens=min(
                    self.settings.max_output_tokens, self.budget.remaining_output_tokens()
                ),
                context=context or {},
                seed=self.settings.seed,
                sample=sample,
            ),
            max_repairs=self.settings.max_repairs,
            backoff_base_s=0.0 if self.settings.stub_token_delay_s == 0 else 0.25,
            on_token=self.emit_token,
        )
        self.budget.charge(self.node, outcome.input_tokens, outcome.output_tokens)
        for kind, detail in outcome.warnings:
            self.warn(kind, detail)  # type: ignore[arg-type]
        return outcome

    async def call_consistent(
        self,
        *,
        tag: str,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        context: dict[str, Any] | None = None,
        samples: int | None = None,
    ) -> tuple[Consensus, CallOutcome]:
        """Sample the same question several times and median-reduce the numbers.

        Samples run concurrently, so the wall-clock cost is one call and the
        token cost is n. Only the first sample's tokens are streamed to the
        console; the rest would be three interleaved answers to the same
        question, which is noise on a screen and useful only in aggregate.
        """
        import asyncio

        n = samples if samples is not None else self.settings.consistency_samples
        n = max(1, n)
        self.budget.check(self.settings.max_output_tokens * n)

        async def one(index: int) -> CallOutcome:
            return await call_structured(
                self.provider,
                ModelRequest(
                    tag=tag,
                    system=system,
                    prompt=prompt,
                    schema=schema,
                    max_tokens=self.settings.max_output_tokens,
                    context=context or {},
                    seed=self.settings.seed,
                    sample=index,
                ),
                max_repairs=self.settings.max_repairs,
                backoff_base_s=0.0 if self.settings.stub_token_delay_s == 0 else 0.25,
                on_token=self.emit_token if index == 0 else None,
            )

        outcomes = await asyncio.gather(*(one(i) for i in range(n)))
        merged = CallOutcome(payload={}, raw=outcomes[0].raw)
        for outcome in outcomes:
            merged.input_tokens += outcome.input_tokens
            merged.output_tokens += outcome.output_tokens
            merged.repairs += outcome.repairs
            merged.retries += outcome.retries
            merged.warnings.extend(outcome.warnings)

        self.budget.charge(self.node, merged.input_tokens, merged.output_tokens)
        for kind, detail in merged.warnings:
            self.warn(kind, detail)  # type: ignore[arg-type]

        consensus = reduce_metrics([o.payload for o in outcomes])
        merged.payload = consensus.payload
        if consensus.dropped_outliers:
            self.warn(
                "low_confidence",
                f"{consensus.dropped_outliers} sampled value(s) were outliers across "
                f"{consensus.samples} samples; agreement {consensus.agreement}",
            )
        return consensus, merged


class Agent(Protocol):
    name: str
    deps: tuple[str, ...]

    async def run(self, ctx: NodeContext) -> AgentResult: ...


@dataclass
class Node:
    name: str
    agent: Agent
    deps: tuple[str, ...]
    state: str = "queued"
    attempt: int = 1
    started_at: float | None = None

    def elapsed_ms(self) -> int:
        if self.started_at is None:
            return 0
        return int((time.monotonic() - self.started_at) * 1000)
