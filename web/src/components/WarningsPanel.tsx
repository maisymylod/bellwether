/**
 * Everything the run had to recover from, in order.
 *
 * This is the panel that justifies the machinery. A run with three retries, a
 * schema repair and a rejected citation still produced an answer, and being
 * able to see that is the difference between trusting the output and hoping.
 */

import type { WarningEvent, WarningKind } from "../lib/events";
import { nodeLabel } from "../lib/format";

const EXPLAIN: Record<WarningKind, string> = {
  retry: "the call failed and was reissued",
  schema_repair: "the response broke the schema and was sent back for correction",
  contradiction: "a claim cited evidence that does not resolve",
  low_confidence: "the agent reported low confidence in its own answer",
  budget_exhausted: "the run hit its token or cost ceiling",
  truncated: "the response was cut off at the token limit",
};

export default function WarningsPanel({ warnings }: { warnings: WarningEvent[] }) {
  if (warnings.length === 0) {
    return <p className="empty">Nothing had to be recovered from.</p>;
  }
  return (
    <ul className="warnings">
      {warnings.map((warning) => (
        <li key={warning.seq} className={`warning kind-${warning.kind}`}>
          <span className="warning-kind" title={EXPLAIN[warning.kind]}>
            {warning.kind.replace(/_/g, " ")}
          </span>
          <span className="warning-node">{nodeLabel(warning.node)}</span>
          <span className="warning-detail">{warning.detail}</span>
        </li>
      ))}
    </ul>
  );
}
