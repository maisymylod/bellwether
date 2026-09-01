"""One reliable call, built out of an unreliable one.

Three things go wrong when you ask a model for structured output, and they need
different answers:

* the call fails (network, rate limit, overload) - retry it, with backoff
* the output is not valid JSON, or is valid JSON that violates the schema -
  do not retry blindly, hand the validator's own error back and ask for a fix
* the output is fine but the model is not sure - keep it, and carry the
  confidence forward so the caller can decide

Conflating the first two is the common mistake. A plain retry on a schema
violation re-rolls the dice and usually loses again; a repair turn tells the
model exactly which field was wrong and converges in one round most of the time.
Both are counted separately in the result, because the ratio between them is the
signal you actually want on a dashboard.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from bellwether.providers.base import (
    ModelRequest,
    Provider,
    ProviderError,
    TextChunk,
    UsageChunk,
)

TokenSink = Callable[[str], None]

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class UnrecoverableCall(RuntimeError):
    """Every attempt was used and none produced a schema-valid object."""


@dataclass
class CallOutcome:
    payload: dict[str, Any]
    raw: str
    input_tokens: int = 0
    output_tokens: int = 0
    retries: int = 0
    repairs: int = 0
    warnings: list[tuple[str, str]] = field(default_factory=list)


def extract_json_object(text: str) -> dict[str, Any]:
    """Pull an object out of model output that may not be pure JSON.

    Constrained decoding makes this unnecessary most of the time. It is here
    because "most of the time" is not a property you want a parser to have, and
    because the fallback path is exercised whenever the schema is loosened or a
    provider without constrained output is plugged in.
    """
    candidate = text.strip()
    if not candidate:
        raise ValueError("empty response")

    fenced = _FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in response") from None
        parsed = json.loads(candidate[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def validate(payload: dict[str, Any], schema: dict[str, Any]) -> None:
    jsonschema.validate(payload, schema)


def _repair_prompt(original: str, raw: str, error: str) -> str:
    return (
        f"{original}\n\n"
        "Your previous response did not satisfy the required schema.\n"
        f"Previous response:\n{raw[:2000]}\n\n"
        f"Validation error:\n{error}\n\n"
        "Return the corrected object only. Change nothing except what the error "
        "identifies, and do not invent values to fill a field you cannot support."
    )


async def call_structured(
    provider: Provider,
    request: ModelRequest,
    *,
    max_retries: int = 2,
    max_repairs: int = 1,
    backoff_base_s: float = 0.25,
    on_token: TokenSink | None = None,
) -> CallOutcome:
    """Issue one call and return a schema-valid payload, or raise.

    ``on_token`` receives every text delta as it arrives, including deltas from
    attempts that are later discarded. That is deliberate: the console shows what
    the model actually did, retries and all, rather than a tidied-up version.
    """
    outcome = CallOutcome(payload={}, raw="")
    attempt = 1
    repairs = 0
    prompt = request.prompt
    last_error = "no attempt made"

    while True:
        current = ModelRequest(
            tag=request.tag,
            system=request.system,
            prompt=prompt,
            schema=request.schema,
            max_tokens=request.max_tokens,
            context=request.context,
            seed=request.seed,
            sample=request.sample,
            attempt=attempt,
        )
        buffer: list[str] = []
        try:
            async for chunk in provider.stream(current):
                if isinstance(chunk, TextChunk):
                    buffer.append(chunk.text)
                    if on_token is not None:
                        on_token(chunk.text)
                elif isinstance(chunk, UsageChunk):
                    outcome.input_tokens += chunk.input_tokens
                    outcome.output_tokens += chunk.output_tokens
        except ProviderError as exc:
            last_error = str(exc)
            if not exc.retryable or outcome.retries >= max_retries:
                raise UnrecoverableCall(
                    f"{request.tag}: {last_error} after {outcome.retries} retries"
                ) from exc
            outcome.retries += 1
            outcome.warnings.append(("retry", last_error))
            await asyncio.sleep(backoff_base_s * (2 ** (outcome.retries - 1)))
            attempt += 1
            continue

        raw = "".join(buffer)
        outcome.raw = raw
        try:
            payload = extract_json_object(raw)
            validate(payload, request.schema)
        except (ValueError, json.JSONDecodeError, jsonschema.ValidationError) as exc:
            last_error = _short_error(exc)
            if repairs >= max_repairs:
                raise UnrecoverableCall(
                    f"{request.tag}: schema invalid after {repairs} repairs: {last_error}"
                ) from exc
            repairs += 1
            outcome.repairs = repairs
            outcome.warnings.append(("schema_repair", last_error))
            prompt = _repair_prompt(request.prompt, raw, last_error)
            attempt += 1
            continue

        outcome.payload = payload
        return outcome


def _short_error(exc: Exception) -> str:
    if isinstance(exc, jsonschema.ValidationError):
        path = "/".join(str(p) for p in exc.absolute_path) or "<root>"
        return f"at {path}: {exc.message}"
    return str(exc)
