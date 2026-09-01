/**
 * The SSE client, and the reason the console does not melt.
 *
 * A run emits on the order of a thousand events, most of them single tokens,
 * and they arrive in bursts. Dispatching each one into React state means a
 * render per token: the browser spends its whole frame budget on reconciliation
 * and the UI goes to treacle exactly when there is most to look at.
 *
 * So events are buffered and flushed once per animation frame. The screen
 * cannot show more than one update per frame anyway, so nothing is lost, and
 * the render count drops from "per token" to "per frame" - bounded by the
 * display, not by the model. Everything downstream takes an array of events
 * for this reason.
 *
 * Reconnects are the browser's `EventSource` doing its own job: every frame
 * carries `id: <seq>`, so on a dropped connection it retries with
 * `Last-Event-ID` and the server replays the tail. The one thing that has to be
 * done here is closing the connection when the run ends, because otherwise
 * `EventSource` treats the server's clean close as a failure and reconnects to
 * a finished run forever.
 */

import type { BellwetherEvent } from "./events";
import { isBellwetherEvent } from "./events";

export interface StreamHandle {
  close: () => void;
}

export interface StreamOptions {
  runId: string;
  onBatch: (events: BellwetherEvent[]) => void;
  onError?: (message: string) => void;
  /** Injected in tests. Defaults to the browser's EventSource. */
  createSource?: (url: string) => EventSourceLike;
  /** Injected in tests. Defaults to requestAnimationFrame. */
  schedule?: (callback: () => void) => void;
}

export interface EventSourceLike {
  addEventListener: (type: string, listener: (event: MessageEvent) => void) => void;
  close: () => void;
  onerror: ((event: unknown) => void) | null;
}

const defaultSchedule = (callback: () => void): void => {
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(callback);
  else setTimeout(callback, 16);
};

export function openRunStream(options: StreamOptions): StreamHandle {
  const { runId, onBatch, onError } = options;
  const createSource = options.createSource ?? ((url) => new EventSource(url));
  const schedule = options.schedule ?? defaultSchedule;

  const source = createSource(`/api/runs/${encodeURIComponent(runId)}/stream`);
  let buffer: BellwetherEvent[] = [];
  let scheduled = false;
  let closed = false;

  const flush = (): void => {
    scheduled = false;
    if (buffer.length === 0) return;
    const batch = buffer;
    buffer = [];
    onBatch(batch);
  };

  const push = (event: BellwetherEvent): void => {
    buffer.push(event);
    if (!scheduled) {
      scheduled = true;
      schedule(flush);
    }
  };

  const close = (): void => {
    if (closed) return;
    closed = true;
    source.close();
    // Anything buffered when the run ended still belongs on screen.
    flush();
  };

  const handle = (raw: MessageEvent): void => {
    if (closed) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw.data as string);
    } catch {
      onError?.("received a frame that was not JSON");
      return;
    }
    if (!isBellwetherEvent(parsed)) {
      // A type this client does not know about is not an error: the engine may
      // be newer. Ignore it rather than tearing the stream down.
      return;
    }
    push(parsed);
    if (parsed.type === "run.finished") close();
  };

  // Named SSE events, so a listener per type rather than one onmessage.
  for (const type of [
    "run.started",
    "node.state",
    "token",
    "node.result",
    "node.failed",
    "warning",
    "metric",
    "run.finished",
  ]) {
    source.addEventListener(type, handle);
  }

  source.onerror = (): void => {
    if (closed) return;
    // EventSource retries on its own with Last-Event-ID. Surface it so the UI
    // can say "reconnecting" rather than looking frozen.
    onError?.("connection interrupted, reconnecting");
  };

  return { close };
}

export async function startRun(question: string): Promise<string> {
  const response = await fetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : `run failed (${response.status})`);
  }
  const body = (await response.json()) as { run_id: string };
  return body.run_id;
}

export async function cancelRun(runId: string): Promise<void> {
  await fetch(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
}

export interface CorpusCompany {
  ticker: string;
  name: string;
  sector: string;
  peers: string[];
  periods: string[];
}

export async function fetchCorpus(): Promise<CorpusCompany[]> {
  const response = await fetch("/api/corpus");
  if (!response.ok) return [];
  const body = (await response.json()) as { companies: CorpusCompany[] };
  return body.companies;
}
