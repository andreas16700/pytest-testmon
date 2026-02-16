"""
Tests that ONLY import Product, not User.

This tests the `from package import ClassName` pattern where:
- Test imports: `from src.models import Product`
- Product is defined in: src/models/product.py
- Test should be re-run when product.py changes
- Test should NOT be re-run when user.py changes
"""

from src.models import Product


class TestProductOnly:
    """Tests that only use the Product class."""

    def test_create_product(self):
        """Test creating a product."""
        product = Product("Test Product", 9.99)
        assert product.name == "Test Product"

    def test_product_price(self):
        """Test product price formatting."""
        product = Product("Item", 25.00)
        assert product.get_formatted_price() == "$25.00"
