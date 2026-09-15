from decimal import Decimal

import pytest

from app.domain.billing import BillingService
from app.domain.errors import InsufficientStockError


def test_draft_edits_do_not_decrement_stock(session, maggi):
    billing = BillingService(session)
    bill = billing.create_draft("owner", "chat")
    billing.add_item(bill.id, "owner", "MAGGI-70G", 4)
    billing.update_item(bill.id, "owner", "MAGGI-70G", 6)
    assert maggi.stock_quantity == Decimal("10.000")


def test_finalize_decrements_stock_once(session, maggi):
    billing = BillingService(session)
    bill = billing.create_draft("owner", "chat")
    billing.add_item(bill.id, "owner", "MAGGI-70G", 4)
    billing.set_payment(bill.id, "owner", "UPI", "txn-1")

    finalized = billing.finalize(bill.id, "owner", idempotency_key="same-finalize")
    assert finalized.status == "FINALIZED"
    assert maggi.stock_quantity == Decimal("6.000")

    again = billing.finalize(bill.id, "owner", idempotency_key="same-finalize")
    assert again.id == finalized.id
    assert maggi.stock_quantity == Decimal("6.000")


def test_finalize_rechecks_stock(session, maggi):
    billing = BillingService(session)
    bill = billing.create_draft("owner", "chat")
    billing.add_item(bill.id, "owner", "MAGGI-70G", 8)
    billing.set_payment(bill.id, "owner", "CASH")

    maggi.stock_quantity = Decimal("5")
    session.flush()
    with pytest.raises(InsufficientStockError):
        billing.finalize(bill.id, "owner")
    assert maggi.stock_quantity == Decimal("5")
