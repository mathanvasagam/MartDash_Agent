class DomainError(Exception):
    """Base class for expected business-rule failures."""


class NotFoundError(DomainError):
    pass


class AmbiguousProductError(DomainError):
    def __init__(self, query: str, matches: list[str]):
        super().__init__(f"Product '{query}' is ambiguous")
        self.query = query
        self.matches = matches


class InsufficientStockError(DomainError):
    def __init__(self, product: str, requested, available):
        super().__init__(
            f"Insufficient stock for {product}: requested {requested}, available {available}"
        )
        self.product = product
        self.requested = requested
        self.available = available


class BusinessRuleError(DomainError):
    pass
