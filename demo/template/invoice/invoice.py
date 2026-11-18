from __future__ import annotations

from .currency import format_inr
from .models import Invoice
from .tax import grand_total


def amount_due(invoice: Invoice) -> str:
    """Return the formatted amount payable shown to the customer."""
    return format_inr(grand_total(invoice))
