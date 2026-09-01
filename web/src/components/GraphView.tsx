/**
 * The agent graph, laid out by dependency depth.
 *
 * It is drawn from the `run.started` event before any node runs, so the shape
 * of the work is visible from the first frame instead of assembling itself as
 * results land. Nodes added mid-run (a revision the critic asked for) appear as
 * a new layer.
 */

import type { LaneState, RunState } from "../lib/reducer";
import { layers } from "../lib/reducer";
import { ms, nodeLabel } from "../lib/format";

interface Props {
  state: RunState;
  selected: string | null;
  onSelect: (node: string) => void;
}

const GLYPH: Record<LaneState["state"], string> = {
  queued: "○",
  running: "◐",
  done: "●",
  failed: "✕",
  skipped: "–",
};

export default function GraphView({ state, selected, onSelect }: Props) {
  const rows = layers(state);
  if (rows.length === 0) {
    return <p className="empty">The graph appears here when a run starts.</p>;
  }

  return (
    <div className="graph">
      {rows.map((row, index) => (
        <div className="graph-layer" key={index}>
          {index > 0 && <div className="graph-rail" aria-hidden="true" />}
          <div className="graph-row">
            {row.map((id) => {
              const lane = state.lanes[id];
              if (lane === undefined) return null;
              return (
                <button
                  type="button"
                  key={id}
                  className={`graph-node state-${lane.state}${selected === id ? " is-selected" : ""}`}
                  onClick={() => onSelect(id)}
                  aria-pressed={selected === id}
                >
                  <span className="graph-glyph" aria-hidden="true">
                    {GLYPH[lane.state]}
                  </span>
                  <span className="graph-name">{nodeLabel(id)}</span>
                  <span className="graph-meta">
                    {lane.state === "running" && lane.tokens > 0
                      ? `${lane.tokens} chunks`
                      : lane.durationMs !== undefined
                        ? ms(lane.durationMs)
                        : lane.state}
                  </span>
                  {(lane.repairs > 0 || lane.retries > 0) && (
                    <span className="graph-flags">
                      {lane.repairs > 0 && <em title="schema repairs">{lane.repairs}R</em>}
                      {lane.retries > 0 && <em title="retries">{lane.retries}↻</em>}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
