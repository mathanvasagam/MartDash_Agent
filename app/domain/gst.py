from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

MONEY = Decimal("0.01")


def money(value: Decimal | str | int | float) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class TaxBreakdown:
    line_total: Decimal
    taxable_value: Decimal
    gst_amount: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal


def inclusive_tax_breakdown(unit_price, quantity, gst_rate) -> TaxBreakdown:
    """Calculate GST for GST-inclusive retail prices using deterministic Decimal arithmetic."""
    unit_price = Decimal(str(unit_price))
    quantity = Decimal(str(quantity))
    gst_rate = Decimal(str(gst_rate))
    line_total = money(unit_price * quantity)

    if gst_rate == 0:
        return TaxBreakdown(line_total, line_total, money(0), money(0), money(0))

    taxable = money(line_total * Decimal("100") / (Decimal("100") + gst_rate))
    gst = money(line_total - taxable)
    cgst = money(gst / Decimal("2"))
    sgst = money(gst - cgst)
    return TaxBreakdown(line_total, taxable, gst, cgst, sgst)
