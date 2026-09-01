/**
 * Renders the object a lane has produced so far.
 *
 * It has to cope with being handed something half-built - a key with no value
 * yet, an array element that is an empty object - because that is what a
 * partial parse of a stream looks like. Nothing here assumes a shape; the
 * schemas live in the engine and this is a structural renderer.
 */

interface Props {
  value: unknown;
  depth?: number;
}

const REF = /^[A-Z]{4}-\d{4}Q[1-4]#[A-Z]+(-\d+)?$/;

export default function JsonView({ value, depth = 0 }: Props) {
  if (value === undefined) return <span className="json-pending">waiting</span>;
  if (value === null) return <span className="json-null">null</span>;

  if (typeof value === "string") {
    if (REF.test(value)) return <span className="json-ref">{value}</span>;
    return <span className="json-string">{value}</span>;
  }
  if (typeof value === "number") return <span className="json-number">{value}</span>;
  if (typeof value === "boolean") return <span className="json-bool">{String(value)}</span>;

  if (Array.isArray(value)) {
    const items = value as unknown[];
    if (items.length === 0) return <span className="json-pending">[]</span>;
    return (
      <ol className="json-array">
        {items.map((item, index) => (
          <li key={index}>
            <JsonView value={item} depth={depth + 1} />
          </li>
        ))}
      </ol>
    );
  }

  const entries = Object.entries(value as Record<string, unknown>).filter(
    ([key]) => !key.startsWith("_"),
  );
  if (entries.length === 0) return <span className="json-pending">…</span>;

  return (
    <dl className="json-object" data-depth={depth}>
      {entries.map(([key, item]) => (
        <div className="json-row" key={key}>
          <dt>{key.replace(/_/g, " ")}</dt>
          <dd>
            <JsonView value={item} depth={depth + 1} />
          </dd>
        </div>
      ))}
    </dl>
  );
}
