from __future__ import annotations

from .models import Invoice
from .pricing import line_total

GST_RATES = {"standard": 0.18, "essential": 0.05, "exempt": 0.0}


def tax_for_invoice(invoice: Invoice) -> float:
    tax = 0.0
    for item in invoice.items:
        rate = GST_RATES.get(item.category, GST_RATES["standard"])
        tax += round(line_total(item) * rate, 2)
    return round(tax, 2)


def grand_total(invoice: Invoice) -> float:
    from .pricing import subtotal
    return round(subtotal(invoice) + tax_for_invoice(invoice), 2)
