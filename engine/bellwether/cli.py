"""Command line entry point.

``bellwether ask``    run the graph and print the answer
``bellwether eval``   run the golden set and print the metrics
``bellwether gate``   the same, but exit non-zero if a threshold is breached
``bellwether sweep``  measure what self-consistency sampling is actually buying
``bellwether serve``  start the API for the console
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from typing import Any

from bellwether.config import Settings
from bellwether.corpus import load_corpus
from bellwether.eval.harness import run_suite
from bellwether.eval.metrics import Thresholds
from bellwether.providers import build_provider
from bellwether.run import run_to_completion


def _ask(args: argparse.Namespace) -> int:
    settings = replace(Settings.from_env(), stub_token_delay_s=0.0)
    if args.seed is not None:
        settings = replace(settings, seed=args.seed)
    provider = build_provider(settings)

    async def go() -> dict[str, Any]:
        _, report = await run_to_completion(
            question=args.question, provider=provider, settings=settings
        )
        return report

    report = asyncio.run(go())
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    answer = report.get("answer", {})
    print(f"\n{answer.get('headline', '(no answer)')}\n")
    print(_wrap(answer.get("summary", "")))
    print("\nKey points")
    for point in answer.get("key_points", []):
        mark = " " if point.get("supported", True) else "!"
        print(f"  {mark} {point['point']}")
        print(f"    {', '.join(point.get('source_refs', []))}")
        if not point.get("supported", True):
            print(f"    rejected by critic: {point.get('rejected_because', '')}")

    grounding = report.get("grounding", {})
    critique = report.get("critique", {})
    print(
        f"\ncitations {grounding.get('citations', 0)}, "
        f"unresolved {grounding.get('unresolved', 0)}, "
        f"revisions {report.get('revisions', 0)}, "
        f"warnings {report.get('warnings', 0)}"
    )
    if critique.get("unsupported"):
        print(
            f"  {len(critique['unsupported'])} point(s) marked ! above were left in the "
            "answer but flagged; the revision limit was reached before they could be "
            "re-evidenced"
        )
    usage = report.get("usage", {})
    timing = report.get("timing", {})
    print(
        f"{usage.get('calls', 0)} calls, {usage.get('output_tokens', 0)} output tokens, "
        f"${usage.get('cost_usd', 0):.4f}, ttft {timing.get('ttft_ms')}ms, "
        f"total {timing.get('total_ms')}ms"
    )
    return 0


def _wrap(text: str, width: int = 88) -> str:
    import textwrap

    return "\n".join(textwrap.wrap(text, width=width))


def _eval(args: argparse.Namespace) -> int:
    settings = Settings(provider="stub", consistency_samples=args.samples)
    report = asyncio.run(run_suite(settings=settings, limit=args.limit, base_seed=args.seed))
    payload = report.to_dict()
    if not args.verbose:
        payload.pop("scores", None)
    print(json.dumps(payload, indent=2))
    return 0


def _gate(args: argparse.Namespace) -> int:
    settings = Settings(provider="stub", consistency_samples=args.samples)
    report = asyncio.run(run_suite(settings=settings, base_seed=args.seed))
    payload = report.to_dict()
    payload.pop("scores", None)
    print(json.dumps(payload, indent=2))

    failures = report.failures(Thresholds())
    if failures:
        print("\nGATE FAILED", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1
    print("\ngate passed")
    return 0


def _sweep(args: argparse.Namespace) -> int:
    """What does paying for n samples instead of one actually buy?

    Averaged over several base seeds, because a single seed's answer to this
    question is within the noise it is supposed to be measuring.
    """
    seeds = [args.seed + offset for offset in (0, 5000, 11000, 18000, 27000)]
    rows: list[tuple[int, float, float]] = []
    for n in args.samples_list:
        reports = [
            asyncio.run(
                run_suite(
                    settings=Settings(provider="stub", consistency_samples=n),
                    base_seed=seed,
                )
            )
            for seed in seeds
        ]
        rows.append(
            (
                n,
                sum(r.numeric_accuracy for r in reports) / len(reports),
                sum(r.cost_usd_per_run for r in reports) / len(reports),
            )
        )

    baseline_cost = rows[0][2] if rows else 0.0
    print(f"{'samples':>7}  {'numeric_accuracy':>16}  {'$/run':>8}  {'vs 1 sample':>12}")
    for n, accuracy, cost in rows:
        delta = (cost / baseline_cost - 1) * 100 if baseline_cost else 0.0
        print(f"{n:>7}  {accuracy:>16.3f}  {cost:>8.4f}  {delta:>11.0f}%")
    print(
        f"\n{len(seeds)} base seeds x {(rows and 18) or 0} cases each, stub provider "
        "at the default fault rate"
    )
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = Settings.from_env()
    if settings.requires_api_key():
        print(f"provider={settings.provider}: will call the live API", file=sys.stderr)
    uvicorn.run(
        "bellwether.api.server:app",
        host=args.host,
        port=args.port or settings.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


def _corpus(_: argparse.Namespace) -> int:
    corpus = load_corpus()
    for ticker in corpus.tickers():
        company = corpus.company(ticker)
        print(
            f"{ticker}  {company['name']:<28} {company['sector']:<24} "
            f"periods {', '.join(corpus.periods(ticker))}  peers {', '.join(company['peers'])}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bellwether", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="run the agent graph on one question")
    ask.add_argument("question")
    ask.add_argument("--json", action="store_true", help="print the full report")
    ask.add_argument("--seed", type=int, default=None)
    ask.set_defaults(func=_ask)

    ev = sub.add_parser("eval", help="score the golden set")
    ev.add_argument("--limit", type=int, default=None)
    ev.add_argument("--samples", type=int, default=3)
    ev.add_argument("--seed", type=int, default=4001)
    ev.add_argument("--verbose", action="store_true", help="include per-case scores")
    ev.set_defaults(func=_eval)

    gate = sub.add_parser("gate", help="score the golden set and enforce thresholds")
    gate.add_argument("--samples", type=int, default=3)
    gate.add_argument("--seed", type=int, default=4001)
    gate.set_defaults(func=_gate)

    sweep = sub.add_parser("sweep", help="measure the self-consistency tradeoff")
    sweep.add_argument("--samples-list", type=int, nargs="+", default=[1, 3, 5])
    sweep.add_argument("--seed", type=int, default=4001)
    sweep.set_defaults(func=_sweep)

    serve = sub.add_parser("serve", help="start the API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=_serve)

    corpus = sub.add_parser("corpus", help="list the synthetic corpus")
    corpus.set_defaults(func=_corpus)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
