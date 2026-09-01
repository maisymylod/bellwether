import { describe, expect, it, vi } from "vitest";
import type { BellwetherEvent } from "./events";
import type { EventSourceLike } from "./stream";
import { openRunStream } from "./stream";

class FakeSource implements EventSourceLike {
  listeners = new Map<string, (event: MessageEvent) => void>();
  onerror: ((event: unknown) => void) | null = null;
  closed = false;

  addEventListener(type: string, listener: (event: MessageEvent) => void): void {
    this.listeners.set(type, listener);
  }

  close(): void {
    this.closed = true;
  }

  send(payload: unknown, type?: string): void {
    const event = payload as { type?: string };
    const listener = this.listeners.get(type ?? event.type ?? "");
    listener?.({ data: JSON.stringify(payload) } as unknown as MessageEvent);
  }

  sendRaw(data: string, type: string): void {
    this.listeners.get(type)?.({ data } as MessageEvent);
  }
}

function harness() {
  const source = new FakeSource();
  const batches: BellwetherEvent[][] = [];
  const errors: string[] = [];
  const pending: (() => void)[] = [];
  const handle = openRunStream({
    runId: "r1",
    onBatch: (events) => batches.push(events),
    onError: (message) => errors.push(message),
    createSource: () => source,
    schedule: (callback) => pending.push(callback),
  });
  const frame = (): void => {
    const callbacks = pending.splice(0);
    for (const callback of callbacks) callback();
  };
  return { source, batches, errors, handle, frame };
}

const token = (seq: number, text: string): BellwetherEvent => ({
  type: "token",
  seq,
  at_ms: 0,
  node: "planner",
  index: seq,
  text,
});

describe("openRunStream", () => {
  it("coalesces a burst of events into one batch per frame", () => {
    // The point of the whole module: 200 tokens must not become 200 renders.
    const { source, batches, frame } = harness();
    for (let i = 1; i <= 200; i++) source.send(token(i, "x"));
    expect(batches).toHaveLength(0);
    frame();
    expect(batches).toHaveLength(1);
    expect(batches[0]).toHaveLength(200);
  });

  it("starts a new batch after each frame", () => {
    const { source, batches, frame } = harness();
    source.send(token(1, "a"));
    frame();
    source.send(token(2, "b"));
    frame();
    expect(batches.map((b) => b.length)).toEqual([1, 1]);
  });

  it("preserves event order across a batch", () => {
    const { source, batches, frame } = harness();
    for (const text of ["a", "b", "c"]) source.send(token(batches.length + 1, text));
    source.send(token(2, "b"));
    source.send(token(3, "c"));
    frame();
    const texts = batches[0]?.map((e) => (e as { text: string }).text);
    expect(texts).toEqual(["a", "b", "c", "b", "c"]);
  });

  it("closes the connection when the run finishes", () => {
    // Otherwise EventSource reads the server's clean close as a failure and
    // reconnects to a finished run indefinitely.
    const { source, batches, frame } = harness();
    source.send({
      type: "run.finished",
      seq: 1,
      at_ms: 0,
      run_id: "r1",
      status: "ok",
      duration_ms: 5,
      report: {},
      warnings: 0,
    });
    expect(source.closed).toBe(true);
    frame();
    expect(batches[0]?.[0]?.type).toBe("run.finished");
  });

  it("flushes what is buffered when the run ends, without waiting for a frame", () => {
    const { source, batches } = harness();
    source.send(token(1, "a"));
    source.send({
      type: "run.finished",
      seq: 2,
      at_ms: 0,
      run_id: "r1",
      status: "ok",
      duration_ms: 5,
      report: {},
      warnings: 0,
    });
    expect(batches[0]).toHaveLength(2);
  });

  it("reports a malformed frame without tearing down the stream", () => {
    const { source, errors, batches, frame } = harness();
    source.sendRaw("not json", "token");
    source.send(token(1, "a"));
    frame();
    expect(errors).toEqual(["received a frame that was not JSON"]);
    expect(batches[0]).toHaveLength(1);
  });

  it("ignores an event type it does not know, rather than failing", () => {
    // Forward compatibility: a newer engine may emit types this build predates.
    const { source, batches, errors, frame } = harness();
    source.send({ type: "future.event", seq: 1, at_ms: 0 }, "token");
    frame();
    expect(batches).toHaveLength(0);
    expect(errors).toHaveLength(0);
  });

  it("surfaces a dropped connection so the UI can say so", () => {
    const { source, errors } = harness();
    source.onerror?.({});
    expect(errors).toEqual(["connection interrupted, reconnecting"]);
  });

  it("stops delivering after close", () => {
    const { source, batches, handle, frame } = harness();
    handle.close();
    source.send(token(1, "a"));
    frame();
    expect(batches).toHaveLength(0);
    expect(source.closed).toBe(true);
  });

  it("does not error after the run has already finished", () => {
    const { source, errors } = harness();
    source.send({
      type: "run.finished",
      seq: 1,
      at_ms: 0,
      run_id: "r1",
      status: "ok",
      duration_ms: 1,
      report: {},
      warnings: 0,
    });
    source.onerror?.({});
    expect(errors).toHaveLength(0);
  });

  it("uses the default scheduler when none is injected", async () => {
    const source = new FakeSource();
    const batches: BellwetherEvent[][] = [];
    openRunStream({
      runId: "r1",
      onBatch: (events) => batches.push(events),
      createSource: () => source,
    });
    source.send(token(1, "a"));
    await vi.waitFor(() => expect(batches).toHaveLength(1));
  });
});
