"""Output schemas, one per agent role.

These are the contract in both directions. They constrain generation, they are
what ``robust.call_structured`` validates against, and they are what a repair
turn quotes back to the model when it gets something wrong. ``additionalProperties``
is false everywhere on purpose: a field the schema does not know about is a field
nothing downstream will read, and silently accepting it makes drift invisible.

Every schema that carries a claim also carries a ``source_ref``. That is the
single design decision the grounding check rests on - a claim without a
resolvable citation is mechanically detectable, so the critic never has to
guess whether something was invented.
"""

from __future__ import annotations

from typing import Any

SOURCE_REF = {
    "type": "string",
    "description": "Evidence id from the corpus, e.g. NVLN-2025Q3#RF-2.",
}

CONFIDENCE = {
    "type": "number",
    "minimum": 0.0,
    "maximum": 1.0,
    "description": "How much weight downstream agents should put on this finding.",
}


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


PLAN = _object(
    {
        "subject_ticker": {"type": "string"},
        "period": {"type": "string"},
        "scope": {"type": "string", "description": "What is in and out of bounds."},
        "tasks": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": _object(
                {
                    "id": {"type": "string"},
                    "agent": {
                        "type": "string",
                        "enum": ["fundamentals", "risk", "tone", "peers"],
                    },
                    "focus": {"type": "string"},
                },
                ["id", "agent", "focus"],
            ),
        },
    },
    ["subject_ticker", "period", "scope", "tasks"],
)

FUNDAMENTALS = _object(
    {
        "metrics": {
            "type": "array",
            "minItems": 1,
            "items": _object(
                {
                    "name": {"type": "string"},
                    "value": {"type": "number"},
                    "unit": {"type": "string", "enum": ["musd", "pct", "count"]},
                    "period": {"type": "string"},
                    "source_ref": SOURCE_REF,
                    # Filled in by the consensus reducer, not by the model.
                    "agreement": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "samples": {"type": "integer", "minimum": 1},
                },
                ["name", "value", "unit", "period", "source_ref"],
            ),
        },
        "trend": {"type": "string", "enum": ["expanding", "stable", "contracting"]},
        "commentary": {"type": "string"},
        "confidence": CONFIDENCE,
    },
    ["metrics", "trend", "commentary", "confidence"],
)

RISK = _object(
    {
        "risks": {
            "type": "array",
            "items": _object(
                {
                    "title": {"type": "string"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "why_it_matters": {"type": "string"},
                    "source_ref": SOURCE_REF,
                },
                ["title", "severity", "why_it_matters", "source_ref"],
            ),
        },
        "top_concern": {"type": "string"},
        "confidence": CONFIDENCE,
    },
    ["risks", "top_concern", "confidence"],
)

TONE = _object(
    {
        "tone": {"type": "string", "enum": ["constructive", "neutral", "cautious"]},
        "hedging_ratio": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "drivers": {
            "type": "array",
            "items": _object(
                {"observation": {"type": "string"}, "source_ref": SOURCE_REF},
                ["observation", "source_ref"],
            ),
        },
        "confidence": CONFIDENCE,
    },
    ["tone", "hedging_ratio", "drivers", "confidence"],
)

PEERS = _object(
    {
        "comparisons": {
            "type": "array",
            "items": _object(
                {
                    "peer": {"type": "string"},
                    "metric": {"type": "string"},
                    "subject_value": {"type": "number"},
                    "peer_value": {"type": "number"},
                    "verdict": {"type": "string", "enum": ["ahead", "behind", "in_line"]},
                    "source_ref": SOURCE_REF,
                },
                ["peer", "metric", "subject_value", "peer_value", "verdict", "source_ref"],
            ),
        },
        "summary": {"type": "string"},
        "confidence": CONFIDENCE,
    },
    ["comparisons", "summary", "confidence"],
)

SYNTHESIS = _object(
    {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "key_points": {
            "type": "array",
            "minItems": 1,
            "items": _object(
                {
                    "point": {"type": "string"},
                    "source_refs": {"type": "array", "minItems": 1, "items": SOURCE_REF},
                },
                ["point", "source_refs"],
            ),
        },
        "confidence": CONFIDENCE,
    },
    ["headline", "summary", "key_points", "confidence"],
)

CRITIQUE = _object(
    {
        "claims_checked": {"type": "integer", "minimum": 0},
        "unsupported": {
            "type": "array",
            "items": _object(
                {"point": {"type": "string"}, "reason": {"type": "string"}},
                ["point", "reason"],
            ),
        },
        "verdict": {"type": "string", "enum": ["accept", "revise"]},
        "confidence": CONFIDENCE,
    },
    ["claims_checked", "unsupported", "verdict", "confidence"],
)

BY_TAG = {
    "plan": PLAN,
    "fundamentals": FUNDAMENTALS,
    "risk": RISK,
    "tone": TONE,
    "peers": PEERS,
    "synthesis": SYNTHESIS,
    "critique": CRITIQUE,
}
