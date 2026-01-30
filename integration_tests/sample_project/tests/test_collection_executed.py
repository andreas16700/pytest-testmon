"""Tests that EXECUTE functions at module level (during collection).

This demonstrates that when a function is executed at module level (not inside
a test function), ALL tests in the file depend on that function's body.

Key pattern:
    COMPUTED_VALUE = compute_constant()  # Executes during collection!

When compute_constant() body changes:
- ALL tests in this file should be selected
- The function was executed during collection, making it a common dependency

When helper_not_at_module_level() body changes:
- ONLY test_calls_helper should be selected
- That function is NOT executed at module level
"""

from src.collection_executed import (
    compute_constant,
    compute_another,
    helper_not_at_module_level,
    STATIC_CONSTANT,
)


# ============================================================================
# MODULE-LEVEL EXECUTION - These run during collection!
# All tests in this file depend on these function bodies.
# ============================================================================
COMPUTED_VALUE = compute_constant()  # Executes during collection!
COMPUTED_STRING = compute_another()  # Also executes during collection!


class TestUsingComputedValues:
    """Tests that use the module-level computed values."""

    def test_uses_computed_value(self):
        """Uses COMPUTED_VALUE which was computed at collection time.

        If compute_constant() changes, this test should be selected because
        COMPUTED_VALUE = compute_constant() ran during collection.
        """
        assert COMPUTED_VALUE == 42

    def test_uses_computed_string(self):
        """Uses COMPUTED_STRING which was computed at collection time.

        If compute_another() changes, this test should be selected.
        """
        assert COMPUTED_STRING == "computed_string"

    def test_uses_both_computed(self):
        """Uses both computed values.

        Should be selected if either compute_constant() or compute_another() changes.
        """
        assert COMPUTED_VALUE == 42
        assert COMPUTED_STRING == "computed_string"


class TestUsingStaticConstant:
    """Tests that use the static constant (not computed at collection time)."""

    def test_uses_static_constant(self):
        """Uses STATIC_CONSTANT from the module.

        This test depends on the module-level block (where STATIC_CONSTANT is defined),
        NOT on any function body. If STATIC_CONSTANT changes, this should be selected.
        """
        assert STATIC_CONSTANT == 100

    def test_computed_plus_static(self):
        """Uses both computed and static values.

        Depends on:
        - compute_constant() body (via COMPUTED_VALUE)
        - Module-level block (via STATIC_CONSTANT)
        """
        assert COMPUTED_VALUE + STATIC_CONSTANT == 142


class TestCallingHelper:
    """Tests that explicitly call helper_not_at_module_level().

    This function is NOT executed at module level, so only tests that
    call it directly depend on its body.
    """

    def test_calls_helper(self):
        """Actually calls helper_not_at_module_level().

        This test should be selected if helper_not_at_module_level() body changes.
        """
        result = helper_not_at_module_level()
        assert result == "helper_result"

    def test_does_not_call_helper(self):
        """Does NOT call helper_not_at_module_level().

        This test should NOT be selected if only helper_not_at_module_level() changes.
        It SHOULD be selected if compute_constant() or compute_another() changes
        (because those are executed at module level).
        """
        # Just use the computed values, don't call helper
        assert COMPUTED_VALUE > 0
