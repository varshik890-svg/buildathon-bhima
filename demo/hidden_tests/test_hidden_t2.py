# Hidden check for t2 (CSV export feature).
import csv
import io

from invoice import Invoice, LineItem
from invoice.export import to_csv


def test_to_csv_rows_and_total():
    inv = Invoice("INV-9", "Acme", [LineItem("pen", 2, 10.0), LineItem("rice", 1, 50.0, "essential")])
    rows = list(csv.reader(io.StringIO(to_csv(inv))))
    assert rows[0] == ["description", "quantity", "unit_price", "category", "line_total"]
    assert rows[1] == ["pen", "2", "10.00", "standard", "20.00"]
    assert rows[2] == ["rice", "1", "50.00", "essential", "50.00"]
    assert rows[-1][0] == "TOTAL" and rows[-1][-1] == "76.10"
