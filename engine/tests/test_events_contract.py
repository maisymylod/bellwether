"""The two halves of the wire protocol, checked against each other.

`events.py` and `web/src/lib/events.ts` are written by hand. Nothing stops them
drifting except this test, which reads the TypeScript source and fails if the
lists no longer agree. A generated client would remove the need for it; at eight
event types, the test is smaller than the generator would be.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bellwether.events import EVENT_TYPES, WarningKind
from bellwether.runtime.scheduler import TERMINAL

TS_EVENTS = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "events.ts"
TS_WARNINGS = TS_EVENTS


def _ts_source() -> str:
    if not TS_EVENTS.exists():
        pytest.skip(f"the console is not checked out at {TS_EVENTS}")
    return TS_EVENTS.read_text()


def _string_list(source: str, marker: str) -> list[str]:
    """Pull the quoted strings out of the declaration that follows ``marker``."""
    start = source.index(marker)
    end = source.index(";", start)
    return re.findall(r'"([^"]+)"', source[start:end])


def test_event_types_match():
    listed = _string_list(_ts_source(), "export const EVENT_TYPES")
    assert listed == list(EVENT_TYPES)


def test_every_event_class_is_in_event_types():
    """A new event class with no entry in EVENT_TYPES would never reach a client."""
    import bellwether.events as events

    declared = {
        cls.type
        for name in dir(events)
        if isinstance(cls := getattr(events, name), type)
        and hasattr(cls, "type")
        and cls.type != "event"
    }
    assert declared == set(EVENT_TYPES)


def test_warning_kinds_match():
    listed = _string_list(_ts_source(), "export type WarningKind")
    assert sorted(listed) == sorted(WarningKind.__args__)


def test_node_states_match():
    listed = _string_list(_ts_source(), "export type NodeState")
    from bellwether.events import NodeState

    assert sorted(listed) == sorted(NodeState.__args__)


def test_terminal_states_are_a_subset_of_the_declared_states():
    from bellwether.events import NodeState

    assert set(TERMINAL) <= set(NodeState.__args__)


def test_every_event_serialises_to_json():
    import json

    from bellwether.events import (
        Metric,
        NodeFailed,
        NodeResult,
        NodeState_,
        RunFinished,
        RunStarted,
        Token,
        Warning_,
    )

    for event in (
        RunStarted(run_id="r"),
        NodeState_(node="a"),
        Token(node="a"),
        NodeResult(node="a"),
        NodeFailed(node="a"),
        Warning_(node="a"),
        Metric(),
        RunFinished(run_id="r"),
    ):
        payload = json.loads(json.dumps(event.to_dict()))
        assert payload["type"] in EVENT_TYPES
        assert "seq" in payload and "at_ms" in payload
