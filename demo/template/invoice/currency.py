from __future__ import annotations


def format_inr(amount: float) -> str:
    """Format an amount as rupees with two decimals, e.g. 'Rs 1,234.50'."""
    return f"Rs {round(amount, 2):,.2f}"
