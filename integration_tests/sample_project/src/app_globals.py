"""
Application globals - a common pattern where configuration and constants
are stored in a single module and imported by other modules.

This pattern is common in many Python projects:
- Django settings
- Flask config
- Application-wide constants
- Feature flags
"""

# Application configuration
APP_NAME = "SampleApp"
APP_VERSION = "1.0.0"
DEBUG_MODE = False

# Processing limits
MAX_ITEMS = 100
MIN_ITEMS = 1
DEFAULT_BATCH_SIZE = 10

# Thresholds
WARNING_THRESHOLD = 0.8
ERROR_THRESHOLD = 0.95

# Feature flags
ENABLE_CACHING = True
ENABLE_LOGGING = True
ENABLE_METRICS = False

# Computed globals (depend on other globals)
CACHE_ENABLED = ENABLE_CACHING and not DEBUG_MODE
