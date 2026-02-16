"""
Models package - re-exports classes from submodules.

This mimics patterns like:
- `from pandas import Series` (Series defined in pandas.core.series)
- `from django.db import models` (models defined in django.db.models.base)

The import tracker should track these classes back to their defining module.
"""

from src.models.user import User
from src.models.product import Product

__all__ = ['User', 'Product']
