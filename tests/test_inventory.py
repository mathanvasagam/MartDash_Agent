from decimal import Decimal

import pytest

from app.domain.errors import AmbiguousProductError, BusinessRuleError
from app.domain.inventory import InventoryService


def test_receive_stock_and_low_stock(session, maggi):
    service = InventoryService(session)
    service.receive_stock("MAGGI-70G", 5, reference="TEST")
    assert maggi.stock_quantity == Decimal("15.000")
    assert maggi not in service.low_stock()


def test_refuses_below_cost_product(session):
    service = InventoryService(session)
    with pytest.raises(BusinessRuleError, match="below cost"):
        service.add_product(
            sku="BAD-1",
            name="Bad Product",
            cost_price=20,
            sell_price=19,
            gst_rate=5,
            hsn_code="0000",
        )


def test_ambiguous_product_requires_clarification(session):
    service = InventoryService(session)
    common = dict(cost_price=30, sell_price=35, gst_rate=0, hsn_code="1101")
    service.add_product(sku="ATTA-1", name="Aashirvaad Atta 5kg", **common)
    service.add_product(
        sku="ATTA-2", name="Loose Atta", product_type="LOOSE", base_unit="kg", **common
    )
    with pytest.raises(AmbiguousProductError):
        service.resolve_product("atta")


def test_retrieve_context_ranks_products_and_returns_low_stock(session, maggi):
    service = InventoryService(session)
    maggi.reorder_level = Decimal("12")
    result = service.retrieve_context("Maggi stock", limit=4)

    assert [product.sku for product in result["products"]] == ["MAGGI-70G"]
    assert [product.sku for product in result["low_stock"]] == ["MAGGI-70G"]
