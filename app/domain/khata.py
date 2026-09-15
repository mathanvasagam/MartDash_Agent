from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Customer, KhataEntry
from app.domain.errors import BusinessRuleError, NotFoundError
from app.domain.gst import money


class KhataService:
    def __init__(self, session: Session):
        self.session = session

    def find_customer(self, owner_id: str, name: str) -> Customer | None:
        return self.session.scalar(
            select(Customer).where(
                Customer.owner_id == owner_id,
                func.lower(Customer.name) == name.strip().lower(),
                Customer.active.is_(True),
            )
        )

    def get_or_create_customer(self, owner_id: str, name: str) -> Customer:
        customer = self.find_customer(owner_id, name)
        if customer:
            return customer
        customer = Customer(owner_id=owner_id, name=name.strip())
        self.session.add(customer)
        self.session.flush()
        return customer

    def balance_for_customer_id(self, customer_id: str) -> Decimal:
        entries = self.session.scalars(
            select(KhataEntry).where(KhataEntry.customer_id == customer_id)
        )
        total = Decimal("0")
        for entry in entries:
            if entry.entry_type == "CREDIT":
                total += entry.amount
            elif entry.entry_type == "PAYMENT":
                total -= entry.amount
        return money(total)

    def balance(self, owner_id: str, name: str) -> Decimal:
        customer = self.find_customer(owner_id, name)
        if not customer:
            raise NotFoundError(f"No khata exists for '{name}'")
        return self.balance_for_customer_id(customer.id)

    def add_credit(
        self, owner_id: str, name: str, amount, note: str | None = None, bill_id: str | None = None
    ) -> Decimal:
        value = money(amount)
        if value <= 0:
            raise BusinessRuleError("Credit amount must be positive")
        customer = self.get_or_create_customer(owner_id, name)
        self.session.add(
            KhataEntry(
                customer_id=customer.id,
                entry_type="CREDIT",
                amount=value,
                note=note,
                bill_id=bill_id,
            )
        )
        self.session.flush()
        return self.balance_for_customer_id(customer.id)

    def record_payment(self, owner_id: str, name: str, amount, note: str | None = None) -> Decimal:
        value = money(amount)
        if value <= 0:
            raise BusinessRuleError("Payment amount must be positive")
        customer = self.find_customer(owner_id, name)
        if not customer:
            raise NotFoundError(f"Cannot settle '{name}': no khata exists")
        balance = self.balance_for_customer_id(customer.id)
        if balance <= 0:
            raise BusinessRuleError(f"{name} has no outstanding balance")
        if value > balance:
            raise BusinessRuleError(f"Payment ₹{value} exceeds outstanding balance ₹{balance}")
        self.session.add(
            KhataEntry(customer_id=customer.id, entry_type="PAYMENT", amount=value, note=note)
        )
        self.session.flush()
        return money(balance - value)
