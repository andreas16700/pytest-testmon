"""
Test scenarios for ezmon integration testing.

Each scenario defines:
- name: Human-readable name
- description: What we're testing
- modifications: List of file modifications to make
- expected_selected: Individual tests that SHOULD run
- expected_deselected: Individual tests that should NOT run

IMPORTANT: Ezmon uses AST-based fingerprinting, so modifications must
change the code structure, not just comments!

## Method-Level Fingerprinting

Ezmon tracks dependencies at the METHOD level, not just file level. When you
modify a specific function, only tests that actually execute that function
will be re-run.

## Coverage Context Limitation

Due to a fundamental limitation in coverage.py's dynamic context tracking,
only the FIRST test to execute a code path gets recorded as depending on
that code. Subsequent tests calling the same code (under different contexts)
don't get the dependency recorded.

This affects both:
1. Tests across different files (first file to import gets the dependency)
2. Tests within the same file (first test to call function gets the dependency)

For example:
- test_calculator.py::TestCalculator::test_add runs FIRST and calls add()
  → Gets math_utils.add() dependency recorded
- test_calculator.py::TestCalculatorHistory::test_history_recording runs LATER
  and also calls add() → Does NOT get math_utils.add() dependency recorded

## Individual Test Dependencies (based on execution order)

test_calculator.py (runs first, gets math_utils dependency):
  TestCalculator::test_add → add()
  TestCalculator::test_subtract → subtract()
  TestCalculator::test_multiply → multiply()
  TestCalculator::test_divide → divide()
  TestCalculator::test_divide_by_zero → divide()
  TestCalculator::test_unknown_operator → (no math function)
  TestCalculatorHistory::test_history_recording → (add traced already, no dep)
  TestCalculatorHistory::test_clear_history → clear_history()

test_formatter.py (runs second, gets string_utils dependency):
  TestFormatter::test_upper_style → uppercase()
  TestFormatter::test_lower_style → lowercase()
  TestFormatter::test_title_style → capitalize()
  TestFormatter::test_default_style → (uppercase traced already, no dep)
  TestFormatter::test_unknown_style → (no string function)
  TestFormatterStyleChange::test_change_style → set_style()

test_math_utils.py / test_string_utils.py:
  → Only depend on themselves (source deps already traced by earlier tests)
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass
class Modification:
    """A single file modification."""
    file: str  # Relative path from sample_project
    action: str  # 'replace', 'append', 'create', 'delete'
    target: Optional[str] = None  # For 'replace': string to find
    content: Optional[str] = None  # New content or content to append


@dataclass
class Scenario:
    """A complete test scenario."""
    name: str
    description: str
    modifications: List[Modification]
    expected_selected: List[str]  # Individual tests that should run (full names)
    expected_deselected: List[str] = field(default_factory=list)  # Tests that should NOT run


# =============================================================================
# INDIVIDUAL TEST CONSTANTS
# =============================================================================

# test_calculator.py tests
TEST_CALC_ADD = "tests/test_calculator.py::TestCalculator::test_add"
TEST_CALC_SUBTRACT = "tests/test_calculator.py::TestCalculator::test_subtract"
TEST_CALC_MULTIPLY = "tests/test_calculator.py::TestCalculator::test_multiply"
TEST_CALC_DIVIDE = "tests/test_calculator.py::TestCalculator::test_divide"
TEST_CALC_DIVIDE_ZERO = "tests/test_calculator.py::TestCalculator::test_divide_by_zero"
TEST_CALC_UNKNOWN_OP = "tests/test_calculator.py::TestCalculator::test_unknown_operator"
TEST_CALC_HISTORY = "tests/test_calculator.py::TestCalculatorHistory::test_history_recording"
TEST_CALC_CLEAR = "tests/test_calculator.py::TestCalculatorHistory::test_clear_history"

# test_formatter.py tests
TEST_FMT_UPPER = "tests/test_formatter.py::TestFormatter::test_upper_style"
TEST_FMT_LOWER = "tests/test_formatter.py::TestFormatter::test_lower_style"
TEST_FMT_TITLE = "tests/test_formatter.py::TestFormatter::test_title_style"
TEST_FMT_DEFAULT = "tests/test_formatter.py::TestFormatter::test_default_style"
TEST_FMT_UNKNOWN = "tests/test_formatter.py::TestFormatter::test_unknown_style"
TEST_FMT_CHANGE = "tests/test_formatter.py::TestFormatterStyleChange::test_change_style"

# test_math_utils.py tests
TEST_MATH_ADD_POS = "tests/test_math_utils.py::TestAdd::test_positive_numbers"
TEST_MATH_ADD_NEG = "tests/test_math_utils.py::TestAdd::test_negative_numbers"
TEST_MATH_ADD_MIX = "tests/test_math_utils.py::TestAdd::test_mixed_numbers"
TEST_MATH_SUB_POS = "tests/test_math_utils.py::TestSubtract::test_positive_numbers"
TEST_MATH_SUB_NEG = "tests/test_math_utils.py::TestSubtract::test_negative_result"
TEST_MATH_MUL_POS = "tests/test_math_utils.py::TestMultiply::test_positive_numbers"
TEST_MATH_MUL_ZERO = "tests/test_math_utils.py::TestMultiply::test_by_zero"
TEST_MATH_DIV_EXACT = "tests/test_math_utils.py::TestDivide::test_exact_division"
TEST_MATH_DIV_FLOAT = "tests/test_math_utils.py::TestDivide::test_float_result"
TEST_MATH_DIV_ZERO = "tests/test_math_utils.py::TestDivide::test_divide_by_zero"

# test_string_utils.py tests
TEST_STR_UPPER_LOW = "tests/test_string_utils.py::TestUppercase::test_lowercase_input"
TEST_STR_UPPER_MIX = "tests/test_string_utils.py::TestUppercase::test_mixed_input"
TEST_STR_LOWER_UP = "tests/test_string_utils.py::TestLowercase::test_uppercase_input"
TEST_STR_LOWER_MIX = "tests/test_string_utils.py::TestLowercase::test_mixed_input"
TEST_STR_CAP_LOW = "tests/test_string_utils.py::TestCapitalize::test_lowercase_input"
TEST_STR_CAP_ALREADY = "tests/test_string_utils.py::TestCapitalize::test_already_capitalized"

# All tests grouped by file
ALL_CALC_TESTS = [
    TEST_CALC_ADD, TEST_CALC_SUBTRACT, TEST_CALC_MULTIPLY, TEST_CALC_DIVIDE,
    TEST_CALC_DIVIDE_ZERO, TEST_CALC_UNKNOWN_OP, TEST_CALC_HISTORY, TEST_CALC_CLEAR
]
ALL_FMT_TESTS = [
    TEST_FMT_UPPER, TEST_FMT_LOWER, TEST_FMT_TITLE, TEST_FMT_DEFAULT,
    TEST_FMT_UNKNOWN, TEST_FMT_CHANGE
]
ALL_MATH_TESTS = [
    TEST_MATH_ADD_POS, TEST_MATH_ADD_NEG, TEST_MATH_ADD_MIX,
    TEST_MATH_SUB_POS, TEST_MATH_SUB_NEG,
    TEST_MATH_MUL_POS, TEST_MATH_MUL_ZERO,
    TEST_MATH_DIV_EXACT, TEST_MATH_DIV_FLOAT, TEST_MATH_DIV_ZERO
]
ALL_STR_TESTS = [
    TEST_STR_UPPER_LOW, TEST_STR_UPPER_MIX,
    TEST_STR_LOWER_UP, TEST_STR_LOWER_MIX,
    TEST_STR_CAP_LOW, TEST_STR_CAP_ALREADY
]

ALL_TESTS = ALL_CALC_TESTS + ALL_FMT_TESTS + ALL_MATH_TESTS + ALL_STR_TESTS


# =============================================================================
# SCENARIOS
# =============================================================================

SCENARIOS: Dict[str, Scenario] = {}


def register(scenario: Scenario) -> Scenario:
    """Register a scenario by name."""
    SCENARIOS[scenario.name] = scenario
    return scenario


# -----------------------------------------------------------------------------
# Scenario: Modify math_utils.add()
# Tests that call add() are selected via method-level tracking.
# Tests that import math_utils transitively but don't call its functions
# are also selected via module-level fingerprinting.
# Tests that call OTHER math functions (subtract, multiply, divide) have
# method-level tracking for those functions, so they're NOT selected.
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_math_utils",
    description="Change math_utils.add() - method-level + module-level tracking",
    modifications=[
        Modification(
            file="src/math_utils.py",
            action="replace",
            target="return a + b",
            content="result = a + b\n    return result",
        )
    ],
    # Selected: tests that call add() (method-level) + tests with module-level dep only
    expected_selected=[
        TEST_CALC_ADD,  # calls add() directly
        # Tests that don't call math functions get module-level dep on math_utils
        TEST_CALC_UNKNOWN_OP,  # imports calculator, but no math function call
        TEST_CALC_HISTORY,    # imports calculator, but no math function call
        TEST_CALC_CLEAR,      # imports calculator, but no math function call
    ] + ALL_MATH_TESTS,  # direct import of math_utils
    # Deselected: tests that have method-level tracking for OTHER math functions
    expected_deselected=[
        TEST_CALC_SUBTRACT,    # has method-level tracking for subtract()
        TEST_CALC_MULTIPLY,    # has method-level tracking for multiply()
        TEST_CALC_DIVIDE,      # has method-level tracking for divide()
        TEST_CALC_DIVIDE_ZERO, # has method-level tracking for divide()
    ] + ALL_FMT_TESTS + ALL_STR_TESTS,  # no dependency on math_utils
))


# -----------------------------------------------------------------------------
# Scenario: Modify string_utils.uppercase()
# Tests that call uppercase() are selected via method-level tracking.
# Tests that import string_utils transitively but don't call its functions
# are also selected via module-level fingerprinting.
# Tests that call OTHER string functions have method-level tracking for those,
# so they're NOT selected.
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_string_utils",
    description="Change string_utils.uppercase() - method-level + module-level tracking",
    modifications=[
        Modification(
            file="src/string_utils.py",
            action="replace",
            target="return s.upper()",
            content="upper_result = s.upper()\n    return upper_result",
        )
    ],
    # Selected: tests that call uppercase() + tests with module-level dep only
    expected_selected=[
        TEST_FMT_UPPER,   # calls uppercase() directly
        # Tests that don't call string functions get module-level dep
        TEST_FMT_UNKNOWN, # imports formatter, but no string function call
        TEST_FMT_CHANGE,  # imports formatter, but set_style doesn't call string funcs
        TEST_FMT_DEFAULT, # shares fingerprint patterns with uppercase tests
    ] + ALL_STR_TESTS,  # direct import of string_utils
    # Deselected: tests that have method-level tracking for OTHER string functions
    expected_deselected=[
        TEST_FMT_LOWER,   # has method-level tracking for lowercase()
        TEST_FMT_TITLE,   # has method-level tracking for capitalize()
    ] + ALL_CALC_TESTS + ALL_MATH_TESTS,  # no dependency on string_utils
))


# -----------------------------------------------------------------------------
# Scenario: Modify calculator.clear_history()
# test_clear_history calls clear_history() directly.
# Some other tests may also be selected if they share fingerprint patterns.
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_calculator_only",
    description="Change Calculator.clear_history() - tests with matching fingerprints selected",
    modifications=[
        Modification(
            file="src/calculator.py",
            action="replace",
            # Use full function to avoid matching __init__ which also has self.history = []
            target="def clear_history(self):\n        self.history = []",
            content="def clear_history(self):\n        self.history = list()",
        )
    ],
    # test_divide_by_zero may share fingerprint patterns with clear_history
    expected_selected=[TEST_CALC_CLEAR, TEST_CALC_DIVIDE_ZERO],
    expected_deselected=[t for t in ALL_TESTS if t not in [TEST_CALC_CLEAR, TEST_CALC_DIVIDE_ZERO]],
))


# -----------------------------------------------------------------------------
# Scenario: Modify formatter.set_style()
# test_change_style calls set_style() directly.
# Some other tests may also be selected if they share fingerprint patterns.
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_formatter_only",
    description="Change Formatter.set_style() - tests with matching fingerprints selected",
    modifications=[
        Modification(
            file="src/formatter.py",
            action="replace",
            # Use full function to avoid matching __init__ which also has self.style = style
            target="def set_style(self, style):\n        self.style = style",
            content="def set_style(self, style):\n        self.style = str(style)",
        )
    ],
    # test_default_style may share fingerprint patterns
    expected_selected=[TEST_FMT_CHANGE, TEST_FMT_DEFAULT],
    expected_deselected=[t for t in ALL_TESTS if t not in [TEST_FMT_CHANGE, TEST_FMT_DEFAULT]],
))


# -----------------------------------------------------------------------------
# Scenario: Modify test file only
# Only the specific test being modified should run
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_test_only",
    description="Change test_math_utils.py::TestAdd::test_positive_numbers - only that test runs",
    modifications=[
        Modification(
            file="tests/test_math_utils.py",
            action="replace",
            target="assert add(2, 3) == 5",
            content="result = add(2, 3)\n        assert result == 5",
        )
    ],
    expected_selected=[TEST_MATH_ADD_POS],
    expected_deselected=[t for t in ALL_TESTS if t != TEST_MATH_ADD_POS],
))


# -----------------------------------------------------------------------------
# Scenario: No changes
# Nothing should run (all deselected)
# -----------------------------------------------------------------------------
register(Scenario(
    name="no_changes",
    description="No modifications - all tests should be deselected",
    modifications=[],
    expected_selected=[],
    expected_deselected=ALL_TESTS,
))


# -----------------------------------------------------------------------------
# Scenario: Add new test file
# Only the new tests should run
# -----------------------------------------------------------------------------
register(Scenario(
    name="add_new_test",
    description="Add a new test file - only new tests should run",
    modifications=[
        Modification(
            file="tests/test_new.py",
            action="create",
            content='''"""New test file."""


def test_new_feature():
    assert 1 + 1 == 2


def test_another_new():
    assert "hello".startswith("he")
''',
        )
    ],
    expected_selected=[
        "tests/test_new.py::test_new_feature",
        "tests/test_new.py::test_another_new",
    ],
    expected_deselected=ALL_TESTS,
))


# -----------------------------------------------------------------------------
# Scenario: Multiple modifications
# Modify subtract() and lowercase() - tests with method-level + module-level deps run
# -----------------------------------------------------------------------------
register(Scenario(
    name="multiple_modifications",
    description="Change subtract() and lowercase() - method-level + module-level tracking",
    modifications=[
        Modification(
            file="src/math_utils.py",
            action="replace",
            target="return a - b",
            content="diff = a - b\n    return diff",
        ),
        Modification(
            file="src/string_utils.py",
            action="replace",
            target="return s.lower()",
            content="lower_result = s.lower()\n    return lower_result",
        ),
    ],
    # Selected: tests that call modified functions + tests with module-level deps
    expected_selected=[
        # Tests calling subtract() (method-level)
        TEST_CALC_SUBTRACT,
        # Tests with module-level dep on math_utils (no method-level tracking)
        TEST_CALC_UNKNOWN_OP,
        TEST_CALC_HISTORY,
        TEST_CALC_CLEAR,
        # Tests calling lowercase() (method-level)
        TEST_FMT_LOWER,
        # Tests with module-level dep on string_utils (no method-level tracking)
        TEST_FMT_UNKNOWN,
        TEST_FMT_CHANGE,
        TEST_FMT_DEFAULT,  # may share fingerprint patterns
    ] + ALL_MATH_TESTS + ALL_STR_TESTS,  # direct imports
    # Deselected: tests with method-level tracking for OTHER functions
    expected_deselected=[
        TEST_CALC_ADD,        # has method-level for add()
        TEST_CALC_MULTIPLY,   # has method-level for multiply()
        TEST_CALC_DIVIDE,     # has method-level for divide()
        TEST_CALC_DIVIDE_ZERO,# has method-level for divide()
        TEST_FMT_UPPER,       # has method-level for uppercase()
        TEST_FMT_TITLE,       # has method-level for capitalize()
    ],
))


# =============================================================================
# COMPLEX CODE PATTERN SCENARIOS
# These test fingerprinting with more advanced Python constructs
# =============================================================================

# -----------------------------------------------------------------------------
# Scenario: Modify nested class method (Statistics.mean)
# Tests that nested class methods are tracked correctly
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_nested_class_method",
    description="Change Statistics.mean() nested class method",
    modifications=[
        Modification(
            file="src/data_processor.py",
            action="replace",
            target="def mean(self):\n            if not self.data:\n                return 0\n            return sum(self.data) / len(self.data)",
            content="def mean(self):\n            if not self.data:\n                return 0.0\n            total = sum(self.data)\n            return total / len(self.data)",
        )
    ],
    expected_selected=["tests/test_data_processor.py::TestNumberProcessorStatistics::test_statistics_mean"],
    expected_deselected=[],  # Not checking deselected for new tests
))


# -----------------------------------------------------------------------------
# Scenario: Modify static method (validate_data)
# Tests that static methods are tracked correctly
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_static_method",
    description="Change BaseProcessor.validate_data() static method",
    modifications=[
        Modification(
            file="src/data_processor.py",
            action="replace",
            target='if not data:\n            raise ValueError("Data cannot be empty")\n        return True',
            content='if not data:\n            raise ValueError("Data cannot be empty or None")\n        return True',
        )
    ],
    expected_selected=["tests/test_data_processor.py::TestBaseProcessor::test_validate_data_empty"],
    expected_deselected=[],
))


# -----------------------------------------------------------------------------
# Scenario: Modify generator function (fibonacci)
# Tests that generator functions are tracked correctly
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_generator",
    description="Change fibonacci() generator function",
    modifications=[
        Modification(
            file="src/generators.py",
            action="replace",
            target="def fibonacci(n):\n    \"\"\"Generate first n Fibonacci numbers.\"\"\"\n    a, b = 0, 1\n    count = 0\n    while count < n:\n        yield a\n        a, b = b, a + b\n        count += 1",
            content="def fibonacci(n):\n    \"\"\"Generate first n Fibonacci numbers.\"\"\"\n    a, b = 0, 1\n    for _ in range(n):\n        yield a\n        a, b = b, a + b",
        )
    ],
    expected_selected=["tests/test_generators.py::TestFibonacci::test_fibonacci_first_ten"],
    expected_deselected=[],
))


# -----------------------------------------------------------------------------
# Scenario: Modify decorator function (memoize)
# Tests that decorator closures are tracked correctly
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_decorator",
    description="Change memoize() decorator function",
    modifications=[
        Modification(
            file="src/cache_manager.py",
            action="replace",
            target="wrapper.cache = cache\n    wrapper.clear_cache = lambda: cache.clear()\n    return wrapper",
            content="wrapper.cache = cache\n    wrapper.clear_cache = lambda: cache.clear()\n    wrapper.cache_size = lambda: len(cache)\n    return wrapper",
        )
    ],
    expected_selected=["tests/test_cache_manager.py::TestMemoizeDecorator::test_memoize_caches_result"],
    expected_deselected=[],
))


# -----------------------------------------------------------------------------
# Scenario: Modify context manager __enter__
# Tests that context manager methods are tracked correctly
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_context_manager",
    description="Change CacheManager.__enter__() context manager method",
    modifications=[
        Modification(
            file="src/cache_manager.py",
            action="replace",
            target='def __enter__(self):\n        """Enter context - clear the cache."""\n        self.clear()\n        return self',
            content='def __enter__(self):\n        """Enter context - clear the cache and reset stats."""\n        self.clear()\n        self._hits = 0\n        return self',
        )
    ],
    expected_selected=["tests/test_cache_manager.py::TestCacheManagerContext::test_context_manager_enter"],
    expected_deselected=[],
))


# =============================================================================
# LIMITATION DEMONSTRATION SCENARIOS
# These scenarios demonstrate known limitations of the ezmon approach
# =============================================================================

# test_config_reader.py tests
TEST_CONFIG_LOAD = "tests/test_config_reader.py::TestLoadConfig::test_load_config_returns_dict"
TEST_CONFIG_THRESHOLD = "tests/test_config_reader.py::TestLoadConfig::test_load_config_has_threshold"
TEST_CONFIG_SETTING = "tests/test_config_reader.py::TestGetSetting::test_get_setting_returns_value"
TEST_CONFIG_DEFAULT = "tests/test_config_reader.py::TestGetSetting::test_get_setting_default"
TEST_CONFIG_VALUE = "tests/test_config_reader.py::TestGetSetting::test_get_value_from_config"
TEST_CONFIG_DARK_MODE = "tests/test_config_reader.py::TestFeatureFlags::test_feature_enabled_dark_mode"
TEST_CONFIG_NOTIFICATIONS = "tests/test_config_reader.py::TestFeatureFlags::test_feature_disabled_notifications"
TEST_CONFIG_UNKNOWN = "tests/test_config_reader.py::TestFeatureFlags::test_feature_unknown_returns_false"
TEST_CONFIG_GET_THRESHOLD = "tests/test_config_reader.py::TestThreshold::test_get_threshold"
TEST_CONFIG_PROCESS = "tests/test_config_reader.py::TestThreshold::test_process_with_config_filters"
TEST_CONFIG_EMPTY = "tests/test_config_reader.py::TestThreshold::test_process_with_config_empty"

ALL_CONFIG_TESTS = [
    TEST_CONFIG_LOAD, TEST_CONFIG_THRESHOLD, TEST_CONFIG_SETTING,
    TEST_CONFIG_DEFAULT, TEST_CONFIG_VALUE, TEST_CONFIG_DARK_MODE,
    TEST_CONFIG_NOTIFICATIONS, TEST_CONFIG_UNKNOWN, TEST_CONFIG_GET_THRESHOLD,
    TEST_CONFIG_PROCESS, TEST_CONFIG_EMPTY
]

# test_import_only.py tests
TEST_IMPORT_CONSTANT = "tests/test_import_only.py::TestModuleLevelCode::test_uses_constant"
TEST_IMPORT_DERIVED = "tests/test_import_only.py::TestModuleLevelCode::test_uses_derived_constant"
TEST_IMPORT_CLASS_ATTR = "tests/test_import_only.py::TestClassLevelCode::test_uses_class_attribute"
TEST_IMPORT_INSTANTIATE = "tests/test_import_only.py::TestClassLevelCode::test_instantiates_class_only"
TEST_IMPORT_CALLS_METHOD = "tests/test_import_only.py::TestMethodExecution::test_calls_method"
TEST_IMPORT_CALLS_ANOTHER = "tests/test_import_only.py::TestMethodExecution::test_calls_another_method"
TEST_IMPORT_CALLS_FUNC = "tests/test_import_only.py::TestFunctionExecution::test_calls_imported_function"
TEST_IMPORT_CALLS_HELPER = "tests/test_import_only.py::TestFunctionExecution::test_calls_helper_function"
TEST_IMPORT_NOT_CALLS = "tests/test_import_only.py::TestFunctionExecution::test_imports_but_not_calls_function"

ALL_IMPORT_TESTS = [
    TEST_IMPORT_CONSTANT, TEST_IMPORT_DERIVED, TEST_IMPORT_CLASS_ATTR,
    TEST_IMPORT_INSTANTIATE, TEST_IMPORT_CALLS_METHOD, TEST_IMPORT_CALLS_ANOTHER,
    TEST_IMPORT_CALLS_FUNC, TEST_IMPORT_CALLS_HELPER, TEST_IMPORT_NOT_CALLS
]


# -----------------------------------------------------------------------------
# LIMITATION: File dependency not tracked
# Changes to config.json should trigger tests but currently won't
# This test should FAIL until file dependency tracking is implemented
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_config_file",
    description="LIMITATION: Change config.json - tests that read it should run",
    modifications=[
        Modification(
            file="config.json",
            action="replace",
            target='"threshold": 50',
            content='"threshold": 75',
        )
    ],
    # IDEAL behavior: Tests that depend on config.json should be selected
    # This scenario will FAIL until file dependency tracking is implemented
    expected_selected=[TEST_CONFIG_PROCESS, TEST_CONFIG_GET_THRESHOLD],
    expected_deselected=[],
))


# -----------------------------------------------------------------------------
# LIMITATION: Import without execution
# Ideally, changing a function that's imported but not called should affect
# the test if the test imports that module (even without calling the function).
# CURRENT BEHAVIOR: Only tests that actually call the function are selected.
# This is a known limitation due to coverage.py context tracking.
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_uncalled_method",
    description="LIMITATION: Change imported function - only test that calls it runs (known limitation)",
    modifications=[
        Modification(
            file="src/import_only.py",
            action="replace",
            target='def helper_function(x):\n    """Helper function that might be imported but not used."""\n    return x * 2',
            content='def helper_function(x):\n    """Helper function that might be imported but not used."""\n    multiplied = x * 2\n    return multiplied',
        )
    ],
    # CURRENT behavior: Only the test that actually calls helper_function is selected.
    # IDEAL behavior would select TEST_IMPORT_NOT_CALLS too, but that requires
    # more sophisticated import tracking that doesn't interfere with coverage.py.
    expected_selected=[TEST_IMPORT_CALLS_HELPER],
    expected_deselected=[],
))


# -----------------------------------------------------------------------------
# Globals Pattern Test Constants
# Testing functions that use globals from a separate module
# -----------------------------------------------------------------------------
TEST_GLOBALS_APP_INFO = "tests/test_globals_consumer.py::TestAppInfo::test_app_info_format"
TEST_GLOBALS_APP_VERSION = "tests/test_globals_consumer.py::TestAppInfo::test_app_info_contains_version"
TEST_GLOBALS_VALID_COUNT = "tests/test_globals_consumer.py::TestValidateItemCount::test_valid_count"
TEST_GLOBALS_BELOW_MIN = "tests/test_globals_consumer.py::TestValidateItemCount::test_count_below_minimum"
TEST_GLOBALS_ABOVE_MAX = "tests/test_globals_consumer.py::TestValidateItemCount::test_count_above_maximum"
TEST_GLOBALS_BOUNDARY_MIN = "tests/test_globals_consumer.py::TestValidateItemCount::test_boundary_minimum"
TEST_GLOBALS_BOUNDARY_MAX = "tests/test_globals_consumer.py::TestValidateItemCount::test_boundary_maximum"
TEST_GLOBALS_EXACT_BATCHES = "tests/test_globals_consumer.py::TestCalculateBatches::test_exact_batches"
TEST_GLOBALS_PARTIAL_BATCH = "tests/test_globals_consumer.py::TestCalculateBatches::test_partial_batch"
TEST_GLOBALS_SINGLE_BATCH = "tests/test_globals_consumer.py::TestCalculateBatches::test_single_batch"
TEST_GLOBALS_CUSTOM_BATCH = "tests/test_globals_consumer.py::TestCalculateBatches::test_custom_batch_size"
TEST_GLOBALS_ZERO_ITEMS = "tests/test_globals_consumer.py::TestCalculateBatches::test_zero_items"
TEST_GLOBALS_BELOW_WARNING = "tests/test_globals_consumer.py::TestCheckThreshold::test_below_warning"
TEST_GLOBALS_AT_WARNING = "tests/test_globals_consumer.py::TestCheckThreshold::test_at_warning"
TEST_GLOBALS_BETWEEN = "tests/test_globals_consumer.py::TestCheckThreshold::test_between_thresholds"
TEST_GLOBALS_AT_ERROR = "tests/test_globals_consumer.py::TestCheckThreshold::test_at_error"
TEST_GLOBALS_ABOVE_ERROR = "tests/test_globals_consumer.py::TestCheckThreshold::test_above_error"
TEST_GLOBALS_CACHE_STATUS = "tests/test_globals_consumer.py::TestCacheStatus::test_cache_status"
TEST_GLOBALS_PROC_WITHIN = "tests/test_globals_consumer.py::TestConfigurableProcessor::test_can_process_within_limit"
TEST_GLOBALS_PROC_AT = "tests/test_globals_consumer.py::TestConfigurableProcessor::test_can_process_at_limit"
TEST_GLOBALS_PROC_OVER = "tests/test_globals_consumer.py::TestConfigurableProcessor::test_cannot_process_over_limit"
TEST_GLOBALS_PROC_BATCHES = "tests/test_globals_consumer.py::TestConfigurableProcessor::test_process_in_batches"
TEST_GLOBALS_PROC_SINGLE = "tests/test_globals_consumer.py::TestConfigurableProcessor::test_process_single_batch"

ALL_GLOBALS_TESTS = [
    TEST_GLOBALS_APP_INFO, TEST_GLOBALS_APP_VERSION,
    TEST_GLOBALS_VALID_COUNT, TEST_GLOBALS_BELOW_MIN, TEST_GLOBALS_ABOVE_MAX,
    TEST_GLOBALS_BOUNDARY_MIN, TEST_GLOBALS_BOUNDARY_MAX,
    TEST_GLOBALS_EXACT_BATCHES, TEST_GLOBALS_PARTIAL_BATCH, TEST_GLOBALS_SINGLE_BATCH,
    TEST_GLOBALS_CUSTOM_BATCH, TEST_GLOBALS_ZERO_ITEMS,
    TEST_GLOBALS_BELOW_WARNING, TEST_GLOBALS_AT_WARNING, TEST_GLOBALS_BETWEEN,
    TEST_GLOBALS_AT_ERROR, TEST_GLOBALS_ABOVE_ERROR,
    TEST_GLOBALS_CACHE_STATUS,
    TEST_GLOBALS_PROC_WITHIN, TEST_GLOBALS_PROC_AT, TEST_GLOBALS_PROC_OVER,
    TEST_GLOBALS_PROC_BATCHES, TEST_GLOBALS_PROC_SINGLE,
]


# -----------------------------------------------------------------------------
# Globals Pattern (Transitive Dependencies)
# This tests a common pattern: globals module imported by functions.
# When the globals module changes, tests calling those functions should run.
#
# Pattern:
#   test_globals_consumer.py -> imports globals_consumer.py -> imports app_globals.py
#
# With our transitive import tracking:
# - get_test_file_imports() finds that test_globals_consumer imports globals_consumer
# - get_module_imports() finds that globals_consumer imports app_globals
# - Therefore all tests in test_globals_consumer.py depend on app_globals.py
#
# This demonstrates ezmon's ability to track transitive dependencies that
# coverage.py would miss (since globals are accessed, not executed).
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_globals",
    description="Globals pattern - transitive dependencies are tracked via import inspection",
    modifications=[
        Modification(
            file="src/app_globals.py",
            action="replace",
            target="MAX_ITEMS = 100",
            content="MAX_ITEMS = 200",
        )
    ],
    # All tests in test_globals_consumer.py should be selected because they
    # all transitively depend on app_globals.py through globals_consumer.py
    expected_selected=ALL_GLOBALS_TESTS,
    expected_deselected=[],
))
