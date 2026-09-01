from __future__ import annotations

import json

import httpx
import pytest

from bellwether.api.server import create_app
from bellwether.config import Settings

QUESTION = "How did Novaline Systems perform in 2025Q3 and what is the main risk?"


@pytest.fixture
async def client():
    app = create_app(Settings(provider="stub", stub_token_delay_s=0.0, seed=99))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as c:
        yield c


def _parse_sse(body: str) -> list[dict]:
    events = []
    for frame in body.split("\n\n"):
        for line in frame.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


async def test_health_reports_the_active_provider(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["provider"] == "stub"


async def test_corpus_lists_every_subject(client):
    body = (await client.get("/api/corpus")).json()
    tickers = {c["ticker"] for c in body["companies"]}
    assert "NVLN" in tickers
    assert all(c["periods"] for c in body["companies"])


async def test_a_question_with_no_subject_is_rejected_before_any_tokens(client):
    response = await client.post("/api/runs", json={"question": "how are markets"})
    assert response.status_code == 422
    assert "known tickers" in response.json()["detail"]


async def test_a_run_streams_to_completion(client):
    created = await client.post("/api/runs", json={"question": QUESTION})
    assert created.status_code == 201
    run_id = created.json()["run_id"]

    async with client.stream("GET", f"/api/runs/{run_id}/stream") as stream:
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert stream.headers["x-accel-buffering"] == "no"
        body = "".join([chunk async for chunk in stream.aiter_text()])

    events = _parse_sse(body)
    assert events[0]["type"] == "run.started"
    assert events[-1]["type"] == "run.finished"
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert any(e["type"] == "token" for e in events)
    assert events[-1]["report"]["answer"]["key_points"]


async def test_the_graph_is_declared_before_any_node_runs(client):
    """The console draws the whole graph from the first event."""
    run_id = (await client.post("/api/runs", json={"question": QUESTION})).json()["run_id"]
    async with client.stream("GET", f"/api/runs/{run_id}/stream") as stream:
        body = "".join([chunk async for chunk in stream.aiter_text()])
    started = _parse_sse(body)[0]
    assert {n["id"] for n in started["nodes"]} >= {
        "planner",
        "fundamentals",
        "risk",
        "tone",
        "peers",
        "synthesis",
        "critique",
    }
    assert started["edges"]


async def test_resuming_from_a_sequence_number_returns_only_the_tail(client):
    run_id = (await client.post("/api/runs", json={"question": QUESTION})).json()["run_id"]
    async with client.stream("GET", f"/api/runs/{run_id}/stream") as stream:
        full = _parse_sse("".join([chunk async for chunk in stream.aiter_text()]))

    resume_at = full[len(full) // 2]["seq"]
    async with client.stream(
        "GET", f"/api/runs/{run_id}/stream", headers={"Last-Event-ID": str(resume_at)}
    ) as stream:
        tail = _parse_sse("".join([chunk async for chunk in stream.aiter_text()]))

    assert [e["seq"] for e in tail] == [e["seq"] for e in full if e["seq"] > resume_at]


async def test_a_malformed_resume_header_is_treated_as_a_fresh_read(client):
    run_id = (await client.post("/api/runs", json={"question": QUESTION})).json()["run_id"]
    async with client.stream(
        "GET", f"/api/runs/{run_id}/stream", headers={"Last-Event-ID": "not-a-number"}
    ) as stream:
        events = _parse_sse("".join([chunk async for chunk in stream.aiter_text()]))
    assert events[0]["seq"] == 1


async def test_a_second_reader_sees_the_same_stream(client):
    run_id = (await client.post("/api/runs", json={"question": QUESTION})).json()["run_id"]
    async with client.stream("GET", f"/api/runs/{run_id}/stream") as first:
        a = _parse_sse("".join([chunk async for chunk in first.aiter_text()]))
    async with client.stream("GET", f"/api/runs/{run_id}/stream") as second:
        b = _parse_sse("".join([chunk async for chunk in second.aiter_text()]))
    assert [e["seq"] for e in a] == [e["seq"] for e in b]


async def test_the_report_is_readable_after_the_run_ends(client):
    run_id = (await client.post("/api/runs", json={"question": QUESTION})).json()["run_id"]
    async with client.stream("GET", f"/api/runs/{run_id}/stream") as stream:
        [chunk async for chunk in stream.aiter_text()]
    body = (await client.get(f"/api/runs/{run_id}")).json()
    assert body["finished"] is True
    assert body["report"]["answer"]["headline"]


async def test_cancelling_terminates_the_stream(client):
    run_id = (await client.post("/api/runs", json={"question": QUESTION})).json()["run_id"]
    assert (await client.post(f"/api/runs/{run_id}/cancel")).json()["cancelled"] is True
    async with client.stream("GET", f"/api/runs/{run_id}/stream") as stream:
        events = _parse_sse("".join([chunk async for chunk in stream.aiter_text()]))
    assert events[-1]["type"] == "run.finished"


async def test_unknown_run_is_a_404(client):
    for path in ("/api/runs/nope", "/api/runs/nope/stream"):
        assert (await client.get(path)).status_code == 404
    assert (await client.post("/api/runs/nope/cancel")).status_code == 404


async def test_a_too_short_question_is_rejected(client):
    assert (await client.post("/api/runs", json={"question": "x"})).status_code == 422
