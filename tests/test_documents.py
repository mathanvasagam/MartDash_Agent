from pathlib import Path

from app.documents.deck import generate_analysis_deck
from app.documents.invoice import generate_invoice_pdf
from app.domain.billing import BillingService
from app.domain.preferences import PreferenceService


def _finalized_bill(session, maggi):
    billing = BillingService(session)
    bill = billing.create_draft("owner", "chat")
    billing.add_item(bill.id, "owner", "MAGGI-70G", 2)
    billing.set_payment(bill.id, "owner", "CASH")
    return billing.finalize(bill.id, "owner", idempotency_key="artifact-bill")


def test_invoice_pdf_is_real_file(session, maggi, tmp_path: Path):
    prefs = PreferenceService(session)
    prefs.set("owner", "shop_name", "Demo Kirana")
    prefs.set("owner", "gstin", "33ABCDE1234F1Z5")
    bill = _finalized_bill(session, maggi)
    output = generate_invoice_pdf(session, "owner", bill.id, tmp_path / "invoices")
    assert output.suffix == ".pdf"
    assert output.exists()
    assert output.stat().st_size > 500


def test_analysis_deck_is_real_pptx(session, maggi, tmp_path: Path):
    _finalized_bill(session, maggi)
    output = generate_analysis_deck(session, "owner", 7, tmp_path / "decks")
    assert output.suffix == ".pptx"
    assert output.exists()
    assert output.stat().st_size > 1000
