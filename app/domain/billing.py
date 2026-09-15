from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Bill, BillItem, Product, StockMovement
from app.domain.errors import BusinessRuleError, InsufficientStockError, NotFoundError
from app.domain.gst import inclusive_tax_breakdown, money
from app.domain.inventory import InventoryService
from app.domain.khata import KhataService


class BillingService:
    def __init__(self, session: Session):
        self.session = session
        self.inventory = InventoryService(session)

    def active_draft(self, owner_id: str, chat_id: str) -> Bill | None:
        return self.session.scalar(
            select(Bill)
            .where(Bill.owner_id == owner_id, Bill.chat_id == chat_id, Bill.status == "DRAFT")
            .order_by(Bill.created_at.desc())
            .options(selectinload(Bill.items))
        )

    def create_draft(self, owner_id: str, chat_id: str) -> Bill:
        existing = self.active_draft(owner_id, chat_id)
        if existing:
            return existing
        bill = Bill(owner_id=owner_id, chat_id=chat_id, status="DRAFT")
        self.session.add(bill)
        self.session.flush()
        return bill

    def _get_draft(self, bill_id: str, owner_id: str) -> Bill:
        bill = self.session.scalar(
            select(Bill)
            .where(Bill.id == bill_id, Bill.owner_id == owner_id)
            .options(selectinload(Bill.items))
        )
        if not bill:
            raise NotFoundError("Bill not found")
        if bill.status != "DRAFT":
            raise BusinessRuleError(f"Bill is {bill.status.lower()}, not editable")
        return bill

    def add_item(self, bill_id: str, owner_id: str, product_query: str, quantity) -> Bill:
        qty = Decimal(str(quantity))
        if qty <= 0:
            raise BusinessRuleError("Quantity must be positive")
        bill = self._get_draft(bill_id, owner_id)
        product = self.inventory.resolve_product(product_query)

        item = next((i for i in bill.items if i.product_id == product.id), None)
        requested = qty + (item.quantity if item else Decimal("0"))
        if requested > product.stock_quantity:
            raise InsufficientStockError(product.name, requested, product.stock_quantity)

        tax = inclusive_tax_breakdown(product.sell_price, requested, product.gst_rate)
        if item:
            item.quantity = requested
            item.unit_price = product.sell_price
            item.gst_rate = product.gst_rate
            item.taxable_value = tax.taxable_value
            item.cgst_amount = tax.cgst_amount
            item.sgst_amount = tax.sgst_amount
            item.line_total = tax.line_total
        else:
            item = BillItem(
                bill=bill,
                product=product,
                product_name=product.name,
                hsn_code=product.hsn_code,
                quantity=requested,
                unit_price=product.sell_price,
                gst_rate=product.gst_rate,
                taxable_value=tax.taxable_value,
                cgst_amount=tax.cgst_amount,
                sgst_amount=tax.sgst_amount,
                line_total=tax.line_total,
            )
            self.session.add(item)
        self.session.flush()
        return self.preview(bill.id, owner_id)

    def update_item(self, bill_id: str, owner_id: str, product_query: str, quantity) -> Bill:
        qty = Decimal(str(quantity))
        if qty <= 0:
            raise BusinessRuleError("Quantity must be positive; use remove for deletion")
        bill = self._get_draft(bill_id, owner_id)
        product = self.inventory.resolve_product(product_query)
        item = next((i for i in bill.items if i.product_id == product.id), None)
        if not item:
            raise NotFoundError(f"{product.name} is not in this bill")
        if qty > product.stock_quantity:
            raise InsufficientStockError(product.name, qty, product.stock_quantity)
        tax = inclusive_tax_breakdown(product.sell_price, qty, product.gst_rate)
        item.quantity = qty
        item.unit_price = product.sell_price
        item.gst_rate = product.gst_rate
        item.taxable_value = tax.taxable_value
        item.cgst_amount = tax.cgst_amount
        item.sgst_amount = tax.sgst_amount
        item.line_total = tax.line_total
        self.session.flush()
        return self.preview(bill.id, owner_id)

    def remove_item(self, bill_id: str, owner_id: str, product_query: str) -> Bill:
        bill = self._get_draft(bill_id, owner_id)
        product = self.inventory.resolve_product(product_query)
        item = next((i for i in bill.items if i.product_id == product.id), None)
        if not item:
            raise NotFoundError(f"{product.name} is not in this bill")
        self.session.delete(item)
        self.session.flush()
        return self.preview(bill.id, owner_id)

    def set_payment(
        self, bill_id: str, owner_id: str, payment_mode: str, payment_reference: str | None = None
    ) -> Bill:
        bill = self._get_draft(bill_id, owner_id)
        mode = payment_mode.upper()
        if mode not in {"CASH", "UPI", "CARD", "KHATA"}:
            raise BusinessRuleError("Payment mode must be CASH, UPI, CARD, or KHATA")
        bill.payment_mode = mode
        bill.payment_reference = payment_reference
        self.session.flush()
        return bill

    def preview(self, bill_id: str, owner_id: str) -> Bill:
        bill = self.session.scalar(
            select(Bill)
            .where(Bill.id == bill_id, Bill.owner_id == owner_id)
            .options(selectinload(Bill.items))
        )
        if not bill:
            raise NotFoundError("Bill not found")
        bill.taxable_total = money(sum((i.taxable_value for i in bill.items), Decimal("0")))
        bill.tax_total = money(
            sum((i.cgst_amount + i.sgst_amount for i in bill.items), Decimal("0"))
        )
        bill.grand_total = money(sum((i.line_total for i in bill.items), Decimal("0")))
        self.session.flush()
        return bill

    def finalize(
        self,
        bill_id: str,
        owner_id: str,
        *,
        idempotency_key: str | None = None,
        customer_name: str | None = None,
    ) -> Bill:
        bill = self.session.scalar(
            select(Bill)
            .where(Bill.id == bill_id, Bill.owner_id == owner_id)
            .options(selectinload(Bill.items))
        )
        if not bill:
            raise NotFoundError("Bill not found")
        if bill.status == "FINALIZED":
            return bill
        if bill.status != "DRAFT":
            raise BusinessRuleError(f"Cannot finalize a {bill.status.lower()} bill")
        if not bill.items:
            raise BusinessRuleError("Cannot finalize an empty bill")
        if not bill.payment_mode:
            raise BusinessRuleError("Payment mode is required before finalizing")
        if idempotency_key:
            existing = self.session.scalar(
                select(Bill).where(Bill.idempotency_key == idempotency_key)
            )
            if existing:
                return existing

        product_ids = [item.product_id for item in bill.items]
        locked_products = list(
            self.session.scalars(
                select(Product).where(Product.id.in_(product_ids)).with_for_update()
            )
        )
        products = {p.id: p for p in locked_products}

        for item in bill.items:
            product = products.get(item.product_id)
            if not product:
                raise NotFoundError(f"Product for {item.product_name} no longer exists")
            if item.unit_price < product.cost_price:
                raise BusinessRuleError(f"Cannot sell {product.name} below cost")
            if item.quantity > product.stock_quantity:
                raise InsufficientStockError(product.name, item.quantity, product.stock_quantity)

        self.preview(bill.id, owner_id)
        now = datetime.now(UTC)
        for item in bill.items:
            product = products[item.product_id]
            product.stock_quantity -= item.quantity
            self.session.add(
                StockMovement(
                    product_id=product.id,
                    movement_type="SALE",
                    quantity=-item.quantity,
                    reference_type="BILL",
                    reference_id=bill.id,
                )
            )

        bill.status = "FINALIZED"
        bill.finalized_at = now
        bill.idempotency_key = idempotency_key
        bill.invoice_number = f"INV-{now:%Y%m%d}-{bill.id[:8].upper()}"

        if bill.payment_mode == "KHATA":
            if not customer_name:
                raise BusinessRuleError("Customer name is required for a khata bill")
            khata = KhataService(self.session)
            customer = khata.get_or_create_customer(owner_id, customer_name)
            bill.customer_id = customer.id
            khata.add_credit(
                owner_id,
                customer_name,
                bill.grand_total,
                note=f"Invoice {bill.invoice_number}",
                bill_id=bill.id,
            )

        self.session.flush()
        return bill
