# Hidden check for t7 (discount code normalization and errors).
import pytest

from invoice.discounts import discount_rate


@pytest.mark.parametrize("code, expected", [("  sAvE10\t", 0.10), ("\nwelcome5 ", 0.05)])
def test_discount_code_normalization(code, expected):
    assert discount_rate(code) == expected


def test_unknown_code_names_original_trimmed_code():
    entered = "\tSpring99\t"
    with pytest.raises(ValueError, match=r"\bSpring99\b") as exc:
        discount_rate(entered)
    assert entered not in str(exc.value)
