"""
Module that consumes globals from app_globals.

This demonstrates the common pattern where functions use global
configuration values without explicitly receiving them as parameters.
"""

from src.app_globals import (
    APP_NAME,
    APP_VERSION,
    MAX_ITEMS,
    MIN_ITEMS,
    DEFAULT_BATCH_SIZE,
    WARNING_THRESHOLD,
    ERROR_THRESHOLD,
    ENABLE_CACHING,
    CACHE_ENABLED,
)


def get_app_info():
    """Return application info using globals."""
    return f"{APP_NAME} v{APP_VERSION}"


def validate_item_count(count):
    """Validate item count against global limits."""
    if count < MIN_ITEMS:
        return False, f"Count must be at least {MIN_ITEMS}"
    if count > MAX_ITEMS:
        return False, f"Count must be at most {MAX_ITEMS}"
    return True, "Valid"


def calculate_batches(total_items, batch_size=None):
    """Calculate number of batches using default from globals."""
    if batch_size is None:
        batch_size = DEFAULT_BATCH_SIZE
    if batch_size <= 0:
        return 0
    return (total_items + batch_size - 1) // batch_size


def check_threshold(value):
    """Check value against warning and error thresholds."""
    if value >= ERROR_THRESHOLD:
        return "error"
    elif value >= WARNING_THRESHOLD:
        return "warning"
    return "ok"


def get_cache_status():
    """Return caching status based on globals."""
    if not ENABLE_CACHING:
        return "caching_disabled"
    if CACHE_ENABLED:
        return "cache_active"
    return "cache_inactive"


class ConfigurableProcessor:
    """A processor that uses global configuration."""

    def __init__(self):
        self.max_items = MAX_ITEMS
        self.batch_size = DEFAULT_BATCH_SIZE

    def can_process(self, items):
        """Check if we can process the given items."""
        return len(items) <= self.max_items

    def process_in_batches(self, items):
        """Process items in batches."""
        results = []
        for i in range(0, len(items), self.batch_size):
            batch = items[i:i + self.batch_size]
            results.append(f"Processed batch of {len(batch)} items")
        return results
