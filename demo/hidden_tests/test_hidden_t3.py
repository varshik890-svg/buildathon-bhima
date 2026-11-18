# Hidden check for t3 (refactor rounding into currency.round_money, half-up).
import inspect

from invoice import LineItem, currency, pricing, tax
from invoice.pricing import line_total


def test_round_money_half_up():
    assert currency.round_money(2.675) == 2.68
    assert currency.round_money(1.005) == 1.01
    assert currency.round_money(-1.005) == -1.01


def test_modules_use_round_money():
    for mod in (pricing, tax):
        src = inspect.getsource(mod)
        assert "round_money" in src
        assert "round(" not in src.replace("round_money(", "")


def test_line_total_rounds_half_up():
    assert line_total(LineItem("x", 1, 2.675)) == 2.68
