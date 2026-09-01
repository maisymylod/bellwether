"""The wire protocol between the engine and any client.

Every event is a JSON object with a ``type`` discriminator and a monotonic
``seq`` scoped to its run. ``seq`` is what makes the stream recoverable: a
client that reconnects sends the last ``seq`` it saw as ``Last-Event-ID`` and
the engine replays the tail from its buffer, so a dropped connection does not
cost the user a run.

The TypeScript union in ``web/src/lib/events.ts`` mirrors this file, and
``tests/test_events_contract.py`` asserts the two have not drifted.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

NodeState = Literal["queued", "running", "done", "failed", "skipped"]

WarningKind = Literal[
    "schema_repair",
    "retry",
    "contradiction",
    "low_confidence",
    "budget_exhausted",
    "truncated",
]


@dataclass(frozen=True)
class Event:
    """Base for every streamed event. Subclasses set ``type``."""

    type: str = field(init=False, default="event")
    seq: int = 0
    at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["type"] = self.type
        return payload


@dataclass(frozen=True)
class RunStarted(Event):
    type: str = field(init=False, default="run.started")
    run_id: str = ""
    question: str = ""
    subject: str = ""
    provider: str = "stub"
    model: str = ""
    # Declared up front so the client can draw the whole graph before any node runs.
    nodes: list[dict[str, str]] = field(default_factory=list)
    edges: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class NodeState_(Event):
    type: str = field(init=False, default="node.state")
    node: str = ""
    state: NodeState = "queued"
    attempt: int = 1
    # Carried on every state change because the graph is not fully known when
    # the run starts: the critic can schedule a revision, and the client has to
    # be able to place the new node rather than hang it off nothing.
    deps: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Token(Event):
    """One chunk of model output for one node.

    ``index`` is per node and strictly increasing. The client uses it to detect
    a gap after a reconnect, and to keep lanes ordered when several nodes stream
    concurrently over one connection.
    """

    type: str = field(init=False, default="token")
    node: str = ""
    index: int = 0
    text: str = ""


@dataclass(frozen=True)
class NodeResult(Event):
    type: str = field(init=False, default="node.result")
    node: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    repairs: int = 0
    retries: int = 0


@dataclass(frozen=True)
class NodeFailed(Event):
    type: str = field(init=False, default="node.failed")
    node: str = ""
    error: str = ""
    attempts: int = 0


@dataclass(frozen=True)
class Warning_(Event):
    type: str = field(init=False, default="warning")
    node: str = ""
    kind: WarningKind = "retry"
    detail: str = ""


@dataclass(frozen=True)
class Metric(Event):
    """Rolling run-level counters, emitted on change rather than on a timer."""

    type: str = field(init=False, default="metric")
    ttft_ms: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    nodes_running: int = 0
    nodes_done: int = 0
    nodes_total: int = 0


@dataclass(frozen=True)
class RunFinished(Event):
    type: str = field(init=False, default="run.finished")
    run_id: str = ""
    status: Literal["ok", "failed", "cancelled"] = "ok"
    duration_ms: int = 0
    report: dict[str, Any] = field(default_factory=dict)
    warnings: int = 0


def to_sse(event: Event) -> str:
    """Render an event as a Server-Sent Events frame.

    ``id:`` carries ``seq`` so the browser's ``EventSource`` hands it back as
    ``Last-Event-ID`` on reconnect without any client bookkeeping.
    """
    body = json.dumps(event.to_dict(), separators=(",", ":"), sort_keys=True)
    return f"id: {event.seq}\nevent: {event.type}\ndata: {body}\n\n"


EVENT_TYPES: tuple[str, ...] = (
    "run.started",
    "node.state",
    "token",
    "node.result",
    "node.failed",
    "warning",
    "metric",
    "run.finished",
)
