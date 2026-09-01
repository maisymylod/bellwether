import { describe, expect, it } from "vitest";
import type { BellwetherEvent } from "./events";
import { initialState, layers, reduce } from "./reducer";

let seq = 0;
const at_ms = 0;
const ev = <T extends Partial<BellwetherEvent>>(e: T): BellwetherEvent =>
  ({ seq: ++seq, at_ms, ...e }) as BellwetherEvent;

const started = (): BellwetherEvent =>
  ev({
    type: "run.started",
    run_id: "r1",
    question: "q",
    subject: "Novaline Systems (NVLN) 2025Q3",
    provider: "stub",
    model: "claude-opus-5",
    nodes: [
      { id: "planner", agent: "planner" },
      { id: "fundamentals", agent: "fundamentals" },
      { id: "synthesis", agent: "synthesis" },
      { id: "critique", agent: "critique" },
    ],
    edges: [
      { from: "planner", to: "fundamentals" },
      { from: "fundamentals", to: "synthesis" },
      { from: "synthesis", to: "critique" },
    ],
  });

describe("reduce", () => {
  it("is a no-op on an empty batch", () => {
    const state = initialState();
    expect(reduce(state, [])).toBe(state);
  });

  it("builds every lane from the opening event, before anything runs", () => {
    seq = 0;
    const state = reduce(initialState(), [started()]);
    expect(state.status).toBe("running");
    expect(state.order).toEqual(["planner", "fundamentals", "synthesis", "critique"]);
    expect(Object.values(state.lanes).every((l) => l.state === "queued")).toBe(true);
    expect(state.subject).toContain("NVLN");
  });

  it("renders a partial object while tokens are still arriving", () => {
    seq = 0;
    const state = reduce(initialState(), [
      started(),
      ev({ type: "node.state", node: "fundamentals", state: "running", attempt: 1, deps: [] }),
      ev({ type: "token", node: "fundamentals", index: 0, text: '{"trend":"exp' }),
    ]);
    expect(state.lanes.fundamentals?.view).toEqual({ trend: "exp" });
  });

  it("only ever gains detail as tokens arrive", () => {
    seq = 0;
    let state = reduce(initialState(), [started()]);
    const chunks = ['{"metrics":[{"name":"revenue","value":88', "0.2", '}],"trend":"exp', 'anding"}'];
    let index = 0;
    for (const text of chunks) {
      state = reduce(state, [ev({ type: "token", node: "fundamentals", index: index++, text })]);
    }
    expect(state.lanes.fundamentals?.view).toEqual({
      metrics: [{ name: "revenue", value: 880.2 }],
      trend: "expanding",
    });
    expect(state.lanes.fundamentals?.tokens).toBe(4);
  });

  it("replaces the partial view with the authoritative payload on completion", () => {
    seq = 0;
    const state = reduce(initialState(), [
      started(),
      ev({ type: "token", node: "fundamentals", index: 0, text: '{"trend":"exp' }),
      ev({
        type: "node.result",
        node: "fundamentals",
        payload: { trend: "expanding", confidence: 0.8 },
        confidence: 0.8,
        input_tokens: 10,
        output_tokens: 20,
        duration_ms: 40,
        repairs: 1,
        retries: 0,
      }),
    ]);
    expect(state.lanes.fundamentals?.view).toEqual({ trend: "expanding", confidence: 0.8 });
    expect(state.lanes.fundamentals?.repairs).toBe(1);
  });

  it("grows a lane for a node that did not exist when the run started", () => {
    // The critic schedules a revision of the synthesiser at runtime.
    seq = 0;
    const state = reduce(initialState(), [
      started(),
      ev({ type: "node.state", node: "synthesis@r1", state: "running", attempt: 1, deps: ["critique"] }),
    ]);
    expect(state.order).toContain("synthesis@r1");
    expect(state.lanes["synthesis@r1"]?.state).toBe("running");
    // Its edge arrives with it, so it lands below the critic rather than at the root.
    expect(state.edges).toContainEqual({ from: "critique", to: "synthesis@r1" });
    expect(layers(state).at(-1)).toEqual(["synthesis@r1"]);
  });

  it("collects warnings in order without dropping any", () => {
    seq = 0;
    const state = reduce(initialState(), [
      started(),
      ev({ type: "warning", node: "risk", kind: "retry", detail: "overloaded" }),
      ev({ type: "warning", node: "synthesis", kind: "contradiction", detail: "bad ref" }),
    ]);
    expect(state.warnings.map((w) => w.kind)).toEqual(["retry", "contradiction"]);
  });

  it("records a failure without losing the lane", () => {
    seq = 0;
    const state = reduce(initialState(), [
      started(),
      ev({ type: "node.failed", node: "risk", error: "RuntimeError: nope", attempts: 3 }),
    ]);
    expect(state.lanes.risk?.error).toContain("RuntimeError");
  });

  it("carries the final status and report", () => {
    seq = 0;
    const state = reduce(initialState(), [
      started(),
      ev({
        type: "run.finished",
        run_id: "r1",
        status: "cancelled",
        duration_ms: 12,
        report: { degraded: true },
        warnings: 0,
      }),
    ]);
    expect(state.status).toBe("cancelled");
    expect(state.report?.degraded).toBe(true);
  });

  it("flags a gap in the sequence rather than silently rendering a hole", () => {
    seq = 0;
    let state = reduce(initialState(), [started()]);
    expect(state.gap).toBe(false);
    seq = 40;
    state = reduce(state, [ev({ type: "token", node: "planner", index: 0, text: "{" })]);
    expect(state.gap).toBe(true);
  });

  it("produces the same state whether events arrive in one batch or many", () => {
    // This is what makes a resumed stream safe: replay is indistinguishable
    // from the original delivery.
    seq = 0;
    const events = [
      started(),
      ev({ type: "node.state", node: "planner", state: "running", attempt: 1, deps: [] }),
      ev({ type: "token", node: "planner", index: 0, text: '{"scope":"a' }),
      ev({ type: "token", node: "planner", index: 1, text: 'll of it"}' }),
      ev({ type: "node.state", node: "planner", state: "done", attempt: 1, deps: [] }),
    ];
    const atOnce = reduce(initialState(), events);
    const oneByOne = events.reduce((state, e) => reduce(state, [e]), initialState());
    expect(oneByOne).toEqual(atOnce);
  });

  it("does not mutate the state it was given", () => {
    seq = 0;
    const before = reduce(initialState(), [started()]);
    const snapshot = JSON.stringify(before);
    reduce(before, [ev({ type: "token", node: "planner", index: 0, text: "{" })]);
    expect(JSON.stringify(before)).toBe(snapshot);
  });
});

describe("layers", () => {
  it("groups nodes into dependency depth", () => {
    seq = 0;
    const state = reduce(initialState(), [started()]);
    expect(layers(state)).toEqual([["planner"], ["fundamentals"], ["synthesis"], ["critique"]]);
  });

  it("puts independent nodes side by side", () => {
    seq = 0;
    const state = reduce(initialState(), [
      ev({
        type: "run.started",
        run_id: "r",
        question: "q",
        subject: "s",
        provider: "stub",
        model: "m",
        nodes: [
          { id: "root", agent: "root" },
          { id: "a", agent: "a" },
          { id: "b", agent: "b" },
        ],
        edges: [
          { from: "root", to: "a" },
          { from: "root", to: "b" },
        ],
      }),
    ]);
    expect(layers(state)).toEqual([["root"], ["a", "b"]]);
  });
});
