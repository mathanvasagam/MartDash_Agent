import re
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Product, StockMovement
from app.domain.errors import AmbiguousProductError, BusinessRuleError, NotFoundError


class InventoryService:
    def __init__(self, session: Session):
        self.session = session

    def search_products(self, query: str, limit: int = 8) -> list[Product]:
        q = query.strip()
        stmt = (
            select(Product)
            .where(
                Product.active.is_(True),
                or_(
                    func.lower(Product.name).contains(q.lower()),
                    func.lower(Product.sku).contains(q.lower()),
                    func.lower(func.coalesce(Product.brand, "")).contains(q.lower()),
                ),
            )
            .order_by(Product.name)
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def retrieve_context(self, query: str, limit: int = 8) -> dict:
        """Retrieve ranked catalog context for grounding conversational answers."""
        terms = [term for term in re.findall(r"[a-z0-9]+", query.lower()) if len(term) > 1]
        if not terms:
            return {"products": [], "low_stock": []}

        products = list(
            self.session.scalars(
                select(Product)
                .where(Product.active.is_(True))
                .order_by(Product.name)
                .limit(200)
            )
        )

        def score(product: Product) -> int:
            fields = (
                product.name.lower(),
                (product.brand or "").lower(),
                product.sku.lower(),
                product.product_type.lower(),
                product.base_unit.lower(),
                (product.pack_unit or "").lower(),
                product.hsn_code.lower(),
            )
            return sum(1 for term in terms if any(term in field for field in fields))

        ranked = sorted(
            ((score(product), product) for product in products),
            key=lambda item: (-item[0], item[1].name.lower()),
        )
        matches = [product for points, product in ranked if points > 0][:limit]
        stock_terms = {"stock", "available", "inventory", "reorder", "running", "out"}
        low_stock = (
            [product for product in products if product.stock_quantity <= product.reorder_level]
            if stock_terms.intersection(terms)
            else []
        )
        return {"products": matches, "low_stock": low_stock[:limit]}

    def resolve_product(self, query: str) -> Product:
        q = query.strip()
        exact = self.session.scalar(
            select(Product).where(
                Product.active.is_(True),
                or_(func.lower(Product.sku) == q.lower(), func.lower(Product.name) == q.lower()),
            )
        )
        if exact:
            return exact

        matches = self.search_products(q)
        if not matches:
            raise NotFoundError(f"No product found for '{query}'")
        if len(matches) > 1:
            raise AmbiguousProductError(query, [f"{p.name} ({p.sku})" for p in matches])
        return matches[0]

    def add_product(
        self,
        *,
        sku: str,
        name: str,
        cost_price,
        sell_price,
        gst_rate,
        hsn_code: str,
        brand: str | None = None,
        product_type: str = "PACKAGED",
        base_unit: str = "piece",
        pack_size=None,
        pack_unit: str | None = None,
        mrp=None,
        reorder_level=0,
    ) -> Product:
        cost = Decimal(str(cost_price))
        sell = Decimal(str(sell_price))
        mrp_value = Decimal(str(mrp)) if mrp is not None else None
        if cost < 0 or sell < 0:
            raise BusinessRuleError("Prices cannot be negative")
        if sell < cost:
            raise BusinessRuleError("Selling below cost is not allowed")
        if mrp_value is not None and sell > mrp_value:
            raise BusinessRuleError("Selling price cannot exceed MRP")
        if self.session.scalar(select(Product).where(func.lower(Product.sku) == sku.lower())):
            raise BusinessRuleError(f"SKU '{sku}' already exists")

        product = Product(
            sku=sku.strip(),
            name=name.strip(),
            brand=brand.strip() if brand else None,
            product_type=product_type.upper(),
            base_unit=base_unit.lower(),
            pack_size=Decimal(str(pack_size)) if pack_size is not None else None,
            pack_unit=pack_unit.lower() if pack_unit else None,
            cost_price=cost,
            sell_price=sell,
            mrp=mrp_value,
            gst_rate=Decimal(str(gst_rate)),
            hsn_code=hsn_code.strip(),
            reorder_level=Decimal(str(reorder_level)),
            stock_quantity=Decimal("0"),
        )
        self.session.add(product)
        self.session.flush()
        return product

    def receive_stock(
        self, query: str, quantity, *, cost_price=None, mrp=None, reference: str | None = None
    ) -> Product:
        qty = Decimal(str(quantity))
        if qty <= 0:
            raise BusinessRuleError("Received quantity must be positive")

        product = self.resolve_product(query)
        if cost_price is not None:
            new_cost = Decimal(str(cost_price))
            if new_cost > product.sell_price:
                raise BusinessRuleError(
                    f"New cost ₹{new_cost} exceeds current sell price ₹{product.sell_price}; update pricing first"
                )
            product.cost_price = new_cost
        if mrp is not None:
            new_mrp = Decimal(str(mrp))
            if product.sell_price > new_mrp:
                raise BusinessRuleError("Current selling price exceeds the supplied MRP")
            product.mrp = new_mrp

        product.stock_quantity += qty
        self.session.add(
            StockMovement(
                product_id=product.id,
                movement_type="STOCK_IN",
                quantity=qty,
                reference_type="RECEIPT",
                reference_id=reference,
            )
        )
        self.session.flush()
        return product

    def low_stock(self) -> list[Product]:
        return list(
            self.session.scalars(
                select(Product)
                .where(Product.active.is_(True), Product.stock_quantity <= Product.reorder_level)
                .order_by(Product.stock_quantity.asc(), Product.name.asc())
            )
        )
