from invoice import Invoice, LineItem
from invoice.pricing import line_total, subtotal


def test_line_total_simple():
    assert line_total(LineItem("pen", 3, 10.0)) == 30.0


def test_bulk_discount_large_order():
    assert line_total(LineItem("pen", 20, 10.0)) == 190.0


def test_subtotal():
    inv = Invoice("INV-1", "Acme", [LineItem("pen", 2, 10.0), LineItem("pad", 1, 45.5)])
    assert subtotal(inv) == 65.5
