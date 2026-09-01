"""Assembles the graph, runs it, and turns what came out into a report."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from bellwether.agents.planner import Planner
from bellwether.agents.subject import Subject, parse_subject
from bellwether.agents.synthesis import Critic, Synthesiser
from bellwether.agents.workers import (
    WORKER_NAMES,
    Fundamentals,
    NarrativeTone,
    PeerComparison,
    RiskFactors,
)
from bellwether.config import Settings
from bellwether.corpus import Corpus, load_corpus
from bellwether.events import RunFinished, RunStarted
from bellwether.providers.base import Provider
from bellwether.providers.claude import estimate_cost_usd
from bellwether.runtime.blackboard import Blackboard
from bellwether.runtime.budget import Budget
from bellwether.runtime.scheduler import Scheduler


@dataclass
class Run:
    run_id: str
    question: str
    board: Blackboard
    scheduler: Scheduler
    task: asyncio.Task[None] | None = None

    def cancel(self) -> None:
        self.board.cancelled.set()


def build_scheduler(
    *,
    board: Blackboard,
    corpus: Corpus,
    provider: Provider,
    settings: Settings,
    subject: Subject,
    question: str,
) -> Scheduler:
    scheduler = Scheduler(
        board=board, corpus=corpus, provider=provider, settings=settings, max_concurrency=4
    )
    scheduler.add("planner", Planner(subject=subject, question=question))
    scheduler.add("fundamentals", Fundamentals(subject=subject, question=question), ("planner",))
    scheduler.add("risk", RiskFactors(subject=subject, question=question), ("planner",))
    scheduler.add("tone", NarrativeTone(subject=subject, question=question), ("planner",))
    scheduler.add("peers", PeerComparison(subject=subject, question=question), ("planner",))
    scheduler.add("synthesis", Synthesiser(subject=subject, question=question), WORKER_NAMES)
    scheduler.add("critique", Critic(subject=subject, question=question), ("synthesis",))
    return scheduler


def start_run(
    *,
    question: str,
    provider: Provider,
    settings: Settings,
    corpus: Corpus | None = None,
    budget: Budget | None = None,
    run_id: str | None = None,
) -> Run:
    """Create the run and its event stream, then hand back before it finishes.

    The board carries a ``run.started`` event before the task is scheduled, so a
    client that subscribes the instant this returns cannot miss the header.
    """
    corpus = corpus or load_corpus()
    subject = parse_subject(question, corpus)
    run_id = run_id or uuid.uuid4().hex[:12]

    board = Blackboard(
        run_id=run_id,
        question=question,
        subject=subject.ticker,
        period=subject.period,
        budget=budget or Budget(),
    )
    scheduler = build_scheduler(
        board=board,
        corpus=corpus,
        provider=provider,
        settings=settings,
        subject=subject,
        question=question,
    )
    nodes, edges = scheduler.graph()
    board.emit(
        RunStarted(
            run_id=run_id,
            question=question,
            subject=f"{subject.company} ({subject.ticker}) {subject.period}",
            provider=getattr(provider, "name", settings.provider),
            model=settings.model,
            nodes=nodes,
            edges=edges,
        )
    )

    run = Run(run_id=run_id, question=question, board=board, scheduler=scheduler)

    async def drive() -> None:
        try:
            result = await scheduler.run()
            board.emit(
                RunFinished(
                    run_id=run_id,
                    status=result.status,  # type: ignore[arg-type]
                    duration_ms=result.duration_ms,
                    report=build_report(board, scheduler),
                    warnings=board.warning_count,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            board.emit(
                RunFinished(
                    run_id=run_id,
                    status="failed",
                    duration_ms=board.elapsed_ms,
                    report={"error": f"{type(exc).__name__}: {exc}"},
                    warnings=board.warning_count,
                )
            )
        finally:
            board.close()

    run.task = asyncio.create_task(drive())
    return run


async def run_to_completion(
    *,
    question: str,
    provider: Provider,
    settings: Settings,
    corpus: Corpus | None = None,
    budget: Budget | None = None,
) -> tuple[Blackboard, dict[str, Any]]:
    run = start_run(
        question=question,
        provider=provider,
        settings=settings,
        corpus=corpus,
        budget=budget,
    )
    assert run.task is not None
    await run.task
    final = [e for e in run.board.snapshot() if e.type == "run.finished"]
    report = final[-1].to_dict()["report"] if final else {}
    return run.board, report


def _latest(prefix: str, board: Blackboard) -> str | None:
    """Highest-numbered revision of a node that actually completed."""
    candidates = [
        name
        for name, state in board.node_states.items()
        if state == "done" and (name == prefix or name.startswith(f"{prefix}@r"))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda n: int(n.split("@r")[1]) if "@r" in n else 0)


def build_report(board: Blackboard, scheduler: Scheduler) -> dict[str, Any]:
    synth_node = _latest("synthesis", board)
    critic_node = _latest("critique", board)
    answer = board.findings.get(synth_node, {}) if synth_node else {}
    critique = board.findings.get(critic_node, {}) if critic_node else {}

    failed = [n for n, s in board.node_states.items() if s == "failed"]
    missing = list(answer.get("missing_specialists", []))
    revisions = int(answer.get("revision", 0))
    answer = _annotate_support(answer, critique)

    return {
        "answer": answer,
        "critique": critique,
        "grounding": critique.get("grounding", {}),
        "subject": board.subject,
        "period": board.period,
        "revisions": revisions,
        "failed_nodes": failed,
        "missing_specialists": missing,
        "degraded": bool(failed or missing or critique.get("unsupported")),
        "warnings": board.warning_count,
        "usage": {
            "input_tokens": board.budget.input_tokens,
            "output_tokens": board.budget.output_tokens,
            "calls": board.budget.calls,
            "cost_usd": estimate_cost_usd(board.budget.input_tokens, board.budget.output_tokens),
        },
        "timing": {"ttft_ms": board.ttft_ms, "total_ms": board.elapsed_ms},
        "nodes": {name: node.state for name, node in scheduler.nodes.items()},
    }


def _annotate_support(answer: dict[str, Any], critique: dict[str, Any]) -> dict[str, Any]:
    """Mark, rather than delete, the points the critic could not support.

    Deleting them would make the answer look cleaner than the run was. A reader
    is better served by seeing that a claim was made, rejected, and why - and a
    UI can then render it struck through instead of pretending it never existed.
    """
    if not answer:
        return answer
    reasons = {u["point"]: u["reason"] for u in critique.get("unsupported", [])}
    if not reasons:
        return answer
    annotated = dict(answer)
    annotated["key_points"] = [
        {
            **point,
            "supported": point.get("point") not in reasons,
            **(
                {"rejected_because": reasons[point["point"]]}
                if point.get("point") in reasons
                else {}
            ),
        }
        for point in answer.get("key_points", [])
    ]
    annotated["unsupported_points"] = len(reasons)
    return annotated
