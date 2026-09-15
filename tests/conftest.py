from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Product


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    try:
        yield db
        db.rollback()
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def maggi(session: Session) -> Product:
    product = Product(
        sku="MAGGI-70G",
        name="Maggi 70g",
        brand="Maggi",
        product_type="PACKAGED",
        base_unit="packet",
        pack_size=Decimal("70"),
        pack_unit="g",
        cost_price=Decimal("12.00"),
        sell_price=Decimal("14.00"),
        mrp=Decimal("14.00"),
        gst_rate=Decimal("12.00"),
        hsn_code="1902",
        stock_quantity=Decimal("10.000"),
        reorder_level=Decimal("3.000"),
    )
    session.add(product)
    session.flush()
    return product
