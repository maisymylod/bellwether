/**
 * The wire protocol, mirrored from `engine/bellwether/events.py`.
 *
 * These are hand-written rather than generated because there are eight of them
 * and a code generator would be more machinery than the problem deserves. What
 * keeps them honest is `engine/tests/test_events_contract.py`, which reads this
 * file and fails if the two sides ever list different event types.
 */

export type NodeState = "queued" | "running" | "done" | "failed" | "skipped";

export type WarningKind =
  | "schema_repair"
  | "retry"
  | "contradiction"
  | "low_confidence"
  | "budget_exhausted"
  | "truncated";

interface Base {
  seq: number;
  at_ms: number;
}

export interface RunStarted extends Base {
  type: "run.started";
  run_id: string;
  question: string;
  subject: string;
  provider: string;
  model: string;
  nodes: { id: string; agent: string }[];
  edges: { from: string; to: string }[];
}

export interface NodeStateEvent extends Base {
  type: "node.state";
  node: string;
  state: NodeState;
  attempt: number;
  /** Present so a node scheduled mid-run can be placed in the graph. */
  deps: string[];
}

export interface TokenEvent extends Base {
  type: "token";
  node: string;
  index: number;
  text: string;
}

export interface NodeResultEvent extends Base {
  type: "node.result";
  node: string;
  payload: Record<string, unknown>;
  confidence: number;
  input_tokens: number;
  output_tokens: number;
  duration_ms: number;
  repairs: number;
  retries: number;
}

export interface NodeFailedEvent extends Base {
  type: "node.failed";
  node: string;
  error: string;
  attempts: number;
}

export interface WarningEvent extends Base {
  type: "warning";
  node: string;
  kind: WarningKind;
  detail: string;
}

export interface MetricEvent extends Base {
  type: "metric";
  ttft_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  nodes_running: number;
  nodes_done: number;
  nodes_total: number;
}

export interface RunFinishedEvent extends Base {
  type: "run.finished";
  run_id: string;
  status: "ok" | "failed" | "cancelled";
  duration_ms: number;
  report: Report;
  warnings: number;
}

export type BellwetherEvent =
  | RunStarted
  | NodeStateEvent
  | TokenEvent
  | NodeResultEvent
  | NodeFailedEvent
  | WarningEvent
  | MetricEvent
  | RunFinishedEvent;

/** Kept in the same order as `EVENT_TYPES` in events.py. The contract test reads this. */
export const EVENT_TYPES = [
  "run.started",
  "node.state",
  "token",
  "node.result",
  "node.failed",
  "warning",
  "metric",
  "run.finished",
] as const;

export interface KeyPoint {
  point: string;
  source_refs: string[];
  supported?: boolean;
  rejected_because?: string;
}

export interface Answer {
  headline?: string;
  summary?: string;
  key_points?: KeyPoint[];
  confidence?: number;
  missing_specialists?: string[];
  revision?: number;
}

export interface Report {
  answer?: Answer;
  critique?: { verdict?: string; unsupported?: { point: string; reason: string }[] };
  grounding?: { citations?: number; unresolved?: number; grounding_rate?: number };
  subject?: string;
  period?: string;
  revisions?: number;
  failed_nodes?: string[];
  missing_specialists?: string[];
  degraded?: boolean;
  warnings?: number;
  usage?: { input_tokens: number; output_tokens: number; calls: number; cost_usd: number };
  timing?: { ttft_ms: number | null; total_ms: number };
  nodes?: Record<string, NodeState>;
  error?: string;
}

export function isBellwetherEvent(value: unknown): value is BellwetherEvent {
  if (typeof value !== "object" || value === null) return false;
  const type = (value as { type?: unknown }).type;
  return (
    typeof type === "string" && (EVENT_TYPES as readonly string[]).includes(type)
  );
}
