from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Bill
from app.domain.errors import BusinessRuleError, NotFoundError
from app.domain.preferences import PreferenceService


def generate_invoice_pdf(session: Session, owner_id: str, bill_id: str, output_dir: Path) -> Path:
    bill = session.scalar(
        select(Bill)
        .where(Bill.id == bill_id, Bill.owner_id == owner_id)
        .options(selectinload(Bill.items), selectinload(Bill.customer))
    )
    if not bill:
        raise NotFoundError("Bill not found")
    if bill.status != "FINALIZED":
        raise BusinessRuleError("Only finalized bills can be exported as invoices")

    prefs = PreferenceService(session).all(owner_id)
    shop_name = prefs.get("shop_name", "Kirana Store")
    shop_address = prefs.get("shop_address", "Address not configured")
    gstin = prefs.get("gstin", "Not configured")
    state = prefs.get("state", "Tamil Nadu")

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{bill.invoice_number}.pdf"

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="CenterSmall", parent=styles["BodyText"], alignment=TA_CENTER, fontSize=9
        )
    )
    styles.add(
        ParagraphStyle(name="RightSmall", parent=styles["BodyText"], alignment=TA_RIGHT, fontSize=8)
    )
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )
    story = [
        Paragraph(shop_name, styles["Title"]),
        Paragraph(shop_address, styles["CenterSmall"]),
        Paragraph(f"GSTIN: {gstin} | State: {state}", styles["CenterSmall"]),
        Spacer(1, 5 * mm),
        Paragraph("TAX INVOICE", styles["Heading2"]),
    ]

    meta = [
        [
            "Invoice",
            bill.invoice_number or "-",
            "Date",
            bill.finalized_at.strftime("%d-%m-%Y %H:%M") if bill.finalized_at else "-",
        ],
        ["Payment", bill.payment_mode or "-", "Reference", bill.payment_reference or "-"],
    ]
    if bill.customer:
        meta.append(["Customer", bill.customer.name, "Khata", "Credit sale"])
    meta_table = Table(meta, colWidths=[24 * mm, 58 * mm, 24 * mm, 58 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
                ("BACKGROUND", (2, 0), (2, -1), colors.whitesmoke),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.extend([meta_table, Spacer(1, 5 * mm)])

    rows = [["Item", "HSN", "Qty", "Rate", "Taxable", "GST", "CGST", "SGST", "Total"]]
    for item in bill.items:
        rows.append(
            [
                item.product_name,
                item.hsn_code,
                f"{item.quantity:g}",
                f"₹{item.unit_price:.2f}",
                f"₹{item.taxable_value:.2f}",
                f"{item.gst_rate:g}%",
                f"₹{item.cgst_amount:.2f}",
                f"₹{item.sgst_amount:.2f}",
                f"₹{item.line_total:.2f}",
            ]
        )

    item_table = Table(
        rows,
        repeatRows=1,
        colWidths=[44 * mm, 16 * mm, 12 * mm, 17 * mm, 20 * mm, 12 * mm, 17 * mm, 17 * mm, 20 * mm],
    )
    item_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDEDED")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 7.2),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ]
        )
    )
    story.extend([item_table, Spacer(1, 4 * mm)])

    totals = Table(
        [
            ["Taxable value", f"₹{bill.taxable_total:.2f}"],
            ["GST collected", f"₹{bill.tax_total:.2f}"],
            ["Grand total", f"₹{bill.grand_total:.2f}"],
        ],
        colWidths=[45 * mm, 35 * mm],
        hAlign="RIGHT",
    )
    totals.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("BACKGROUND", (0, -1), (-1, -1), colors.whitesmoke),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ]
        )
    )
    story.extend(
        [totals, Spacer(1, 5 * mm), Paragraph("Computer-generated invoice", styles["RightSmall"])]
    )
    doc.build(story)
    return path
