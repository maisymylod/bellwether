/**
 * Read a JSON object that has not finished arriving.
 *
 * The engine constrains every agent's output to a JSON schema, so what streams
 * over the wire is JSON, one token at a time. Waiting for the closing brace
 * before rendering anything would throw away the whole point of streaming: on a
 * multi-second generation the user stares at a spinner and then gets a wall of
 * text.
 *
 * So the console parses the prefix. On every frame the accumulated text is
 * repaired into the smallest valid JSON document that could complete it -
 * unterminated strings closed, dangling commas and half-written keys dropped,
 * open containers closed - and rendered. Fields appear as they land, and the
 * one currently being written shows its partial value.
 *
 * The repair is deliberately conservative. It never guesses at a value: an
 * incomplete number or literal is dropped rather than rounded to whatever has
 * arrived, because rendering `1.` as `1` and then correcting it to `1.87` a
 * frame later is worse than rendering nothing for one frame.
 */

export interface PartialParse<T = unknown> {
  /** Best-effort value for the prefix, or undefined if not enough has arrived. */
  value: T | undefined;
  /** True when the input was already valid JSON with nothing left open. */
  complete: boolean;
}

const OPENERS = new Set(["{", "["]);
const CLOSERS: Record<string, string> = { "{": "}", "[": "]" };

interface Scan {
  stack: string[];
  inString: boolean;
  escaped: boolean;
}

function scan(text: string): Scan {
  const stack: string[] = [];
  let inString = false;
  let escaped = false;

  for (let i = 0; i < text.length; i++) {
    const c = text[i] as string;
    if (inString) {
      if (escaped) escaped = false;
      else if (c === "\\") escaped = true;
      else if (c === '"') inString = false;
      continue;
    }
    if (c === '"') inString = true;
    else if (OPENERS.has(c)) stack.push(c);
    else if (c === "}" || c === "]") stack.pop();
  }
  return { stack, inString, escaped };
}

/** Drop a trailing token that is not yet a valid JSON scalar (`1.`, `tru`, `-`). */
function trimIncompleteScalar(text: string): string {
  const match = /[^\s,:[{]+$/.exec(text);
  if (!match) return text;
  const token = match[0];
  // A closing brace or bracket is a structural character, not a scalar.
  if (token.endsWith("}") || token.endsWith("]") || token.endsWith('"')) return text;
  try {
    JSON.parse(token);
    return text;
  } catch {
    return text.slice(0, match.index);
  }
}

/**
 * Index of the quote that opens the complete string ending at `end`.
 * `text[end - 1]` is that string's closing quote, so the search starts before it.
 */
function stringStart(text: string, end: number): number {
  let i = end - 2;
  while (i >= 0) {
    if (text[i] === '"') {
      let backslashes = 0;
      let j = i - 1;
      while (j >= 0 && text[j] === "\\") {
        backslashes++;
        j--;
      }
      if (backslashes % 2 === 0) return i;
    }
    i--;
  }
  return -1;
}

/**
 * Remove anything at the tail that cannot be followed by a closing bracket:
 * a trailing comma, a `key:` with no value yet, or a bare key with no colon.
 */
function dropDangling(text: string, stack: string[]): string {
  let out = text;
  for (;;) {
    const before = out;
    out = out.replace(/\s+$/, "");

    if (out.endsWith(",")) {
      out = out.slice(0, -1);
    } else if (out.endsWith(":")) {
      out = out.slice(0, -1).replace(/\s+$/, "");
      const start = stringStart(out, out.length);
      if (start === -1) return out;
      out = out.slice(0, start);
    } else if (out.endsWith('"') && stack[stack.length - 1] === "{") {
      // A complete string at the tail of an object is only a value if a colon
      // precedes it. Otherwise it is a key whose value has not arrived.
      const start = stringStart(out, out.length);
      if (start === -1) return out;
      const preceding = out.slice(0, start).replace(/\s+$/, "");
      if (preceding.endsWith(":")) return out;
      out = out.slice(0, start);
    }

    if (out === before) return out;
  }
}

/** Turn a prefix of a JSON document into the smallest valid document. */
export function repair(text: string): string | undefined {
  const trimmed = text.trimStart();
  if (trimmed === "") return undefined;

  const { stack, inString, escaped } = scan(trimmed);
  let out = trimmed;

  if (inString) {
    if (escaped) out = out.slice(0, -1);
    // A `\uXXXX` escape that is still arriving cannot be closed, only dropped.
    const partialUnicode = /\\u[0-9a-fA-F]{0,3}$/.exec(out);
    if (partialUnicode) out = out.slice(0, partialUnicode.index);
    out += '"';
  } else {
    out = trimIncompleteScalar(out);
  }

  out = dropDangling(out, stack);
  for (let i = stack.length - 1; i >= 0; i--) {
    out += CLOSERS[stack[i] as string];
  }
  return out;
}

export function parsePartial<T = unknown>(text: string): PartialParse<T> {
  if (text.trim() === "") return { value: undefined, complete: false };

  try {
    return { value: JSON.parse(text) as T, complete: true };
  } catch {
    // Not finished yet; fall through to repair.
  }

  const repaired = repair(text);
  if (repaired === undefined) return { value: undefined, complete: false };
  try {
    return { value: JSON.parse(repaired) as T, complete: false };
  } catch {
    return { value: undefined, complete: false };
  }
}

/**
 * Fold a new partial parse into the previous one, keeping what has gone missing.
 *
 * `parsePartial` is deliberately conservative, which means a field can appear
 * and then vanish for a frame: `"value":880` parses, and one character later
 * `"value":880.` is an incomplete number and gets dropped. Correct, and awful to
 * look at.
 *
 * Rendering the merge of every parse so far fixes that without making the parser
 * lie. A key that has been seen keeps its last known value until a better one
 * arrives, so the view only ever gains detail. Values still change as more
 * digits land - 880 becoming 880.2 - which is inherent to streaming a number
 * and is not something a parser can paper over.
 */
export function stableMerge(previous: unknown, next: unknown): unknown {
  if (next === undefined) return previous;
  if (previous === undefined) return next;

  if (isPlainObject(previous) && isPlainObject(next)) {
    const merged: Record<string, unknown> = { ...previous };
    for (const [key, value] of Object.entries(next)) {
      merged[key] = stableMerge(previous[key], value);
    }
    return merged;
  }

  if (Array.isArray(previous) && Array.isArray(next)) {
    const merged: unknown[] = [...(previous as unknown[])];
    for (let i = 0; i < next.length; i++) {
      merged[i] = stableMerge(previous[i], next[i]);
    }
    return merged;
  }

  return next;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
