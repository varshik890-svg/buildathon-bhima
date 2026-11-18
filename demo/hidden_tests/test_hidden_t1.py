# Hidden check for t1 (bulk discount boundary). Copied into the workspace only at verify time.
from invoice import LineItem
from invoice.pricing import line_total


def test_bulk_discount_applies_at_exactly_threshold():
    assert line_total(LineItem("pen", 10, 10.0)) == 95.0


def test_no_discount_below_threshold():
    assert line_total(LineItem("pen", 9, 10.0)) == 90.0
