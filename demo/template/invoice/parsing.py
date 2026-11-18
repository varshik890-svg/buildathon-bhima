from __future__ import annotations

import csv
import io

from .models import LineItem


def parse_items(csv_text: str) -> list[LineItem]:
    """Parse 'description,quantity,unit_price[,category]' rows (header required)."""
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    items = []
    for row in reader:
        items.append(LineItem(
            description=row["description"].strip(),
            quantity=int(row["quantity"]),
            unit_price=float(row["unit_price"]),
            category=(row.get("category") or "standard").strip(),
        ))
    return items
