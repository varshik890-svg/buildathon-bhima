from invoice.discounts import discount_rate


def test_discount_rate_for_known_codes():
    assert discount_rate("SAVE10") == 0.10
    assert discount_rate("WELCOME5") == 0.05
