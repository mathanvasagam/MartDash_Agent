from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Bill
from app.domain.gst import money


class AnalyticsService:
    def __init__(self, session: Session):
        self.session = session

    def sales_between(self, start: datetime, end: datetime) -> list[Bill]:
        return list(
            self.session.scalars(
                select(Bill)
                .where(
                    Bill.status == "FINALIZED",
                    Bill.finalized_at >= start,
                    Bill.finalized_at < end,
                )
                .options(selectinload(Bill.items))
                .order_by(Bill.finalized_at)
            )
        )

    def _build_summary(self, bills: list[Bill], start: datetime, end: datetime) -> dict:
        payment_modes: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        product_qty: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        product_sales: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        total_sales = Decimal("0")
        total_tax = Decimal("0")

        for bill in bills:
            total_sales += bill.grand_total
            total_tax += bill.tax_total
            payment_modes[bill.payment_mode or "UNKNOWN"] += bill.grand_total
            for item in bill.items:
                product_qty[item.product_name] += item.quantity
                product_sales[item.product_name] += item.line_total

        top_items = sorted(
            (
                {
                    "name": name,
                    "quantity": str(product_qty[name]),
                    "sales": str(money(value)),
                }
                for name, value in product_sales.items()
            ),
            key=lambda row: Decimal(row["sales"]),
            reverse=True,
        )[:10]

        return {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "bill_count": len(bills),
            "total_sales": str(money(total_sales)),
            "tax_collected": str(money(total_tax)),
            "payment_modes": {k: str(money(v)) for k, v in payment_modes.items()},
            "top_items": top_items,
        }

    def summary(self, days: int = 1) -> dict:
        if days < 1 or days > 365:
            raise ValueError("days must be between 1 and 365")
        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        summary = self._build_summary(self.sales_between(start, end), start, end)
        summary["days"] = days
        return summary

    def today_summary(self, timezone_name: str = "Asia/Kolkata") -> dict:
        tz = ZoneInfo(timezone_name)
        now_local = datetime.now(tz)
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=1)
        start = start_local.astimezone(UTC)
        end = end_local.astimezone(UTC)
        summary = self._build_summary(self.sales_between(start, end), start, end)
        summary["period"] = "today"
        summary["timezone"] = timezone_name
        return summary
