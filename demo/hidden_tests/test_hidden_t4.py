# Hidden check for t4 (input validation on LineItem).
import pytest

from invoice import LineItem
from invoice.parsing import parse_items


@pytest.mark.parametrize("kwargs", [
    dict(description="", quantity=1, unit_price=1.0),
    dict(description="  ", quantity=1, unit_price=1.0),
    dict(description="pen", quantity=0, unit_price=1.0),
    dict(description="pen", quantity=-2, unit_price=1.0),
    dict(description="pen", quantity=1, unit_price=-0.5),
    dict(description="pen", quantity=1, unit_price=1.0, category="luxury"),
])
def test_invalid_line_items_raise(kwargs):
    with pytest.raises(ValueError):
        LineItem(**kwargs)


def test_valid_item_ok():
    assert LineItem("pen", 1, 0.0, "exempt").unit_price == 0.0


def test_parse_reports_row_number():
    with pytest.raises(ValueError, match="row 2"):
        parse_items("description,quantity,unit_price\npen,1,10\npad,-1,5\n")
