from invoice.currency import format_inr
from invoice.parsing import parse_items


def test_parse_items():
    items = parse_items("description,quantity,unit_price,category\npen,2,10.5,standard\nrice,1,50,essential\n")
    assert len(items) == 2
    assert items[0].quantity == 2 and items[0].unit_price == 10.5
    assert items[1].category == "essential"


def test_format_inr():
    assert format_inr(1234.5) == "Rs 1,234.50"
