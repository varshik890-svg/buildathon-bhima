from __future__ import annotations

DISCOUNT_RATES = {"SAVE10": 0.10, "WELCOME5": 0.05}


def discount_rate(code: str) -> float:
    """Return the fraction of the order price covered by a promotion."""
    return DISCOUNT_RATES.get(code, 0.0)
