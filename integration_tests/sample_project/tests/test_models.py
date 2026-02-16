"""
Tests for the models package.

These tests import classes via the package (e.g., `from src.models import User`)
rather than directly from submodules (e.g., `from src.models.user import User`).

This pattern is common in libraries like pandas:
    from pandas import Series  # Series is defined in pandas.core.series

The import tracker should track User back to src/models/user.py
so that changes to user.py trigger these tests to re-run.
"""

from src.models import User, Product


class TestUser:
    """Tests for the User class."""

    def test_user_creation(self):
        """Test creating a user."""
        user = User("Alice", "alice@example.com")
        assert user.name == "Alice"
        assert user.email == "alice@example.com"

    def test_user_display_name(self):
        """Test the display name method."""
        user = User("Bob", "bob@test.org")
        assert user.get_display_name() == "Bob"

    def test_user_email_domain(self):
        """Test extracting email domain."""
        user = User("Charlie", "charlie@company.io")
        assert user.get_email_domain() == "company.io"


class TestProduct:
    """Tests for the Product class."""

    def test_product_creation(self):
        """Test creating a product."""
        product = Product("Widget", 29.99)
        assert product.name == "Widget"
        assert product.price == 29.99

    def test_product_formatted_price(self):
        """Test price formatting."""
        product = Product("Gadget", 15.5)
        assert product.get_formatted_price() == "$15.50"

    def test_product_discount(self):
        """Test applying a discount."""
        product = Product("Item", 100.0)
        discounted = product.apply_discount(20)
        assert discounted == 80.0
