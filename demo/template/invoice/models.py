from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LineItem:
    description: str
    quantity: int
    unit_price: float
    category: str = "standard"


@dataclass
class Invoice:
    number: str
    customer: str
    items: list[LineItem] = field(default_factory=list)

    def add(self, item: LineItem) -> None:
        self.items.append(item)
