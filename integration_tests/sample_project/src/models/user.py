"""User model - defines the User class."""


class User:
    """A user in the system."""

    def __init__(self, name: str, email: str):
        self.name = name
        self.email = email

    def get_display_name(self) -> str:
        """Return the display name for this user."""
        return self.name

    def get_email_domain(self) -> str:
        """Extract the domain from the email address."""
        return self.email.split('@')[-1]
