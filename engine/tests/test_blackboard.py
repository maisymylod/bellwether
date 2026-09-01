from __future__ import annotations

import asyncio

from bellwether.events import Token
from bellwether.runtime.blackboard import Blackboard


def _board() -> Blackboard:
    return Blackboard(run_id="r", question="q", subject="X", period="2025Q3")


def _drain(queue: asyncio.Queue) -> list:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


def test_seq_is_monotonic_from_one():
    board = _board()
    seqs = [board.emit(Token(node="a", text=str(i))).seq for i in range(5)]
    assert seqs == [1, 2, 3, 4, 5]


def test_late_subscriber_is_backfilled():
    board = _board()
    for i in range(3):
        board.emit(Token(node="a", text=str(i)))
    queue = board.subscribe()
    assert [e.seq for e in _drain(queue)] == [1, 2, 3]


def test_resume_delivers_only_the_tail():
    board = _board()
    for i in range(5):
        board.emit(Token(node="a", text=str(i)))
    queue = board.subscribe(after_seq=3)
    assert [e.seq for e in _drain(queue)] == [4, 5]


def test_backfill_and_live_delivery_do_not_overlap():
    """A subscriber must see each event exactly once across the seam."""
    board = _board()
    board.emit(Token(node="a", text="0"))
    queue = board.subscribe()
    board.emit(Token(node="a", text="1"))
    seqs = [e.seq for e in _drain(queue)]
    assert seqs == [1, 2]
    assert len(seqs) == len(set(seqs))


def test_two_subscribers_each_get_everything():
    board = _board()
    first = board.subscribe()
    second = board.subscribe()
    board.emit(Token(node="a", text="x"))
    assert len(_drain(first)) == 1
    assert len(_drain(second)) == 1


def test_close_terminates_open_streams():
    board = _board()
    queue = board.subscribe()
    board.close()
    assert _drain(queue) == [None]


def test_subscribing_after_close_terminates_immediately():
    board = _board()
    board.emit(Token(node="a", text="x"))
    board.close()
    queue = board.subscribe()
    assert _drain(queue) == [board.snapshot()[0], None]


def test_unsubscribe_stops_delivery():
    board = _board()
    queue = board.subscribe()
    board.unsubscribe(queue)
    board.emit(Token(node="a", text="x"))
    assert _drain(queue) == []


def test_eviction_is_detectable_rather_than_silent():
    board = _board()
    board._log = type(board._log)(maxlen=3)
    for i in range(6):
        board.emit(Token(node="a", text=str(i)))
    assert board.dropped == 3
    assert board.missed(after_seq=1) is True  # seq 2 and 3 are gone
    assert board.missed(after_seq=4) is False  # seq 5 and 6 are still buffered
