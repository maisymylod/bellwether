"""HTTP surface: start a run, watch it, stop it.

The interesting endpoint is the stream. A few things it has to get right that a
naive SSE handler does not:

* **Resume.** Every frame carries ``id: <seq>``. The browser sends the last one
  back as ``Last-Event-ID`` automatically on reconnect, and the run's buffer is
  replayed from there. A dropped Wi-Fi connection costs nothing.
* **Late subscribers.** Subscribing to a run already in flight backfills the
  whole log first, so the console renders identically whether it attached at
  the start or halfway through.
* **Disconnects.** A client that goes away has its queue removed. Runs are not
  cancelled by disconnect - the work keeps going and the client can come back -
  but nothing accumulates for a reader that will never read.
* **Heartbeats.** A comment frame every few seconds, because an idle SSE
  connection is indistinguishable from a hung one to every proxy in between.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from bellwether.agents.subject import SubjectNotFound, parse_subject
from bellwether.config import Settings
from bellwether.corpus import load_corpus
from bellwether.events import to_sse
from bellwether.providers import build_provider
from bellwether.run import Run, build_report, start_run

HEARTBEAT_S = 5.0
MAX_RETAINED_RUNS = 32

# The console is served from Vite in development and from the same origin in
# production, so only the dev origins need to be named here.
DEV_ORIGINS = ("http://localhost:5273", "http://127.0.0.1:5273")


class RunRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    corpus = load_corpus()
    runs: OrderedDict[str, Run] = OrderedDict()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        # Cancel in-flight runs on shutdown so live model calls are not orphaned.
        for run in runs.values():
            run.cancel()
            if run.task is not None:
                with contextlib.suppress(TimeoutError, asyncio.CancelledError, Exception):
                    await asyncio.wait_for(run.task, timeout=2.0)

    app = FastAPI(title="bellwether", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(DEV_ORIGINS),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    def remember(run: Run) -> None:
        runs[run.run_id] = run
        while len(runs) > MAX_RETAINED_RUNS:
            _, evicted = runs.popitem(last=False)
            evicted.cancel()

    def get_run(run_id: str) -> Run:
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
        return run

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "provider": settings.provider,
            "model": settings.model,
            "active_runs": sum(
                1 for r in runs.values() if r.task is not None and not r.task.done()
            ),
        }

    @app.get("/api/corpus")
    async def get_corpus() -> dict[str, Any]:
        """What the console offers as subjects. The corpus is the whole world here."""
        return {
            "companies": [
                {
                    "ticker": t,
                    "name": corpus.company(t)["name"],
                    "sector": corpus.company(t)["sector"],
                    "peers": corpus.company(t)["peers"],
                    "periods": corpus.periods(t),
                }
                for t in corpus.tickers()
            ]
        }

    @app.post("/api/runs", status_code=201)
    async def create_run(body: RunRequest) -> dict[str, Any]:
        try:
            parse_subject(body.question, corpus)
        except SubjectNotFound as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        run = start_run(
            question=body.question,
            provider=build_provider(settings),
            settings=settings,
            corpus=corpus,
        )
        remember(run)
        return {"run_id": run.run_id, "stream": f"/api/runs/{run.run_id}/stream"}

    @app.get("/api/runs/{run_id}")
    async def read_run(run_id: str) -> dict[str, Any]:
        run = get_run(run_id)
        finished = run.task is not None and run.task.done()
        return {
            "run_id": run_id,
            "question": run.question,
            "finished": finished,
            "seq": run.board.seq,
            "nodes": dict(run.board.node_states),
            "report": build_report(run.board, run.scheduler) if finished else None,
        }

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict[str, Any]:
        run = get_run(run_id)
        run.cancel()
        return {"run_id": run_id, "cancelled": True}

    @app.get("/api/runs/{run_id}/stream")
    async def stream_run(run_id: str, request: Request) -> Response:
        run = get_run(run_id)
        after = _last_event_id(request)
        if run.board.missed(after):
            # Be explicit rather than silently serving a stream with a hole in it.
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "the requested resume point has been evicted from the "
                    "run buffer; refetch the run instead of resuming",
                    "oldest_seq": run.board.snapshot()[0].seq,
                },
            )
        queue = run.board.subscribe(after_seq=after)

        async def frames() -> Any:
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                    except TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield ": keepalive\n\n"
                        continue
                    if event is None:
                        return
                    yield to_sse(event)
            finally:
                run.board.unsubscribe(queue)

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                # Nginx buffers text/event-stream by default, which turns a live
                # stream into one large response at the end.
                "X-Accel-Buffering": "no",
            },
        )

    return app


def _last_event_id(request: Request) -> int:
    raw = request.headers.get("last-event-id") or request.query_params.get("after")
    try:
        return max(0, int(raw)) if raw else 0
    except ValueError:
        return 0


app = create_app()
