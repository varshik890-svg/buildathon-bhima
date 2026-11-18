# Hidden check for t6 (invoice GST rounded by rate).
import pytest

from invoice import Invoice, LineItem
from invoice.invoice import amount_due


@pytest.mark.parametrize("items, expected", [
    ([LineItem("pen", 1, 10.02), LineItem("pad", 1, 20.02)], "Rs 35.45"),
    ([LineItem("pen", 1, 10.03), LineItem("pad", 1, 20.03)], "Rs 35.47"),
    ([LineItem("pen", 1, 10.02), LineItem("pad", 1, 20.02),
      LineItem("rice", 1, 10.14, "essential"),
      LineItem("milk", 1, 20.50, "exempt")], "Rs 66.60"),
])
def test_amount_due_with_fractional_gst(items, expected):
    assert amount_due(Invoice("INV-6", "Acme", items)) == expected
