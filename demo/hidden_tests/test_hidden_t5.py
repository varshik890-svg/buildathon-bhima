# Hidden check for t5 (scrape-style: parse supplier price table and sort it).
from pathlib import Path

from invoice.catalog import load_catalog

HTML = Path(__file__).resolve().parent.parent / "data" / "supplier_prices.html"


def test_sorted_by_price_desc():
    rows = load_catalog(str(HTML), sort_by="price", descending=True)
    assert [r["sku"] for r in rows] == ["RC-01", "OL-02", "PN-10", "SP-03", "MK-01", "NB-05"]
    assert isinstance(rows[0]["price"], float) and isinstance(rows[0]["stock"], int)
    assert set(rows[0]) == {"sku", "item", "category", "price", "stock"}


def test_sorted_by_stock_asc():
    rows = load_catalog(str(HTML), sort_by="stock")
    assert rows[0]["sku"] == "SP-03" and rows[-1]["sku"] == "NB-05"
