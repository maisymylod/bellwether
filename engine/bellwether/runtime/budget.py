"""Token and cost accounting, and the ceiling that stops a run running away.

An agent graph that can retry, re-sample, and revise has no natural upper bound
on spend. The budget is the bound. It is checked before a call is issued rather
than after, because the useful question is "can I afford this" and not "what did
that cost me".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bellwether.providers.claude import estimate_cost_usd


class BudgetExhausted(RuntimeError):
    pass


@dataclass
class Budget:
    max_output_tokens: int = 60_000
    max_cost_usd: float = 2.00
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    per_node: dict[str, int] = field(default_factory=dict)

    @property
    def cost_usd(self) -> float:
        return estimate_cost_usd(self.input_tokens, self.output_tokens)

    def remaining_output_tokens(self) -> int:
        return max(0, self.max_output_tokens - self.output_tokens)

    def can_afford(self, projected_output_tokens: int) -> bool:
        if self.output_tokens + projected_output_tokens > self.max_output_tokens:
            return False
        return self.cost_usd < self.max_cost_usd

    def check(self, projected_output_tokens: int) -> None:
        if not self.can_afford(projected_output_tokens):
            raise BudgetExhausted(
                f"budget exhausted at {self.output_tokens} output tokens / ${self.cost_usd:.4f}"
            )

    def charge(self, node: str, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.calls += 1
        self.per_node[node] = self.per_node.get(node, 0) + output_tokens
