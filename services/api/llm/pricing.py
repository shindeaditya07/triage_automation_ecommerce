"""Turning token counts into rupees. Prices come from settings, never from code."""

from dataclasses import dataclass
from decimal import Decimal

MILLION = Decimal(1_000_000)
PRECISION = Decimal("0.0001")


@dataclass(frozen=True)
class Price:
    """INR per million tokens."""

    input_inr_per_1m: Decimal = Decimal(0)
    output_inr_per_1m: Decimal = Decimal(0)

    def cost_inr(self, input_tokens: int, output_tokens: int = 0) -> Decimal:
        total = (
            Decimal(input_tokens) * self.input_inr_per_1m
            + Decimal(output_tokens) * self.output_inr_per_1m
        ) / MILLION
        return total.quantize(PRECISION)
