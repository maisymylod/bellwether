from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from bellwether.config import Settings
from bellwether.corpus import load_corpus
from bellwether.providers import StubProvider
from bellwether.runtime.agent import AgentResult, NodeContext
from bellwether.runtime.blackboard import Blackboard
from bellwether.runtime.scheduler import Scheduler


@dataclass
class Recorder:
    """An agent that records when it ran, and optionally misbehaves."""

    name: str
    deps: tuple[str, ...] = ()
    delay: float = 0.0
    raises: Exception | None = None
    log: list[tuple[str, str]] = field(default_factory=list)
    spawn: tuple[str, Recorder] | None = None

    async def run(self, ctx: NodeContext) -> AgentResult:
        self.log.append(("enter", ctx.node))
        if self.spawn is not None:
            name, agent = self.spawn
            ctx.schedule(name, agent, (ctx.node,))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises is not None:
            raise self.raises
        self.log.append(("exit", ctx.node))
        return AgentResult(payload={"node": ctx.node}, confidence=1.0)


def _scheduler(concurrency: int = 4) -> tuple[Scheduler, Blackboard]:
    board = Blackboard(run_id="r", question="q", subject="X", period="2025Q3")
    scheduler = Scheduler(
        board=board,
        corpus=load_corpus(),
        provider=StubProvider(token_delay_s=0.0),
        settings=Settings(provider="stub", stub_token_delay_s=0.0),
        max_concurrency=concurrency,
    )
    return scheduler, board


async def test_independent_nodes_run_concurrently():
    log: list[tuple[str, str]] = []
    scheduler, _ = _scheduler()
    scheduler.add("root", Recorder("root", log=log))
    for name in ("a", "b", "c"):
        scheduler.add(name, Recorder(name, delay=0.05, log=log), ("root",))
    await scheduler.run()
    # All three enter before any exits, which they cannot do if run serially.
    entries = [n for kind, n in log if kind == "enter" and n in ("a", "b", "c")]
    first_exit = next(i for i, (kind, n) in enumerate(log) if kind == "exit" and n in "abc")
    assert set(entries) == {"a", "b", "c"}
    assert sum(1 for kind, _ in log[:first_exit] if kind == "enter") == 4


async def test_concurrency_limit_is_respected():
    live = 0
    peak = 0

    @dataclass
    class Counter:
        name: str
        deps: tuple[str, ...] = ()

        async def run(self, ctx: NodeContext) -> AgentResult:
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.02)
            live -= 1
            return AgentResult(payload={}, confidence=1.0)

    scheduler, _ = _scheduler(concurrency=2)
    for name in "abcdef":
        scheduler.add(name, Counter(name))
    await scheduler.run()
    assert peak <= 2


async def test_a_node_waits_for_its_dependency():
    log: list[tuple[str, str]] = []
    scheduler, _ = _scheduler()
    scheduler.add("first", Recorder("first", delay=0.03, log=log))
    scheduler.add("second", Recorder("second", log=log), ("first",))
    await scheduler.run()
    assert log == [("enter", "first"), ("exit", "first"), ("enter", "second"), ("exit", "second")]


async def test_a_failed_dependency_does_not_block_the_dependent():
    """Four specialists feeding a synthesiser: one dying must narrow the answer,
    not cancel it."""
    scheduler, board = _scheduler()
    scheduler.add("ok", Recorder("ok"))
    scheduler.add("boom", Recorder("boom", raises=RuntimeError("nope")))
    scheduler.add("after", Recorder("after"), ("ok", "boom"))
    result = await scheduler.run()
    assert result.nodes["boom"] == "failed"
    assert result.nodes["after"] == "done"
    assert "boom" not in board.findings
    assert board.findings["after"] == {"node": "after"}


async def test_failure_is_reported_as_an_event():
    scheduler, board = _scheduler()
    scheduler.add("boom", Recorder("boom", raises=RuntimeError("nope")))
    await scheduler.run()
    failures = [e for e in board.snapshot() if e.type == "node.failed"]
    assert len(failures) == 1
    assert "RuntimeError: nope" in failures[0].error


async def test_an_agent_can_add_a_node_mid_run():
    """The critic uses this to put a revision of the synthesiser back on the queue."""
    log: list[tuple[str, str]] = []
    followup = Recorder("followup", log=log)
    scheduler, _board = _scheduler()
    scheduler.add("first", Recorder("first", log=log, spawn=("followup", followup)))
    result = await scheduler.run()
    assert result.nodes == {"first": "done", "followup": "done"}
    assert [n for kind, n in log if kind == "enter"] == ["first", "followup"]


async def test_cancellation_marks_everything_and_stops():
    scheduler, board = _scheduler()
    scheduler.add("slow", Recorder("slow", delay=5.0))
    scheduler.add("never", Recorder("never"), ("slow",))
    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.02)
    board.cancelled.set()
    result = await asyncio.wait_for(task, timeout=2.0)
    assert result.status == "cancelled"
    assert result.nodes["never"] == "skipped"


async def test_unknown_dependency_is_rejected_at_build_time():
    scheduler, _ = _scheduler()
    with pytest.raises(ValueError, match="unknown node"):
        scheduler.add("a", Recorder("a"), ("missing",))


async def test_duplicate_node_is_rejected():
    scheduler, _ = _scheduler()
    scheduler.add("a", Recorder("a"))
    with pytest.raises(ValueError, match="duplicate"):
        scheduler.add("a", Recorder("a"))


async def test_graph_description_matches_the_nodes_added():
    scheduler, _ = _scheduler()
    scheduler.add("a", Recorder("a"))
    scheduler.add("b", Recorder("b"), ("a",))
    nodes, edges = scheduler.graph()
    assert [n["id"] for n in nodes] == ["a", "b"]
    assert edges == [{"from": "a", "to": "b"}]
