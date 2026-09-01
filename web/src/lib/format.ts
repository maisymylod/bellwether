export const nf = new Intl.NumberFormat("en-US");

export function tokens(value: number | undefined): string {
  if (value === undefined) return "-";
  if (value < 1000) return String(value);
  return `${(value / 1000).toFixed(1)}k`;
}

export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return value < 1000 ? `${value}ms` : `${(value / 1000).toFixed(2)}s`;
}

export function usd(value: number | undefined): string {
  if (value === undefined) return "-";
  return `$${value.toFixed(4)}`;
}

export function pct(value: number | undefined): string {
  if (value === undefined) return "-";
  return `${Math.round(value * 100)}%`;
}

/** Short label for a node id, so `synthesis@r1` reads as `synthesis r1`. */
export function nodeLabel(id: string): string {
  return id.replace("@r", " r");
}
