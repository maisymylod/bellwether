"""Runs the agent graph.

Three properties matter more than the topology itself.

*The graph grows during the run.* The critic can put the synthesiser back on the
queue as a new node. So this is not a topological sort computed once and walked;
it is a loop over "what is ready now", re-evaluated after every completion.

*A dependency is a wait, not a requirement.* A node runs once its dependencies
have reached a terminal state, whether or not they succeeded. Four workers
feeding a synthesiser should produce a narrower answer when one of them fails,
not no answer at all. Agents check what is actually on the blackboard and say
what they had to work without.

*Cancellation is immediate and clean.* An in-flight run holds live model calls;
when a client cancels, tasks are cancelled, nodes are marked, and a terminal
event is emitted so every subscriber's stream ends rather than hangs.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from bellwether.config import Settings
from bellwether.corpus import Corpus
from bellwether.events import NodeFailed, NodeResult, NodeState_
from bellwether.providers.base import Provider
from bellwether.runtime.agent import Agent, AgentResult, Node, NodeContext
from bellwether.runtime.blackboard import Blackboard
from bellwether.runtime.budget import BudgetExhausted
from bellwether.runtime.robust import UnrecoverableCall

TERMINAL = ("done", "failed", "skipped")


class Cancelled(RuntimeError):
    pass


@dataclass
class GraphResult:
    status: str
    nodes: dict[str, str]
    duration_ms: int


class Scheduler:
    def __init__(
        self,
        *,
        board: Blackboard,
        corpus: Corpus,
        provider: Provider,
        settings: Settings,
        max_concurrency: int = 4,
    ) -> None:
        self.board = board
        self.corpus = corpus
        self.provider = provider
        self.settings = settings
        self.max_concurrency = max_concurrency
        self.nodes: dict[str, Node] = {}
        self._order: list[str] = []

    def add(self, name: str, agent: Agent, deps: tuple[str, ...] = ()) -> None:
        if name in self.nodes:
            raise ValueError(f"duplicate node {name!r}")
        unknown = [d for d in deps if d not in self.nodes]
        if unknown:
            raise ValueError(f"node {name!r} depends on unknown node(s) {unknown}")
        self.nodes[name] = Node(name=name, agent=agent, deps=deps)
        self._order.append(name)
        self.board.node_states[name] = "queued"

    def graph(self) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        nodes = [{"id": n, "agent": self.nodes[n].agent.name} for n in self._order]
        edges = [{"from": dep, "to": name} for name in self._order for dep in self.nodes[name].deps]
        return nodes, edges

    # ------------------------------------------------------------------

    def _set_state(self, node: Node, state: str) -> None:
        node.state = state
        self.board.node_states[node.name] = state
        self.board.emit(
            NodeState_(
                node=node.name,
                state=state,  # type: ignore[arg-type]
                attempt=node.attempt,
                deps=list(node.deps),
            )
        )
        self.board.emit(self.board.metric_event(len(self.nodes)))

    def _ready(self) -> list[Node]:
        out = []
        for name in list(self._order):
            node = self.nodes[name]
            if node.state != "queued":
                continue
            if all(self.nodes[d].state in TERMINAL for d in node.deps):
                out.append(node)
        return out

    async def run(self) -> GraphResult:
        started = time.monotonic()
        running: dict[asyncio.Task[AgentResult], Node] = {}

        try:
            while True:
                if self.board.cancelled.is_set():
                    raise Cancelled

                for node in self._ready():
                    if len(running) >= self.max_concurrency:
                        break
                    node.started_at = time.monotonic()
                    self._set_state(node, "running")
                    ctx = NodeContext(
                        node=node.name,
                        board=self.board,
                        corpus=self.corpus,
                        provider=self.provider,
                        settings=self.settings,
                        budget=self.board.budget,
                        schedule=self._schedule_from_agent,
                    )
                    running[asyncio.create_task(node.agent.run(ctx))] = node

                if not running:
                    if any(n.state == "queued" for n in self.nodes.values()):
                        # Only reachable if a dependency cycle slipped past add().
                        raise RuntimeError("scheduler stalled with queued nodes")
                    break

                done, _ = await asyncio.wait(running.keys(), return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    node = running.pop(task)
                    self._settle(node, task)

        except (Cancelled, asyncio.CancelledError):
            for task, node in running.items():
                task.cancel()
                self._set_state(node, "skipped")
            if running:
                await asyncio.gather(*running.keys(), return_exceptions=True)
            for node in self.nodes.values():
                if node.state == "queued":
                    self._set_state(node, "skipped")
            return GraphResult(
                status="cancelled",
                nodes=dict(self.board.node_states),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        failed = any(n.state == "failed" for n in self.nodes.values())
        return GraphResult(
            status="failed" if failed and not self.board.findings else "ok",
            nodes=dict(self.board.node_states),
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def _settle(self, node: Node, task: asyncio.Task[AgentResult]) -> None:
        exc = task.exception()
        if exc is not None:
            if isinstance(exc, asyncio.CancelledError):
                self._set_state(node, "skipped")
                return
            if isinstance(exc, BudgetExhausted):
                self.board.warning_count += 1
            if not isinstance(exc, UnrecoverableCall | BudgetExhausted):
                # An agent raising something the runtime does not model is a bug,
                # not a degraded run. Fail the node, but keep the message intact.
                pass
            self.board.emit(
                NodeFailed(
                    node=node.name, error=f"{type(exc).__name__}: {exc}", attempts=node.attempt
                )
            )
            self._set_state(node, "failed")
            return

        result = task.result()
        self.board.record(node.name, result.payload, result.confidence)
        self.board.emit(
            NodeResult(
                node=node.name,
                payload=result.payload,
                confidence=result.confidence,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                duration_ms=node.elapsed_ms(),
                repairs=result.repairs,
                retries=result.retries,
            )
        )
        self._set_state(node, "done")

    def _schedule_from_agent(self, name: str, agent: Agent, deps: tuple[str, ...]) -> None:
        """Let a running agent add a node. Used by the critic to force a revision."""
        self.add(name, agent, deps)
