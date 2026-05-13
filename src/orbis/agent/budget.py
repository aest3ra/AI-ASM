"""Agent step/token budget tracking."""

from __future__ import annotations

from dataclasses import dataclass


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class BudgetTracker:
    max_steps: int = 20
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    steps_used: int = 0
    input_tokens_used: int = 0
    output_tokens_used: int = 0

    def consume_step(self, count: int = 1) -> None:
        self.steps_used += count
        if self.steps_used > self.max_steps:
            raise BudgetExceeded("step budget exceeded")

    def consume_tokens(self, *, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.input_tokens_used += input_tokens
        self.output_tokens_used += output_tokens
        if (
            self.max_input_tokens is not None
            and self.input_tokens_used > self.max_input_tokens
        ):
            raise BudgetExceeded("input token budget exceeded")
        if (
            self.max_output_tokens is not None
            and self.output_tokens_used > self.max_output_tokens
        ):
            raise BudgetExceeded("output token budget exceeded")

    def remaining_steps(self) -> int:
        return max(0, self.max_steps - self.steps_used)
