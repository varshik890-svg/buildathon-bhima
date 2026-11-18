from invoice import Invoice, LineItem
from invoice.invoice import amount_due


def test_amount_due():
    inv = Invoice("INV-4", "Acme", [LineItem("pen", 1, 100.0)])
    assert amount_due(inv) == "Rs 118.00"
