/**
 * The delivered answer, with the critic's verdict attached rather than applied.
 *
 * A point the critic could not support stays visible and struck through, next
 * to the reason. Removing it would make the run look cleaner than it was, and
 * the whole reason the citation check exists is to be seen.
 */

import type { Report } from "../lib/events";
import { ms, pct, usd } from "../lib/format";

interface Props {
  report: Report | undefined;
  status: string;
}

export default function AnswerPanel({ report, status }: Props) {
  if (report === undefined) {
    return (
      <p className="empty">
        {status === "running" ? "Working. The answer is written last." : "No answer yet."}
      </p>
    );
  }
  if (report.error !== undefined) {
    return <p className="lane-error">{report.error}</p>;
  }

  const answer = report.answer ?? {};
  const grounding = report.grounding ?? {};
  const missing = report.missing_specialists ?? [];

  return (
    <div className="answer">
      <h2>{answer.headline ?? "No answer produced"}</h2>
      {report.degraded === true && (
        <p className="banner">
          Degraded run.
          {missing.length > 0 && ` ${missing.join(", ")} did not return.`}
          {(report.failed_nodes ?? []).length > 0 &&
            ` ${(report.failed_nodes ?? []).join(", ")} failed.`}
          {(grounding.unresolved ?? 0) > 0 &&
            ` ${grounding.unresolved} citation(s) did not resolve.`}
        </p>
      )}

      <p className="answer-summary">{answer.summary}</p>

      <ul className="points">
        {(answer.key_points ?? []).map((point, index) => (
          <li key={index} className={point.supported === false ? "is-rejected" : ""}>
            <span className="point-text">{point.point}</span>
            <span className="point-refs">
              {[...new Set(point.source_refs)].map((ref) => (
                <code key={ref}>{ref}</code>
              ))}
            </span>
            {point.rejected_because !== undefined && (
              <span className="point-reason">rejected: {point.rejected_because}</span>
            )}
          </li>
        ))}
      </ul>

      <dl className="answer-facts">
        <Fact label="citations" value={String(grounding.citations ?? 0)} />
        <Fact
          label="grounded"
          value={pct(grounding.grounding_rate)}
          tone={(grounding.grounding_rate ?? 1) < 1 ? "warn" : undefined}
        />
        <Fact label="revisions" value={String(report.revisions ?? 0)} />
        <Fact label="warnings" value={String(report.warnings ?? 0)} />
        <Fact label="calls" value={String(report.usage?.calls ?? 0)} />
        <Fact label="cost" value={usd(report.usage?.cost_usd)} />
        <Fact label="total" value={ms(report.timing?.total_ms)} />
      </dl>
    </div>
  );
}

function Fact({ label, value, tone }: { label: string; value: string; tone?: "warn" | undefined }) {
  return (
    <div className={`fact${tone !== undefined ? ` tone-${tone}` : ""}`}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
