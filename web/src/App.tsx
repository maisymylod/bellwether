import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import AnswerPanel from "./components/AnswerPanel";
import AskBar from "./components/AskBar";
import GraphView from "./components/GraphView";
import Lane from "./components/Lane";
import MetricsBar from "./components/MetricsBar";
import WarningsPanel from "./components/WarningsPanel";
import type { BellwetherEvent } from "./lib/events";
import { initialState, reduce } from "./lib/reducer";
import type { CorpusCompany, StreamHandle } from "./lib/stream";
import { cancelRun, fetchCorpus, openRunStream, startRun } from "./lib/stream";

type Tab = "lanes" | "warnings";

export default function App() {
  // The reducer takes a batch, which is what the stream client delivers: one
  // dispatch per animation frame rather than one per token.
  const [state, dispatch] = useReducer(reduce, undefined, initialState);
  const [companies, setCompanies] = useState<CorpusCompany[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [connection, setConnection] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("lanes");
  const streamRef = useRef<StreamHandle | null>(null);

  useEffect(() => {
    void fetchCorpus().then(setCompanies);
    return () => streamRef.current?.close();
  }, []);

  const busy = state.status === "running";

  const onAsk = useCallback(async (question: string) => {
    streamRef.current?.close();
    setError(null);
    setConnection(null);
    setSelected(null);
    setTab("lanes");
    try {
      const runId = await startRun(question);
      streamRef.current = openRunStream({
        runId,
        onBatch: (events: BellwetherEvent[]) => {
          setConnection(null);
          dispatch(events);
        },
        onError: setConnection,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  const onStop = useCallback(() => {
    if (state.runId !== undefined) void cancelRun(state.runId);
  }, [state.runId]);

  const lanes = useMemo(
    () =>
      state.order
        .map((id) => state.lanes[id])
        .filter((lane) => lane !== undefined)
        .filter((lane) => selected === null || lane.node === selected),
    [state.order, state.lanes, selected],
  );

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <strong>bellwether</strong>
          <span className="tagline">
            multi-agent filing analysis, streamed token by token
          </span>
        </div>
        {state.subject !== "" && <div className="subject">{state.subject}</div>}
      </header>

      <AskBar busy={busy} companies={companies} onAsk={(q) => void onAsk(q)} onStop={onStop} />
      {error !== null && <p className="banner banner-error">{error}</p>}

      <MetricsBar state={state} connection={connection} />

      <main className="grid">
        <aside className="panel panel-graph">
          <h2 className="panel-title">
            Graph
            {selected !== null && (
              <button type="button" className="link" onClick={() => setSelected(null)}>
                show all
              </button>
            )}
          </h2>
          <GraphView
            state={state}
            selected={selected}
            onSelect={(node) => setSelected((current) => (current === node ? null : node))}
          />
        </aside>

        <section className="panel panel-lanes">
          <h2 className="panel-title">
            <button
              type="button"
              className={`tab${tab === "lanes" ? " is-active" : ""}`}
              onClick={() => setTab("lanes")}
            >
              Live output
            </button>
            <button
              type="button"
              className={`tab${tab === "warnings" ? " is-active" : ""}`}
              onClick={() => setTab("warnings")}
            >
              Recovered from ({state.warnings.length})
            </button>
          </h2>
          {tab === "lanes" ? (
            lanes.length === 0 ? (
              <p className="empty">Ask something to start a run.</p>
            ) : (
              <div className="lanes">
                {lanes.map((lane) => (
                  <Lane key={lane.node} lane={lane} />
                ))}
              </div>
            )
          ) : (
            <WarningsPanel warnings={state.warnings} />
          )}
        </section>

        <section className="panel panel-answer">
          <h2 className="panel-title">Answer</h2>
          <AnswerPanel report={state.report} status={state.status} />
        </section>
      </main>
    </div>
  );
}
