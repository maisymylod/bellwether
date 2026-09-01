"""Read-side access to the synthetic filing corpus.

The important method here is :meth:`Corpus.resolve`. Agents cite evidence by id
(``NVLN-2025Q3#RF-2``); ``resolve`` turns an id back into the text it points at,
or returns ``None``. That single lookup is the whole grounding check: a citation
either resolves or it does not, and the critic does not have to reason about it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parent / "data" / "filings.json"


@dataclass(frozen=True)
class Filing:
    id: str
    ticker: str
    company: str
    period: str
    filed: str
    financials: dict[str, float]
    segments: list[dict[str, Any]]
    risk_factors: list[dict[str, Any]]
    mdna: list[dict[str, str]]
    guidance: dict[str, float]

    def citable_ids(self) -> list[str]:
        ids = [s["id"] for s in self.segments]
        ids += [r["id"] for r in self.risk_factors]
        ids += [m["id"] for m in self.mdna]
        ids.append(f"{self.id}#FIN")
        ids.append(f"{self.id}#GUID")
        return ids


class Corpus:
    def __init__(self, companies: list[dict[str, Any]], filings: list[dict[str, Any]]) -> None:
        self.companies = {c["ticker"]: c for c in companies}
        self.filings = [Filing(**f) for f in filings]
        self._by_id = {f.id: f for f in self.filings}

    def tickers(self) -> list[str]:
        return sorted(self.companies)

    def company(self, ticker: str) -> dict[str, Any]:
        return self.companies[ticker]

    def periods(self, ticker: str) -> list[str]:
        return sorted(f.period for f in self.filings if f.ticker == ticker)

    def filing(self, ticker: str, period: str) -> Filing:
        key = f"{ticker}-{period}"
        if key not in self._by_id:
            raise KeyError(f"no filing {key}")
        return self._by_id[key]

    def history(self, ticker: str) -> list[Filing]:
        return sorted((f for f in self.filings if f.ticker == ticker), key=lambda f: f.period)

    def latest(self, ticker: str) -> Filing:
        return self.history(ticker)[-1]

    def resolve(self, ref: str) -> str | None:
        """Return the text a citation points at, or ``None`` if it points nowhere."""
        if "#" not in ref:
            return None
        filing_id, anchor = ref.split("#", 1)
        filing = self._by_id.get(filing_id)
        if filing is None:
            return None
        if anchor == "FIN":
            return json.dumps(filing.financials, sort_keys=True)
        if anchor == "GUID":
            return json.dumps(filing.guidance, sort_keys=True)
        for bucket in (filing.segments, filing.risk_factors, filing.mdna):
            for item in bucket:
                if item["id"] == ref:
                    return str(item.get("text") or item.get("name") or "")
        return None

    def resolves(self, ref: str) -> bool:
        return self.resolve(ref) is not None


@lru_cache(maxsize=1)
def load_corpus(path: Path | None = None) -> Corpus:
    raw = json.loads((path or DATA_PATH).read_text())
    return Corpus(raw["companies"], raw["filings"])
