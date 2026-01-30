"""Module with functions that may be executed at collection time.

When a test file imports this module and EXECUTES a function at module level
(not inside a test), that function becomes a dependency for ALL tests in
that file - because it runs during collection before any individual test.

Example in test file:
    from collection_executed import compute_constant

    # This executes during collection (module level)
    COMPUTED_VALUE = compute_constant()

    def test_something():
        # COMPUTED_VALUE was computed before this test ran
        assert COMPUTED_VALUE == expected

If compute_constant() changes, ALL tests in that file should be selected
because the computed value affects the entire test file's environment.
"""


def compute_constant():
    """A function that might be called at module level during import.

    If a test file does:
        COMPUTED_VALUE = compute_constant()
    at module level, then all tests in that file depend on this function.
    """
    return 42


def compute_another():
    """Another function that might be called at module level.

    Tests that call this at module level depend on it.
    """
    return "computed_string"


def helper_not_at_module_level():
    """A function NOT called at module level in any test file.

    Only tests that explicitly call this function depend on it.
    """
    return "helper_result"


# Module-level constant for comparison
STATIC_CONSTANT = 100
