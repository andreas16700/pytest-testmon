"""
Tests that ONLY import User, not Product.

This tests the `from package import ClassName` pattern where:
- Test imports: `from src.models import User`
- User is defined in: src/models/user.py
- Test should be re-run when user.py changes
- Test should NOT be re-run when product.py changes
"""

from src.models import User


class TestUserOnly:
    """Tests that only use the User class."""

    def test_create_user(self):
        """Test creating a user."""
        user = User("Test", "test@example.com")
        assert user.name == "Test"

    def test_user_display(self):
        """Test user display name."""
        user = User("Display", "display@test.com")
        assert user.get_display_name() == "Display"
