import type { RunState } from "../lib/reducer";
import { ms, tokens, usd } from "../lib/format";

interface Props {
  state: RunState;
  connection: string | null;
}

export default function MetricsBar({ state, connection }: Props) {
  const metric = state.metric;
  return (
    <dl className="metrics">
      <Stat label="provider" value={state.provider || "-"} />
      <Stat label="model" value={state.model || "-"} />
      <Stat
        label="time to first token"
        value={ms(metric?.ttft_ms)}
        title="Measured from the moment the run was accepted, not from the first model call."
      />
      <Stat label="output tokens" value={tokens(metric?.output_tokens)} />
      <Stat label="cost" value={usd(metric?.cost_usd)} />
      <Stat
        label="nodes"
        value={
          metric === undefined
            ? "-"
            : `${metric.nodes_done}/${metric.nodes_total}${
                metric.nodes_running > 0 ? ` (${metric.nodes_running} running)` : ""
              }`
        }
      />
      {state.gap && <Stat label="stream" value="gap detected" tone="bad" />}
      {connection !== null && <Stat label="stream" value={connection} tone="warn" />}
    </dl>
  );
}

function Stat({
  label,
  value,
  tone,
  title,
}: {
  label: string;
  value: string;
  tone?: "warn" | "bad" | undefined;
  title?: string;
}) {
  return (
    <div className={`stat${tone !== undefined ? ` tone-${tone}` : ""}`} title={title}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
