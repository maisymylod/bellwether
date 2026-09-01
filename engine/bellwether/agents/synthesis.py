"""The synthesiser, and the critic that is allowed to send it back.

The critic is split in two on purpose.

The half that can be decided by code is decided by code: every ``source_ref`` in
the answer is looked up in the corpus, and one that resolves to nothing is
unsupported, full stop. No model call, no judgement, no flakiness. This is the
cheapest and most reliable hallucination check available here, and it exists
because the schemas made citations mandatory.

The half that cannot be decided by code is the interesting one: a citation can
resolve perfectly and still not say what the claim says. That question goes to
the model, with the resolved evidence text in front of it.

When either half rejects a point, the critic puts a revision of the synthesiser
back on the queue - which is the one place the graph shape is decided at runtime
rather than up front. Revisions are capped, because a critic and a writer left
alone will argue indefinitely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bellwether.agents import prompts, schemas
from bellwether.agents.subject import Subject
from bellwether.agents.workers import WORKER_NAMES
from bellwether.runtime.agent import AgentResult, NodeContext


@dataclass
class Synthesiser:
    subject: Subject
    question: str
    name: str = "synthesis"
    deps: tuple[str, ...] = WORKER_NAMES
    revision: int = 0
    revision_note: str | None = None

    async def run(self, ctx: NodeContext) -> AgentResult:
        findings = {n: ctx.finding(n) for n in WORKER_NAMES if ctx.has(n)}
        missing = [n for n in WORKER_NAMES if not ctx.has(n)]
        if not findings:
            raise RuntimeError("no specialist returned; nothing to synthesise")

        # A finding the specialist itself flagged as weak is passed on with that
        # flag attached, so the synthesiser is not handed a confident-looking
        # object that nobody believes.
        annotated = {
            node: {**payload, "_confidence": ctx.board.confidence.get(node, 0.0)}
            for node, payload in findings.items()
        }

        outcome = await ctx.call(
            tag="synthesis",
            system=prompts.BASE_SYSTEM,
            prompt=prompts.synthesis_prompt(
                self.question,
                self.subject.ticker,
                self.subject.period,
                annotated,
                missing,
                self.revision_note,
            ),
            schema=schemas.SYNTHESIS,
            context={
                "ticker": self.subject.ticker,
                "period": self.subject.period,
                "findings": findings,
                "missing": missing,
                "revision_note": self.revision_note,
            },
        )
        payload = dict(outcome.payload)
        payload["missing_specialists"] = missing
        payload["revision"] = self.revision
        return AgentResult(
            payload=payload,
            confidence=float(payload.get("confidence", 0.5)),
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            repairs=outcome.repairs,
            retries=outcome.retries,
        )


@dataclass
class Critic:
    subject: Subject
    question: str
    target: str = "synthesis"
    revision: int = 0
    name: str = "critique"
    deps: tuple[str, ...] = ("synthesis",)
    _grounding: dict[str, Any] = field(default_factory=dict, init=False)

    async def run(self, ctx: NodeContext) -> AgentResult:
        answer = ctx.finding(self.target)
        points = list(answer.get("key_points", []))

        # -- half one: decidable by lookup ---------------------------------
        claims: list[dict[str, Any]] = []
        evidence: dict[str, str] = {}
        total_refs = 0
        unresolved_refs = 0
        for point in points:
            refs = list(point.get("source_refs", []))
            bad = [r for r in refs if not ctx.corpus.resolves(r)]
            total_refs += len(refs)
            unresolved_refs += len(bad)
            for ref in refs:
                text = ctx.corpus.resolve(ref)
                if text is not None:
                    evidence[ref] = text
            claims.append(
                {"point": point.get("point", ""), "source_refs": refs, "unresolved_refs": bad}
            )

        grounding_rate = 1.0 if total_refs == 0 else 1.0 - unresolved_refs / total_refs
        self._grounding = {
            "citations": total_refs,
            "unresolved": unresolved_refs,
            "grounding_rate": round(grounding_rate, 3),
        }
        if unresolved_refs:
            ctx.warn(
                "contradiction",
                f"{unresolved_refs} of {total_refs} citations do not resolve to the corpus",
            )

        # -- half two: needs the model -------------------------------------
        outcome = await ctx.call(
            tag="critique",
            system=prompts.CRITIC_SYSTEM,
            prompt=prompts.critique_prompt(claims, evidence),
            schema=schemas.CRITIQUE,
            context={"claims": claims},
        )

        payload = dict(outcome.payload)
        flagged = {u["point"] for u in payload.get("unsupported", [])}
        for claim in claims:
            if claim["unresolved_refs"] and claim["point"] not in flagged:
                # The lookup is authoritative. If the model missed one, add it.
                payload.setdefault("unsupported", []).append(
                    {
                        "point": claim["point"],
                        "reason": "cites "
                        + ", ".join(claim["unresolved_refs"])
                        + ", which is not in the corpus",
                    }
                )
        payload["verdict"] = "revise" if payload.get("unsupported") else "accept"
        payload["grounding"] = self._grounding
        payload["target"] = self.target

        if payload["verdict"] == "revise":
            self._request_revision(ctx, payload)

        return AgentResult(
            payload=payload,
            confidence=float(payload.get("confidence", 0.5)),
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            repairs=outcome.repairs,
            retries=outcome.retries,
        )

    def _request_revision(self, ctx: NodeContext, payload: dict[str, Any]) -> None:
        next_revision = self.revision + 1
        if next_revision > ctx.settings.max_revisions:
            ctx.warn(
                "contradiction",
                f"{len(payload['unsupported'])} unsupported point(s) remain and the "
                f"revision limit of {ctx.settings.max_revisions} is reached; "
                "reported as-is rather than revised again",
            )
            return

        note = "\n".join(f"- {u['point']}: {u['reason']}" for u in payload["unsupported"])
        synth_node = f"synthesis@r{next_revision}"
        critic_node = f"critique@r{next_revision}"
        ctx.schedule(
            synth_node,
            Synthesiser(
                subject=self.subject,
                question=self.question,
                name=synth_node,
                deps=(ctx.node,),
                revision=next_revision,
                revision_note=note,
            ),
            (ctx.node,),
        )
        ctx.schedule(
            critic_node,
            Critic(
                subject=self.subject,
                question=self.question,
                target=synth_node,
                revision=next_revision,
                name=critic_node,
                deps=(synth_node,),
            ),
            (synth_node,),
        )
