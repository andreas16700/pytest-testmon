"""Tests for config_reader - demonstrates file dependency limitation.

LIMITATION: These tests depend on config.json, but changes to config.json
will NOT trigger test re-runs because ezmon only tracks Python source
file dependencies, not data file dependencies.

If config.json changes:
- test_load_config_returns_dict → SHOULD re-run but WON'T
- test_get_setting_returns_value → SHOULD re-run but WON'T
- test_threshold_filtering → SHOULD re-run but WON'T
"""

from src.config_reader import (
    load_config,
    get_setting,
    get_feature_enabled,
    get_threshold,
    process_with_config,
)


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_config_returns_dict(self):
        """Test that load_config returns a dictionary."""
        config = load_config()
        assert isinstance(config, dict)
        # This assertion depends on config.json content!
        assert "setting" in config

    def test_load_config_has_threshold(self):
        """Test that config has threshold."""
        config = load_config()
        assert "threshold" in config


class TestGetSetting:
    """Tests for get_setting function."""

    def test_get_setting_returns_value(self):
        """Test getting a specific setting."""
        # This test's result depends on config.json!
        setting = get_setting("setting")
        assert setting == "production"

    def test_get_setting_default(self):
        """Test default value for missing key."""
        result = get_setting("nonexistent", default="fallback")
        assert result == "fallback"

    def test_get_value_from_config(self):
        """Test getting the 'value' setting."""
        # This test's result depends on config.json!
        value = get_setting("value")
        assert value == 42


class TestFeatureFlags:
    """Tests for feature flag functionality."""

    def test_feature_enabled_dark_mode(self):
        """Test dark_mode feature flag."""
        # This test depends on config.json features!
        assert get_feature_enabled("dark_mode") is True

    def test_feature_disabled_notifications(self):
        """Test notifications feature flag."""
        # This test depends on config.json features!
        assert get_feature_enabled("notifications") is False

    def test_feature_unknown_returns_false(self):
        """Test unknown feature returns False."""
        assert get_feature_enabled("unknown_feature") is False


class TestThreshold:
    """Tests for threshold functionality."""

    def test_get_threshold(self):
        """Test getting threshold value."""
        # This test depends on config.json!
        threshold = get_threshold()
        assert threshold == 50

    def test_process_with_config_filters(self):
        """Test processing data with config threshold."""
        # This test depends on config.json threshold value!
        data = [10, 30, 50, 70, 100]
        result = process_with_config(data)
        # With threshold=50, should keep [50, 70, 100]
        assert result == [50, 70, 100]

    def test_process_with_config_empty(self):
        """Test processing empty data."""
        result = process_with_config([])
        assert result == []
