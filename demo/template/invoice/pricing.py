from __future__ import annotations

from .models import Invoice, LineItem

BULK_THRESHOLD = 10      # units of a single line
BULK_DISCOUNT = 0.05     # 5% off that line


def line_total(item: LineItem) -> float:
    total = item.quantity * item.unit_price
    if item.quantity > BULK_THRESHOLD:
        total = total * (1 - BULK_DISCOUNT)
    return round(total, 2)


def subtotal(invoice: Invoice) -> float:
    return round(sum(line_total(i) for i in invoice.items), 2)
