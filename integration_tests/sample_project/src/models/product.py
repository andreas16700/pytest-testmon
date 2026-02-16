"""Product model - defines the Product class."""


class Product:
    """A product in the catalog."""

    def __init__(self, name: str, price: float):
        self.name = name
        self.price = price

    def get_formatted_price(self) -> str:
        """Return price formatted as currency."""
        return f"${self.price:.2f}"

    def apply_discount(self, percent: float) -> float:
        """Apply a percentage discount and return new price."""
        return self.price * (1 - percent / 100)
