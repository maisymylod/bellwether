/**
 * The whole console is a fold over the event stream.
 *
 * Everything on screen is derived from `RunState`, and `RunState` is derived
 * from the events, in order, by a pure function. No component holds its own
 * copy of anything. That is what makes a resumed stream indistinguishable from
 * one that never dropped: replaying the buffered tail through the same reducer
 * reconstructs exactly the state the client would have had.
 *
 * `reduce` takes an *array* of events rather than one, because the stream
 * arrives far faster than a screen refreshes. See `stream.ts`.
 */

import type {
  BellwetherEvent,
  MetricEvent,
  NodeState,
  Report,
  WarningEvent,
} from "./events";
import { parsePartial, stableMerge } from "./partialJson";

export interface LaneState {
  node: string;
  agent: string;
  state: NodeState;
  attempt: number;
  /** Every token this node emitted, including from attempts that were discarded. */
  raw: string;
  /** Best-effort object parsed from `raw`, monotonically merged across frames. */
  view: unknown;
  tokens: number;
  confidence?: number;
  durationMs?: number;
  repairs: number;
  retries: number;
  error?: string;
}

export type RunStatus = "idle" | "running" | "ok" | "failed" | "cancelled";

export interface RunState {
  runId?: string;
  question: string;
  subject: string;
  provider: string;
  model: string;
  status: RunStatus;
  order: string[];
  edges: { from: string; to: string }[];
  lanes: Record<string, LaneState>;
  warnings: WarningEvent[];
  metric?: MetricEvent;
  report?: Report;
  lastSeq: number;
  /** True once a sequence number has been skipped, which should never happen. */
  gap: boolean;
}

export function initialState(): RunState {
  return {
    question: "",
    subject: "",
    provider: "",
    model: "",
    status: "idle",
    order: [],
    edges: [],
    lanes: {},
    warnings: [],
    lastSeq: 0,
    gap: false,
  };
}

function blankLane(node: string, agent: string): LaneState {
  return {
    node,
    agent,
    state: "queued",
    attempt: 1,
    raw: "",
    view: undefined,
    tokens: 0,
    repairs: 0,
    retries: 0,
  };
}

export function reduce(state: RunState, events: BellwetherEvent[]): RunState {
  if (events.length === 0) return state;

  const next: RunState = {
    ...state,
    lanes: { ...state.lanes },
    warnings: state.warnings,
  };
  // Lanes whose text changed this batch. Parsing is done once per lane per
  // frame rather than once per token, which is the difference between O(n) and
  // O(n^2) work over a run.
  const touched = new Set<string>();

  for (const event of events) {
    if (next.lastSeq !== 0 && event.seq > next.lastSeq + 1) next.gap = true;
    next.lastSeq = Math.max(next.lastSeq, event.seq);

    switch (event.type) {
      case "run.started": {
        next.runId = event.run_id;
        next.question = event.question;
        next.subject = event.subject;
        next.provider = event.provider;
        next.model = event.model;
        next.status = "running";
        next.order = event.nodes.map((n) => n.id);
        next.edges = event.edges;
        next.lanes = Object.fromEntries(
          event.nodes.map((n) => [n.id, blankLane(n.id, n.agent)]),
        );
        next.warnings = [];
        delete next.report;
        break;
      }

      case "node.state": {
        // A node can appear mid-run: the critic schedules a revision of the
        // synthesiser. Its edges arrive with it, so the graph stays laid out by
        // real dependency depth rather than dropping the newcomer at the root.
        const lane = next.lanes[event.node] ?? blankLane(event.node, event.node);
        if (!next.order.includes(event.node)) {
          next.order = [...next.order, event.node];
          const known = new Set(next.edges.map((e) => `${e.from}->${e.to}`));
          const added = (event.deps ?? [])
            .map((from) => ({ from, to: event.node }))
            .filter((edge) => !known.has(`${edge.from}->${edge.to}`));
          if (added.length > 0) next.edges = [...next.edges, ...added];
        }
        next.lanes[event.node] = { ...lane, state: event.state, attempt: event.attempt };
        break;
      }

      case "token": {
        const lane = next.lanes[event.node] ?? blankLane(event.node, event.node);
        next.lanes[event.node] = {
          ...lane,
          raw: lane.raw + event.text,
          tokens: lane.tokens + 1,
        };
        touched.add(event.node);
        break;
      }

      case "node.result": {
        const lane = next.lanes[event.node] ?? blankLane(event.node, event.node);
        next.lanes[event.node] = {
          ...lane,
          // The authoritative payload replaces whatever the partial parse had.
          view: event.payload,
          confidence: event.confidence,
          durationMs: event.duration_ms,
          repairs: event.repairs,
          retries: event.retries,
        };
        touched.delete(event.node);
        break;
      }

      case "node.failed": {
        const lane = next.lanes[event.node] ?? blankLane(event.node, event.node);
        next.lanes[event.node] = { ...lane, error: event.error, attempt: event.attempts };
        break;
      }

      case "warning": {
        next.warnings = [...next.warnings, event];
        break;
      }

      case "metric": {
        next.metric = event;
        break;
      }

      case "run.finished": {
        next.status = event.status;
        next.report = event.report;
        break;
      }
    }
  }

  for (const node of touched) {
    const lane = next.lanes[node];
    if (lane === undefined) continue;
    next.lanes[node] = {
      ...lane,
      view: stableMerge(lane.view, parsePartial(lane.raw).value),
    };
  }

  return next;
}

/** Node ids grouped into dependency layers, for laying the graph out top to bottom. */
export function layers(state: RunState): string[][] {
  const incoming = new Map<string, string[]>();
  for (const id of state.order) incoming.set(id, []);
  for (const edge of state.edges) {
    incoming.get(edge.to)?.push(edge.from);
  }

  const depth = new Map<string, number>();
  const resolve = (id: string, seen: Set<string>): number => {
    const cached = depth.get(id);
    if (cached !== undefined) return cached;
    if (seen.has(id)) return 0;
    seen.add(id);
    const parents = incoming.get(id) ?? [];
    const value = parents.length === 0 ? 0 : 1 + Math.max(...parents.map((p) => resolve(p, seen)));
    depth.set(id, value);
    return value;
  };

  const grouped = new Map<number, string[]>();
  for (const id of state.order) {
    const d = resolve(id, new Set());
    grouped.set(d, [...(grouped.get(d) ?? []), id]);
  }
  return [...grouped.entries()].sort((a, b) => a[0] - b[0]).map(([, ids]) => ids);
}
