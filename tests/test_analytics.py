from app.domain.analytics import AnalyticsService
from app.domain.billing import BillingService


def test_today_close_uses_calendar_day(session, maggi):
    billing = BillingService(session)
    bill = billing.create_draft("owner", "chat")
    billing.add_item(bill.id, "owner", "MAGGI-70G", 2)
    billing.set_payment(bill.id, "owner", "UPI", "txn-today")
    billing.finalize(bill.id, "owner", idempotency_key="today-close")

    summary = AnalyticsService(session).today_summary()
    assert summary["period"] == "today"
    assert summary["timezone"] == "Asia/Kolkata"
    assert summary["bill_count"] == 1
    assert summary["payment_modes"]["UPI"] == "28.00"
    assert summary["top_items"][0]["name"] == "Maggi 70g"
