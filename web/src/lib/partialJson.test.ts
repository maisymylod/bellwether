import { describe, expect, it } from "vitest";
import { parsePartial, repair, stableMerge } from "./partialJson";

const PAYLOAD = JSON.stringify({
  metrics: [
    { name: "revenue", value: 880.2, unit: "musd", source_ref: "NVLN-2025Q3#FIN" },
    { name: "net_income", value: -12.5, unit: "musd", source_ref: "NVLN-2025Q3#FIN" },
  ],
  trend: "expanding",
  commentary: 'grew 3.9%, with a "strong" quarter and a \\ backslash',
  nested: { deep: [true, false, null, { deeper: [1, [2, [3]]] }] },
  confidence: 0.87,
});

describe("parsePartial", () => {
  it("returns the value and marks it complete when the JSON is whole", () => {
    expect(parsePartial('{"a":1}')).toEqual({ value: { a: 1 }, complete: true });
  });

  it("returns nothing for empty or whitespace input", () => {
    for (const input of ["", "   ", "\n\t"]) {
      expect(parsePartial(input)).toEqual({ value: undefined, complete: false });
    }
  });

  it("closes an open object", () => {
    expect(parsePartial('{"a":1').value).toEqual({ a: 1 });
  });

  it("closes nested containers in the right order", () => {
    expect(parsePartial('{"a":[1,2,{"b":3').value).toEqual({ a: [1, 2, { b: 3 }] });
  });

  it("closes a string that is still being written", () => {
    expect(parsePartial('{"summary":"revenue grew').value).toEqual({
      summary: "revenue grew",
    });
  });

  it("drops a key whose value has not arrived", () => {
    expect(parsePartial('{"a":1,"b"').value).toEqual({ a: 1 });
    expect(parsePartial('{"a":1,"b":').value).toEqual({ a: 1 });
    expect(parsePartial('{"a":1,"b": ').value).toEqual({ a: 1 });
  });

  it("drops a trailing comma", () => {
    expect(parsePartial('{"a":1,').value).toEqual({ a: 1 });
    expect(parsePartial("[1,2,").value).toEqual([1, 2]);
  });

  it("drops a number that is not finished rather than guessing at it", () => {
    // 1.  could become 1.87. Rendering it as 1 and correcting later is worse
    // than rendering nothing for one frame.
    expect(parsePartial('{"a":1,"b":1.').value).toEqual({ a: 1 });
    expect(parsePartial('{"a":-').value).toEqual({});
    expect(parsePartial('{"a":1e').value).toEqual({});
  });

  it("drops a literal that is not finished", () => {
    expect(parsePartial('{"a":tru').value).toEqual({});
    expect(parsePartial('{"a":nul').value).toEqual({});
    expect(parsePartial('{"a":true').value).toEqual({ a: true });
  });

  it("keeps a complete integer at the tail", () => {
    expect(parsePartial('{"a":12').value).toEqual({ a: 12 });
  });

  it("handles escaped quotes inside a partial string", () => {
    expect(parsePartial('{"a":"he said \\"yes').value).toEqual({ a: 'he said "yes' });
  });

  it("drops a dangling backslash", () => {
    expect(parsePartial('{"a":"path\\').value).toEqual({ a: "path" });
  });

  it("drops a half-written unicode escape", () => {
    expect(parsePartial('{"a":"x\\u26').value).toEqual({ a: "x" });
    expect(parsePartial('{"a":"x\\u2603').value).toEqual({ a: "x☃" });
  });

  it("does not confuse structural characters inside strings", () => {
    expect(parsePartial('{"a":"}{[,:').value).toEqual({ a: "}{[,:" });
  });

  it("handles an array of objects mid-element", () => {
    const text = '{"metrics":[{"name":"revenue","value":880.2},{"name":"net_i';
    // The second element is kept as an empty object rather than dropped: it has
    // begun arriving, and a list that grows a placeholder then fills it reads
    // better than one whose rows appear fully formed a beat late.
    expect(parsePartial(text).value).toEqual({
      metrics: [{ name: "revenue", value: 880.2 }, { name: "net_i" }],
    });
  });

  it("never throws on any prefix of a real payload", () => {
    let lastComplete = false;
    for (let i = 0; i <= PAYLOAD.length; i++) {
      const parsed = parsePartial(PAYLOAD.slice(0, i));
      expect(typeof parsed.complete).toBe("boolean");
      lastComplete = parsed.complete;
    }
    expect(lastComplete).toBe(true);
    expect(parsePartial(PAYLOAD)).toEqual({ value: JSON.parse(PAYLOAD), complete: true });
  });

  it("gives up rather than inventing a value it cannot repair", () => {
    expect(parsePartial("not json at all").value).toBeUndefined();
    expect(parsePartial("}").value).toBeUndefined();
  });
});

describe("repair", () => {
  it("leaves already valid JSON alone", () => {
    expect(repair('{"a":1}')).toBe('{"a":1}');
  });

  it("returns undefined for nothing", () => {
    expect(repair("   ")).toBeUndefined();
  });
});

describe("stableMerge", () => {
  it("keeps a key that the newest parse dropped", () => {
    expect(stableMerge({ a: 1, b: 2 }, { a: 1 })).toEqual({ a: 1, b: 2 });
  });

  it("prefers the newer value when both have the key", () => {
    expect(stableMerge({ a: 1 }, { a: 2 })).toEqual({ a: 2 });
  });

  it("merges nested objects and arrays rather than replacing them", () => {
    expect(
      stableMerge({ m: [{ name: "a", value: 1 }] }, { m: [{ name: "a" }, { name: "b" }] }),
    ).toEqual({ m: [{ name: "a", value: 1 }, { name: "b" }] });
  });

  it("keeps trailing array elements the newest parse lost", () => {
    expect(stableMerge([1, 2, 3], [1, 2])).toEqual([1, 2, 3]);
  });

  it("passes through when either side is missing", () => {
    expect(stableMerge(undefined, { a: 1 })).toEqual({ a: 1 });
    expect(stableMerge({ a: 1 }, undefined)).toEqual({ a: 1 });
  });

  it("never loses a field once seen, over every prefix of a payload", () => {
    // This is the property the console actually depends on: the rendered view
    // only ever gains detail, whatever the parser does frame to frame.
    let view: unknown = undefined;
    let previousKeys = 0;
    for (let i = 0; i <= PAYLOAD.length; i++) {
      view = stableMerge(view, parsePartial(PAYLOAD.slice(0, i)).value);
      const keys = view === undefined ? 0 : Object.keys(view as object).length;
      expect(keys).toBeGreaterThanOrEqual(previousKeys);
      previousKeys = keys;
    }
    expect(view).toEqual(JSON.parse(PAYLOAD));
  });
});
