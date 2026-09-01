"""A deterministic stand-in for the model, with fault injection.

Two reasons this exists.

The first is that the whole system runs with no API key and no network, so the
console demos, the tests, and the eval harness all work on a laptop and in CI.

The second matters more. A stub that always returns clean, well-formed,
correctly-cited output would make every guard above it look like it works while
proving nothing. This one is a *hostile* stub: at a seeded rate it emits
schema-invalid objects, cites evidence ids that do not exist, disagrees with
itself across self-consistency samples, and fails mid-stream. Those are the four
failure modes the runtime claims to handle, so those are the four the offline
provider produces on purpose. Turn ``FaultProfile`` up to 1.0 and the run should
degrade honestly rather than lie; turn it to 0.0 and the eval metrics should hit
their ceilings. Both are asserted in ``tests/``.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from bellwether.corpus import Corpus, load_corpus
from bellwether.providers.base import Chunk, ModelRequest, ProviderError, TextChunk, UsageChunk

REVISION_MARKER = "This is a revision."
REVISION_FAULT_SCALE = 0.15


def _stable_seed(*parts: object) -> int:
    """Seed that does not move between processes.

    ``hash()`` on a string is salted per interpreter (PYTHONHASHSEED), so using
    it here would make "deterministic" mean "deterministic until you restart".
    """
    material = "|".join(str(p) for p in parts).encode()
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


@dataclass(frozen=True)
class FaultProfile:
    """Per-call probability of each injected failure mode."""

    schema_violation: float = 0.10
    bad_citation: float = 0.12
    transient_error: float = 0.06
    low_confidence: float = 0.15
    numeric_outlier: float = 0.18

    @classmethod
    def clean(cls) -> FaultProfile:
        return cls(0.0, 0.0, 0.0, 0.0, 0.0)


class StubProvider:
    """Synthesises grounded answers from the same context the prompt carries."""

    name = "stub"

    def __init__(
        self,
        *,
        seed: int = 1729,
        token_delay_s: float = 0.0,
        faults: FaultProfile | None = None,
        corpus: Corpus | None = None,
    ) -> None:
        self.seed = seed
        self.token_delay_s = token_delay_s
        self.faults = faults if faults is not None else FaultProfile()
        self.corpus = corpus or load_corpus()

    def _rng(self, request: ModelRequest) -> random.Random:
        return random.Random(
            _stable_seed(self.seed, request.tag, request.sample, request.attempt, request.prompt)
        )

    def _fault_rng(self, request: ModelRequest) -> random.Random:
        # Separate stream so changing the payload rng does not shift which faults
        # fire. Keyed on the prompt, not just the tag: the same question always
        # goes the same way, and a re-ask with different wording gets an
        # independent roll, which is what a real model does.
        return random.Random(
            _stable_seed(self.seed, "fault", request.tag, request.sample, request.prompt)
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[Chunk]:
        import asyncio

        rng = self._rng(request)
        frng = self._fault_rng(request)
        # A repair or retry attempt is the model being given another go at the
        # *content*; it does not re-roll the same content fault, otherwise
        # nothing would ever converge.
        first_attempt = request.attempt == 1

        # Transport failure is the exception: it is rolled on every attempt,
        # because a network does not care how many times you have already asked.
        # At the default rate a run almost always recovers; at 1.0 the node
        # exhausts its retries and fails, which is the path the degraded-run
        # tests need to reach.
        transient = random.Random(
            _stable_seed(
                self.seed,
                "transient",
                request.tag,
                request.sample,
                request.prompt,
                request.attempt,
            )
        )
        if transient.random() < self.faults.transient_error:
            raise ProviderError(f"stub: injected transient failure on {request.tag}")

        payload = _synthesise(request, rng, self.corpus, self.faults)

        # A revision prompt quotes the specific point that was rejected, so the
        # model is far less likely to repeat that mistake. The stub reflects
        # that: revisions are not fault-free, just much cleaner.
        scale = REVISION_FAULT_SCALE if REVISION_MARKER in request.prompt else 1.0

        if first_attempt and frng.random() < self.faults.bad_citation * scale:
            _corrupt_citation(payload, frng)
        if first_attempt and frng.random() < self.faults.low_confidence * scale:
            payload["confidence"] = round(frng.uniform(0.05, 0.34), 2)
        if first_attempt and frng.random() < self.faults.schema_violation * scale:
            _violate_schema(payload, request.schema, frng)

        body = json.dumps(payload, ensure_ascii=False)
        # Chunk on a token-ish boundary so the client's incremental JSON parser
        # sees realistically ragged input rather than clean field boundaries.
        for piece in _chunks(body, rng):
            if self.token_delay_s:
                await asyncio.sleep(self.token_delay_s)
            yield TextChunk(piece)

        yield UsageChunk(
            input_tokens=max(1, (len(request.system) + len(request.prompt)) // 4),
            output_tokens=max(1, len(body) // 4),
        )


def _chunks(body: str, rng: random.Random) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(body):
        n = rng.randint(2, 9)
        out.append(body[i : i + n])
        i += n
    return out


def _violate_schema(payload: dict[str, Any], schema: dict[str, Any], rng: random.Random) -> None:
    """Break the payload in a way a real model plausibly would."""
    required = list(schema.get("required", []))
    present = [k for k in required if k in payload]
    if not present:
        payload["unexpected_field"] = "-"
        return
    victim = rng.choice(present)
    if rng.random() < 0.5:
        payload.pop(victim, None)  # omitted a required field
    else:
        payload[victim] = "n/a"  # right field, wrong type


def _corrupt_citation(payload: dict[str, Any], rng: random.Random) -> None:
    """Point one citation at evidence that does not exist."""
    refs = _find_refs(payload)
    if not refs:
        return
    container, key = rng.choice(refs)
    current = container[key]
    if isinstance(current, list):
        if current:
            container[key] = [*current[:-1], "NVLN-2024Q1#RF-99"]
    else:
        container[key] = "NVLN-2024Q1#RF-99"


def _find_refs(node: Any) -> list[tuple[Any, str]]:
    found: list[tuple[Any, str]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("source_ref", "source_refs"):
                found.append((node, k))
            else:
                found.extend(_find_refs(v))
    elif isinstance(node, list):
        for item in node:
            found.extend(_find_refs(item))
    return found


# ----------------------------------------------------------------------------
# Per-agent synthesis. Each reads the same structured context the live prompt
# renders into text, so stub answers are grounded in the real corpus.
# ----------------------------------------------------------------------------


def _synthesise(
    request: ModelRequest, rng: random.Random, corpus: Corpus, faults: FaultProfile
) -> dict[str, Any]:
    fn = _SYNTH.get(request.tag)
    if fn is None:
        raise ProviderError(f"stub has no synthesiser for tag {request.tag!r}", retryable=False)
    return fn(request.context, rng, corpus, faults)


def _plan(ctx: dict[str, Any], rng: random.Random, _c: Corpus, _f: FaultProfile) -> dict[str, Any]:
    ticker = ctx["ticker"]
    period = ctx["period"]
    tasks = [
        {
            "id": "t1",
            "agent": "fundamentals",
            "focus": f"revenue, margin and cash trend for {ticker}",
        },
        {"id": "t2", "agent": "risk", "focus": f"risk factors disclosed in {period}"},
        {"id": "t3", "agent": "tone", "focus": "hedging and management posture in the narrative"},
        {"id": "t4", "agent": "peers", "focus": "position against disclosed peers"},
    ]
    return {
        "subject_ticker": ticker,
        "period": period,
        "tasks": tasks,
        "scope": (
            f"Answer from the {period} filing for {ticker} and the three preceding "
            "periods. Do not reach outside the corpus."
        ),
    }


def _fundamentals(
    ctx: dict[str, Any], rng: random.Random, _c: Corpus, faults: FaultProfile
) -> dict[str, Any]:
    history = ctx["history"]
    latest = history[-1]
    prior = history[-2] if len(history) > 1 else latest
    fin = latest["financials"]
    pfin = prior["financials"]

    def jitter(value: float) -> float:
        # Self-consistency exists because a model asked the same question twice
        # does not answer identically. The stub reproduces that: small drift
        # most of the time, an occasional outlier that the median has to reject.
        # ``numeric_outlier`` is the rate, so the eval can sweep the point at
        # which a median over n samples stops being able to save the answer.
        if rng.random() < faults.numeric_outlier:
            return round(value * rng.uniform(1.4, 2.2), 1)
        return round(value * rng.uniform(0.995, 1.005), 1)

    growth = (fin["revenue_musd"] - pfin["revenue_musd"]) / max(pfin["revenue_musd"], 1e-9) * 100
    margin = (fin["revenue_musd"] - fin["cost_of_revenue_musd"]) / fin["revenue_musd"] * 100
    ref = f"{latest['id']}#FIN"

    metrics = [
        {
            "name": "revenue",
            "value": jitter(fin["revenue_musd"]),
            "unit": "musd",
            "period": latest["period"],
            "source_ref": ref,
        },
        {
            "name": "net_income",
            "value": jitter(fin["net_income_musd"]),
            "unit": "musd",
            "period": latest["period"],
            "source_ref": ref,
        },
        {
            "name": "free_cash_flow",
            "value": jitter(fin["free_cash_flow_musd"]),
            "unit": "musd",
            "period": latest["period"],
            "source_ref": ref,
        },
        {
            "name": "gross_margin",
            "value": round(margin, 1),
            "unit": "pct",
            "period": latest["period"],
            "source_ref": ref,
        },
        {
            "name": "revenue_growth_qoq",
            "value": round(growth, 1),
            "unit": "pct",
            "period": latest["period"],
            "source_ref": ref,
        },
    ]
    trend = "expanding" if growth > 1.5 else ("contracting" if growth < -1.5 else "stable")
    return {
        "metrics": metrics,
        "trend": trend,
        "commentary": (
            f"Revenue of {fin['revenue_musd']}m in {latest['period']} against "
            f"{pfin['revenue_musd']}m prior, a {growth:.1f}% move, with gross margin at "
            f"{margin:.1f}%."
        ),
        "confidence": round(rng.uniform(0.62, 0.93), 2),
    }


def _risk(ctx: dict[str, Any], rng: random.Random, _c: Corpus, _f: FaultProfile) -> dict[str, Any]:
    factors = ctx["risk_factors"]
    ranked = sorted(factors, key=lambda r: {"high": 0, "medium": 1, "low": 2}[r["severity"]])
    risks = [
        {
            "title": r["title"],
            "severity": r["severity"],
            "why_it_matters": r["text"].split(". ")[0] + ".",
            "source_ref": r["id"],
        }
        for r in ranked[:4]
    ]
    return {
        "risks": risks,
        "top_concern": risks[0]["title"] if risks else "none disclosed",
        "confidence": round(rng.uniform(0.58, 0.9), 2),
    }


def _tone(ctx: dict[str, Any], rng: random.Random, _c: Corpus, _f: FaultProfile) -> dict[str, Any]:
    paras = ctx["mdna"]
    hedge_markers = ("believes", "may not", "subject to revision", "could shift", "cannot assure")
    hedged = [p for p in paras if any(m in p["text"] for m in hedge_markers)]
    ratio = round(len(hedged) / max(len(paras), 1), 2)
    guidance_down = any("downward" in p["text"] for p in paras)
    guidance_up = any("raised" in p["text"] for p in paras)
    tone = (
        "cautious"
        if (guidance_down or ratio > 0.5)
        else ("constructive" if guidance_up else "neutral")
    )
    drivers = [
        {"observation": p["text"][:110], "source_ref": p["id"]} for p in (hedged or paras)[:3]
    ]
    return {
        "tone": tone,
        "hedging_ratio": ratio,
        "drivers": drivers,
        "confidence": round(rng.uniform(0.55, 0.88), 2),
    }


def _peers(ctx: dict[str, Any], rng: random.Random, _c: Corpus, _f: FaultProfile) -> dict[str, Any]:
    subject = ctx["subject"]
    comparisons = []
    for peer in ctx["peers"]:
        for metric in ("revenue_musd", "net_income_musd"):
            sv = subject["financials"][metric]
            pv = peer["financials"][metric]
            verdict = "ahead" if sv > pv * 1.05 else ("behind" if sv < pv * 0.95 else "in_line")
            comparisons.append(
                {
                    "peer": peer["ticker"],
                    "metric": metric,
                    "subject_value": sv,
                    "peer_value": pv,
                    "verdict": verdict,
                    "source_ref": f"{peer['id']}#FIN",
                }
            )
    ahead = sum(1 for c in comparisons if c["verdict"] == "ahead")
    return {
        "comparisons": comparisons,
        "summary": f"Ahead of disclosed peers on {ahead} of {len(comparisons)} compared measures.",
        "confidence": round(rng.uniform(0.6, 0.9), 2),
    }


def _synthesis(
    ctx: dict[str, Any], rng: random.Random, _c: Corpus, _f: FaultProfile
) -> dict[str, Any]:
    findings = ctx["findings"]
    fund = findings.get("fundamentals", {})
    risk = findings.get("risk", {})
    tone = findings.get("tone", {})
    peers = findings.get("peers", {})
    ticker = ctx["ticker"]
    period = ctx["period"]

    points: list[dict[str, Any]] = []
    for metric in fund.get("metrics", [])[:3]:
        points.append(
            {
                "point": f"{metric['name'].replace('_', ' ')} of {metric['value']} "
                f"{metric['unit']} in {metric['period']}",
                "source_refs": [metric["source_ref"]],
            }
        )
    for entry in risk.get("risks", [])[:2]:
        points.append(
            {
                "point": f"{entry['severity']} severity: {entry['title']}",
                "source_refs": [entry["source_ref"]],
            }
        )
    if tone.get("drivers"):
        points.append(
            {
                "point": f"narrative tone reads {tone.get('tone', 'neutral')} with a hedging "
                f"ratio of {tone.get('hedging_ratio', 0)}",
                "source_refs": [d["source_ref"] for d in tone["drivers"][:2]],
            }
        )
    if peers.get("comparisons"):
        points.append(
            {
                "point": peers.get("summary", "peer comparison inconclusive"),
                "source_refs": [c["source_ref"] for c in peers["comparisons"][:2]],
            }
        )

    revision = ctx.get("revision_note")
    headline = (
        f"{ticker} {period}: {fund.get('trend', 'unclear')} trend, "
        f"{tone.get('tone', 'neutral')} narrative"
    )
    summary = (
        f"{ticker} filed {period} with a {fund.get('trend', 'unclear')} revenue trend. "
        f"{fund.get('commentary', '')} The most material disclosed exposure is "
        f"{risk.get('top_concern', 'not identified')}. "
        f"{peers.get('summary', '')} "
        f"Management narrative reads {tone.get('tone', 'neutral')}."
    )
    if revision:
        summary += " Revised after review."
    return {
        "headline": headline,
        "summary": " ".join(summary.split()),
        "key_points": points,
        "confidence": round(rng.uniform(0.6, 0.92), 2),
    }


def _critique(
    ctx: dict[str, Any], rng: random.Random, _c: Corpus, _f: FaultProfile
) -> dict[str, Any]:
    claims = ctx["claims"]
    # The stub critic only reports what the deterministic pre-check already
    # found. The live critic is asked the harder semantic question on top.
    unsupported = [
        {"point": c["point"], "reason": "cites evidence that does not resolve"}
        for c in claims
        if c.get("unresolved_refs")
    ]
    return {
        "claims_checked": len(claims),
        "unsupported": unsupported,
        "verdict": "revise" if unsupported else "accept",
        "confidence": round(rng.uniform(0.7, 0.95), 2),
    }


_SYNTH = {
    "plan": _plan,
    "fundamentals": _fundamentals,
    "risk": _risk,
    "tone": _tone,
    "peers": _peers,
    "synthesis": _synthesis,
    "critique": _critique,
}
