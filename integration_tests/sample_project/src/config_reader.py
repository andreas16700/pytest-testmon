"""Config reader module - demonstrates file dependency limitation.

This module reads configuration from a JSON file. Changes to the config
file will NOT trigger test re-runs because ezmon only tracks Python source
file dependencies, not data file dependencies.
"""

import json
import os


CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "config.json")


def load_config():
    """Load configuration from config.json file."""
    if not os.path.exists(CONFIG_FILE):
        return {"setting": "default", "value": 0}

    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def get_setting(key, default=None):
    """Get a specific setting from the config."""
    config = load_config()
    return config.get(key, default)


def get_feature_enabled(feature_name):
    """Check if a feature is enabled in config."""
    config = load_config()
    features = config.get("features", {})
    return features.get(feature_name, False)


def get_threshold():
    """Get the threshold value from config."""
    config = load_config()
    return config.get("threshold", 100)


def process_with_config(data):
    """Process data using config-based threshold."""
    threshold = get_threshold()
    return [x for x in data if x >= threshold]
