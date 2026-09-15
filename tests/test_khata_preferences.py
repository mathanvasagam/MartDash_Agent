from decimal import Decimal

import pytest

from app.domain.errors import BusinessRuleError, NotFoundError
from app.domain.khata import KhataService
from app.domain.preferences import PreferenceService


def test_khata_credit_payment_and_guardrails(session):
    khata = KhataService(session)
    assert khata.add_credit("owner", "Ramesh", 500) == Decimal("500.00")
    assert khata.record_payment("owner", "Ramesh", 300) == Decimal("200.00")
    assert khata.balance("owner", "Ramesh") == Decimal("200.00")

    with pytest.raises(BusinessRuleError, match="exceeds outstanding"):
        khata.record_payment("owner", "Ramesh", 201)
    with pytest.raises(NotFoundError):
        khata.record_payment("owner", "Unknown", 10)


def test_preferences_persist_in_database(session):
    prefs = PreferenceService(session)
    prefs.set("owner", "default_payment", "UPI")
    prefs.set("owner", "shop_name", "Demo Kirana")
    assert prefs.get("owner", "default_payment") == "UPI"
    assert prefs.all("owner")["shop_name"] == "Demo Kirana"
