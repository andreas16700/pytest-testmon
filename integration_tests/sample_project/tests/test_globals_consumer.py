"""Tests for globals_consumer module.

These tests exercise functions that use global configuration values.
The key insight is that these tests should be affected when app_globals.py
changes, because the functions they test depend on those globals.

Pattern being tested:
1. test_globals_consumer.py imports from globals_consumer
2. globals_consumer imports from app_globals
3. Changes to app_globals should affect these tests

This is a transitive dependency that coverage.py might miss because:
- app_globals is imported when globals_consumer is first imported
- By the time individual tests run, app_globals is already in sys.modules
- Coverage doesn't see the import happen during the test
"""

from src.globals_consumer import (
    get_app_info,
    validate_item_count,
    calculate_batches,
    check_threshold,
    get_cache_status,
    ConfigurableProcessor,
)


class TestAppInfo:
    """Tests for get_app_info function."""

    def test_app_info_format(self):
        """Test that app info is formatted correctly."""
        info = get_app_info()
        assert "SampleApp" in info
        assert "v" in info

    def test_app_info_contains_version(self):
        """Test that app info contains version."""
        info = get_app_info()
        assert "1.0.0" in info


class TestValidateItemCount:
    """Tests for validate_item_count function."""

    def test_valid_count(self):
        """Test that valid count passes validation."""
        valid, msg = validate_item_count(50)
        assert valid is True
        assert msg == "Valid"

    def test_count_below_minimum(self):
        """Test that count below minimum fails."""
        valid, msg = validate_item_count(0)
        assert valid is False
        assert "at least" in msg

    def test_count_above_maximum(self):
        """Test that count above maximum fails."""
        valid, msg = validate_item_count(150)
        assert valid is False
        assert "at most" in msg

    def test_boundary_minimum(self):
        """Test minimum boundary value."""
        valid, _ = validate_item_count(1)
        assert valid is True

    def test_boundary_maximum(self):
        """Test maximum boundary value."""
        valid, _ = validate_item_count(100)
        assert valid is True


class TestCalculateBatches:
    """Tests for calculate_batches function."""

    def test_exact_batches(self):
        """Test when items divide evenly into batches."""
        # Default batch size is 10
        assert calculate_batches(100) == 10

    def test_partial_batch(self):
        """Test when there's a partial batch."""
        assert calculate_batches(25) == 3  # 10 + 10 + 5

    def test_single_batch(self):
        """Test when all items fit in one batch."""
        assert calculate_batches(5) == 1

    def test_custom_batch_size(self):
        """Test with custom batch size."""
        assert calculate_batches(100, batch_size=25) == 4

    def test_zero_items(self):
        """Test with zero items."""
        assert calculate_batches(0) == 0


class TestCheckThreshold:
    """Tests for check_threshold function."""

    def test_below_warning(self):
        """Test value below warning threshold."""
        assert check_threshold(0.5) == "ok"

    def test_at_warning(self):
        """Test value at warning threshold."""
        assert check_threshold(0.8) == "warning"

    def test_between_thresholds(self):
        """Test value between warning and error."""
        assert check_threshold(0.9) == "warning"

    def test_at_error(self):
        """Test value at error threshold."""
        assert check_threshold(0.95) == "error"

    def test_above_error(self):
        """Test value above error threshold."""
        assert check_threshold(1.0) == "error"


class TestCacheStatus:
    """Tests for get_cache_status function."""

    def test_cache_status(self):
        """Test cache status returns expected value."""
        status = get_cache_status()
        # Based on globals: ENABLE_CACHING=True, DEBUG_MODE=False
        # So CACHE_ENABLED = True and True = True
        assert status in ["cache_active", "cache_inactive", "caching_disabled"]


class TestConfigurableProcessor:
    """Tests for ConfigurableProcessor class."""

    def test_can_process_within_limit(self):
        """Test processing items within limit."""
        processor = ConfigurableProcessor()
        assert processor.can_process(list(range(50))) is True

    def test_can_process_at_limit(self):
        """Test processing items at limit."""
        processor = ConfigurableProcessor()
        assert processor.can_process(list(range(100))) is True

    def test_cannot_process_over_limit(self):
        """Test processing items over limit."""
        processor = ConfigurableProcessor()
        assert processor.can_process(list(range(150))) is False

    def test_process_in_batches(self):
        """Test batch processing."""
        processor = ConfigurableProcessor()
        results = processor.process_in_batches(list(range(25)))
        assert len(results) == 3  # 10 + 10 + 5

    def test_process_single_batch(self):
        """Test processing single batch."""
        processor = ConfigurableProcessor()
        results = processor.process_in_batches(list(range(5)))
        assert len(results) == 1
