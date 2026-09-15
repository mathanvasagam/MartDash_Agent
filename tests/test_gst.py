from decimal import Decimal

from app.domain.gst import inclusive_tax_breakdown, money


def test_money_uses_half_up_rounding():
    assert money("10.125") == Decimal("10.13")


def test_inclusive_gst_split_is_deterministic():
    tax = inclusive_tax_breakdown("105", 1, 5)
    assert tax.line_total == Decimal("105.00")
    assert tax.taxable_value == Decimal("100.00")
    assert tax.gst_amount == Decimal("5.00")
    assert tax.cgst_amount == Decimal("2.50")
    assert tax.sgst_amount == Decimal("2.50")


def test_zero_rated_item_has_no_tax():
    tax = inclusive_tax_breakdown("58", "2.5", 0)
    assert tax.line_total == Decimal("145.00")
    assert tax.taxable_value == Decimal("145.00")
    assert tax.gst_amount == Decimal("0.00")
