from invoice import Invoice, LineItem
from invoice.tax import grand_total, tax_for_invoice


def test_tax_by_category():
    inv = Invoice("INV-2", "Acme", [LineItem("rice", 2, 50.0, "essential"),
                                    LineItem("pen", 1, 100.0)])
    assert tax_for_invoice(inv) == 5.0 + 18.0


def test_grand_total():
    inv = Invoice("INV-3", "Acme", [LineItem("pen", 1, 100.0)])
    assert grand_total(inv) == 118.0
