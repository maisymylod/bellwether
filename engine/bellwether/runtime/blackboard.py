"""Shared run state, and the event stream every client reads.

Agents do not call each other. They read from and write to this object, which
means adding an agent is a scheduling change rather than a refactor of whoever
would otherwise have called it.

The event log is append-only and retained for the life of the run. That buffer
is what makes reconnects cheap: a client that drops at seq 412 asks for
everything after 412 and gets it, instead of the run being restarted or the
first half of the output being lost.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from bellwether.events import Event, Metric
from bellwether.providers.claude import estimate_cost_usd
from bellwether.runtime.budget import Budget

# Enough to cover a full run of the graph at the default budget. Beyond this the
# oldest events are dropped and a reconnecting client is told the tail is gone,
# which is better than an unbounded buffer per run.
DEFAULT_BUFFER = 20_000


@dataclass
class Blackboard:
    run_id: str
    question: str
    subject: str
    period: str
    budget: Budget = field(default_factory=Budget)

    findings: dict[str, dict[str, Any]] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)
    node_states: dict[str, str] = field(default_factory=dict)

    started_at: float = field(default_factory=time.monotonic)
    first_token_at: float | None = None
    warning_count: int = 0
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)

    _seq: int = 0
    _log: deque[Event] = field(default_factory=lambda: deque(maxlen=DEFAULT_BUFFER))
    _subscribers: list[asyncio.Queue[Event | None]] = field(default_factory=list)
    _dropped: int = 0
    _closed: bool = False

    # -- event plumbing -----------------------------------------------------

    def emit(self, event: Event) -> Event:
        self._seq += 1
        object.__setattr__(event, "seq", self._seq)
        if len(self._log) == self._log.maxlen:
            self._dropped += 1
        self._log.append(event)
        for queue in self._subscribers:
            queue.put_nowait(event)
        return event

    def subscribe(self, after_seq: int = 0) -> asyncio.Queue[Event | None]:
        """Attach a client, replaying anything it has already missed.

        Backfill happens before the queue is registered, so an event emitted
        while the backfill is being copied cannot be delivered twice.
        """
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        for event in self._log:
            if event.seq > after_seq:
                queue.put_nowait(event)
        if self._closed:
            queue.put_nowait(None)
            return queue
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event | None]) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def close(self) -> None:
        self._closed = True
        for queue in self._subscribers:
            queue.put_nowait(None)
        self._subscribers.clear()

    def missed(self, after_seq: int) -> bool:
        """True if a client asking to resume from ``after_seq`` has lost events."""
        return bool(self._log) and after_seq < self._log[0].seq - 1

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def dropped(self) -> int:
        return self._dropped

    def snapshot(self) -> list[Event]:
        return list(self._log)

    # -- run state ----------------------------------------------------------

    def note_first_token(self) -> None:
        if self.first_token_at is None:
            self.first_token_at = time.monotonic()

    @property
    def ttft_ms(self) -> int | None:
        if self.first_token_at is None:
            return None
        return int((self.first_token_at - self.started_at) * 1000)

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)

    def record(self, node: str, payload: dict[str, Any], confidence: float) -> None:
        self.findings[node] = payload
        self.confidence[node] = confidence

    def metric_event(self, nodes_total: int) -> Metric:
        return Metric(
            ttft_ms=self.ttft_ms,
            input_tokens=self.budget.input_tokens,
            output_tokens=self.budget.output_tokens,
            cost_usd=estimate_cost_usd(self.budget.input_tokens, self.budget.output_tokens),
            nodes_running=sum(1 for s in self.node_states.values() if s == "running"),
            nodes_done=sum(1 for s in self.node_states.values() if s == "done"),
            nodes_total=nodes_total,
        )
