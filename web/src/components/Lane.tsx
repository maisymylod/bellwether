/**
 * One agent's output as it arrives.
 *
 * Two views of the same thing: the structured object parsed from the prefix,
 * and the raw token stream underneath it. The raw view exists because the
 * structured one is a repair of incomplete input, and when it looks wrong the
 * first question is always what actually came over the wire.
 */

import { useEffect, useRef, useState } from "react";
import JsonView from "./JsonView";
import type { LaneState } from "../lib/reducer";
import { ms, nodeLabel, pct, tokens } from "../lib/format";

interface Props {
  lane: LaneState;
}

export default function Lane({ lane }: Props) {
  const [showRaw, setShowRaw] = useState(false);
  const rawRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    // Follow the tail while it streams, the way a log viewer does.
    const node = rawRef.current;
    if (node !== null && showRaw) node.scrollTop = node.scrollHeight;
  }, [lane.raw, showRaw]);

  const lowConfidence = lane.confidence !== undefined && lane.confidence < 0.45;

  return (
    <section className={`lane state-${lane.state}`} aria-label={lane.node}>
      <header className="lane-head">
        <h3>{nodeLabel(lane.node)}</h3>
        <span className={`pill pill-${lane.state}`}>{lane.state}</span>
        {lane.attempt > 1 && <span className="pill pill-attempt">attempt {lane.attempt}</span>}
        {lane.confidence !== undefined && (
          <span className={`pill ${lowConfidence ? "pill-warn" : "pill-quiet"}`}>
            confidence {pct(lane.confidence)}
          </span>
        )}
        <span className="lane-stats">
          {tokens(lane.tokens)} chunks
          {lane.durationMs !== undefined ? ` · ${ms(lane.durationMs)}` : ""}
          {lane.repairs > 0 ? ` · ${lane.repairs} repair${lane.repairs > 1 ? "s" : ""}` : ""}
          {lane.retries > 0 ? ` · ${lane.retries} retr${lane.retries > 1 ? "ies" : "y"}` : ""}
        </span>
        <button type="button" className="link" onClick={() => setShowRaw((v) => !v)}>
          {showRaw ? "hide raw" : "raw"}
        </button>
      </header>

      {lane.error !== undefined ? (
        <p className="lane-error">{lane.error}</p>
      ) : (
        <div className="lane-body">
          <JsonView value={lane.view} />
        </div>
      )}

      {showRaw && (
        <pre className="lane-raw" ref={rawRef}>
          {lane.raw === "" ? "(nothing yet)" : lane.raw}
        </pre>
      )}
    </section>
  );
}
