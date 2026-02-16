"""
Test scenarios for ezmon-nocov integration testing.

Each scenario defines:
- name: Human-readable name
- description: What we're testing
- modifications: List of file modifications to make
- expected_selected: Individual tests that SHOULD run
- expected_deselected: Individual tests that should NOT run

## AST-Based Checksums

Ezmon-nocov computes file checksums using AST (Abstract Syntax Tree) parsing:

1. Parse source code into AST (comments are automatically excluded)
2. Strip docstrings from the AST
3. Compute CRC32 checksum of the AST dump

### Changes that DO NOT trigger test re-runs:
- Comments (any # comments) - AST parsing ignores them
- Docstrings (module, class, function) - explicitly stripped
- Whitespace changes - AST normalizes whitespace

### Changes that DO trigger test re-runs:
- Function/method body changes
- Import statement changes
- Class/function signature changes
- Module-level code (constants, globals)

## File-Level Granularity

When a file's checksum changes, ALL tests that import that file re-run.
This is less precise than coverage-based tracking but:
- ~3x faster on first run (no coverage.py overhead)
- Never misses affected tests (conservative, always correct)
- Simpler implementation

## Test Dependencies

For file-level tracking, what matters is which files each test imports:

test_calculator.py imports:
  - src/calculator.py
  - src/math_utils.py (via calculator)

test_math_utils.py imports:
  - src/math_utils.py

test_string_utils.py imports:
  - src/string_utils.py

test_formatter.py imports:
  - src/formatter.py
  - src/string_utils.py (via formatter)
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
# With file-level tracking, ALL tests importing math_utils.py are selected.
# This includes test_calculator.py tests (via calculator.py -> math_utils.py)
# and test_math_utils.py tests (direct import).
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_math_utils",
    description="Change math_utils.py - all tests importing it are selected (file-level)",
    modifications=[
        Modification(
            file="src/math_utils.py",
            action="replace",
            target="return a + b",
            content="result = a + b\n    return result",
        )
    ],
    # Selected: ALL tests that import math_utils.py (directly or transitively)
    expected_selected=ALL_CALC_TESTS + ALL_MATH_TESTS,
    # No deselection with file-level granularity - all importers run
    expected_deselected=[],
))


# -----------------------------------------------------------------------------
# Scenario: Modify string_utils.uppercase()
# With file-level tracking, ALL tests importing string_utils.py are selected.
# This includes test_formatter.py tests (via formatter.py -> string_utils.py)
# and test_string_utils.py tests (direct import).
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_string_utils",
    description="Change string_utils.py - all tests importing it are selected (file-level)",
    modifications=[
        Modification(
            file="src/string_utils.py",
            action="replace",
            target="return s.upper()",
            content="upper_result = s.upper()\n    return upper_result",
        )
    ],
    # Selected: ALL tests that import string_utils.py (directly or transitively)
    expected_selected=ALL_FMT_TESTS + ALL_STR_TESTS,
    # No deselection with file-level granularity - all importers run
    expected_deselected=[],
))


# -----------------------------------------------------------------------------
# Scenario: Modify calculator.clear_history()
# With file-level tracking, ALL tests importing calculator.py are selected.
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_calculator_only",
    description="Change calculator.py - all tests importing it are selected (file-level)",
    modifications=[
        Modification(
            file="src/calculator.py",
            action="replace",
            # Use full function to avoid matching __init__ which also has self.history = []
            target="def clear_history(self):\n        self.history = []",
            content="def clear_history(self):\n        self.history = list()",
        )
    ],
    # All tests importing calculator.py
    expected_selected=ALL_CALC_TESTS,
    expected_deselected=[],
))


# -----------------------------------------------------------------------------
# Scenario: Modify formatter.set_style()
# With file-level tracking, ALL tests importing formatter.py are selected.
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_formatter_only",
    description="Change formatter.py - all tests importing it are selected (file-level)",
    modifications=[
        Modification(
            file="src/formatter.py",
            action="replace",
            # Use full function to avoid matching __init__ which also has self.style = style
            target="def set_style(self, style):\n        self.style = style",
            content="def set_style(self, style):\n        self.style = str(style)",
        )
    ],
    # All tests importing formatter.py
    expected_selected=ALL_FMT_TESTS,
    expected_deselected=[],
))


# -----------------------------------------------------------------------------
# Scenario: Modify test file only
# With file-level tracking, ALL tests in the modified test file run.
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_test_only",
    description="Change test_math_utils.py - all tests in that file run (file-level)",
    modifications=[
        Modification(
            file="tests/test_math_utils.py",
            action="replace",
            target="assert add(2, 3) == 5",
            content="result = add(2, 3)\n        assert result == 5",
        )
    ],
    # All tests in test_math_utils.py
    expected_selected=ALL_MATH_TESTS,
    expected_deselected=[],
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
# Scenario: Comment-only changes
# Comments are stripped by AST parsing, so NO tests should run
# -----------------------------------------------------------------------------
register(Scenario(
    name="comment_only_change",
    description="Add/change comments only - no tests should run (AST ignores comments)",
    modifications=[
        Modification(
            file="src/math_utils.py",
            action="replace",
            target="def add(a, b):",
            content="# This is a new comment about the add function\ndef add(a, b):",
        )
    ],
    expected_selected=[],
    expected_deselected=[],  # Not checking deselection, just that nothing is selected
))


# -----------------------------------------------------------------------------
# Scenario: Docstring-only changes
# Docstrings are stripped before checksum, so NO tests should run
# -----------------------------------------------------------------------------
register(Scenario(
    name="docstring_only_change",
    description="Change docstrings only - no tests should run (docstrings stripped)",
    modifications=[
        Modification(
            file="src/math_utils.py",
            action="replace",
            target='def add(a, b):\n    return a + b',
            content='def add(a, b):\n    """Add two numbers together and return the sum."""\n    return a + b',
        )
    ],
    expected_selected=[],
    expected_deselected=[],  # Not checking deselection, just that nothing is selected
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
# With file-level tracking, ALL tests importing modified files are selected.
# -----------------------------------------------------------------------------
register(Scenario(
    name="multiple_modifications",
    description="Change math_utils.py and string_utils.py - all tests importing them selected (file-level)",
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
    # All tests importing math_utils.py or string_utils.py
    expected_selected=ALL_CALC_TESTS + ALL_MATH_TESTS + ALL_FMT_TESTS + ALL_STR_TESTS,
    expected_deselected=[],
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
# Non-Python file dependency tracking
# Tests that read config.json via open() are tracked as dependencies.
# Ezmon intercepts file I/O and tracks git-committed non-Python files.
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_config_file",
    description="Change config.json - tests that read it should run",
    modifications=[
        Modification(
            file="config.json",
            action="replace",
            target='"threshold": 50',
            content='"threshold": 75',
        )
    ],
    # All tests in test_config_reader.py read config.json via load_config()
    expected_selected=ALL_CONFIG_TESTS,
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
# Module-level change affecting all importers
# When MODULE-LEVEL code changes (constants, imports, class definitions),
# ALL tests that import the module should be selected - even tests that
# don't call any functions from the module.
#
# This is different from function body changes:
# - Function body change → only tests calling that function are selected
# - Module-level change → ALL tests importing the module are selected
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_import_only_module_level",
    description="Module-level change - ALL tests importing the module are selected, tests in OTHER files are NOT",
    modifications=[
        Modification(
            file="src/import_only.py",
            action="replace",
            target='MODULE_CONSTANT = "import_only_module"',
            content='MODULE_CONSTANT = "import_only_module_changed"',
        )
    ],
    # ALL tests in test_import_only.py should be selected because they all
    # import the module, and module-level code changes affect the module-level block
    # that ALL importers depend on (captured during collection).
    expected_selected=ALL_IMPORT_TESTS,
    # CRITICAL: Tests in OTHER test files that do NOT import import_only.py
    # should NOT be selected. This verifies cross-file isolation.
    expected_deselected=[
        # Tests from test_calculator.py - imports calculator.py, not import_only.py
        TEST_CALC_ADD,
        TEST_CALC_SUBTRACT,
        TEST_CALC_MULTIPLY,
        # Tests from test_formatter.py - imports formatter.py, not import_only.py
        TEST_FMT_UPPER,
        TEST_FMT_LOWER,
        TEST_FMT_TITLE,
        # Tests from test_math_utils.py - imports math_utils.py, not import_only.py
        TEST_MATH_ADD_POS,
        TEST_MATH_ADD_NEG,
        TEST_MATH_ADD_MIX,
        # Tests from test_string_utils.py - imports string_utils.py, not import_only.py
        TEST_STR_UPPER_LOW,
        TEST_STR_UPPER_MIX,
        TEST_STR_LOWER_UP,
        TEST_STR_LOWER_MIX,
    ],
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

# test_dynamic_loader.py tests (dynamic imports via importlib.import_module)
TEST_DYNAMIC_MATH_ADD = "tests/test_dynamic_loader.py::TestDynamicMathImport::test_dynamic_add"
TEST_DYNAMIC_COMPUTE = "tests/test_dynamic_loader.py::TestDynamicMathImport::test_compute_with_dynamic_import"
TEST_DYNAMIC_CAPITALIZE = "tests/test_dynamic_loader.py::TestDynamicStringImport::test_dynamic_capitalize"
TEST_DYNAMIC_FORMAT = "tests/test_dynamic_loader.py::TestDynamicStringImport::test_format_with_dynamic_import"

ALL_DYNAMIC_TESTS = [
    TEST_DYNAMIC_MATH_ADD, TEST_DYNAMIC_COMPUTE,
    TEST_DYNAMIC_CAPITALIZE, TEST_DYNAMIC_FORMAT,
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


# =============================================================================
# DYNAMIC IMPORT SCENARIOS
# These test that importlib.import_module() with string arguments is tracked
# =============================================================================

# -----------------------------------------------------------------------------
# Scenario: Modify math_utils.add() and verify dynamic imports are tracked
# With file-level tracking, ALL tests importing math_utils.py are selected.
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_dynamic_import_dependency",
    description="Dynamic imports - all tests importing math_utils.py selected (file-level)",
    modifications=[
        Modification(
            file="src/math_utils.py",
            action="replace",
            target="return a + b",
            content="sum_result = a + b\n    return sum_result",
        )
    ],
    # All tests importing math_utils.py (including dynamic imports)
    expected_selected=[
        TEST_DYNAMIC_MATH_ADD,
        TEST_DYNAMIC_COMPUTE,
        # All regular tests that import math_utils.py
    ] + ALL_CALC_TESTS + ALL_MATH_TESTS,
    expected_deselected=[],
))


# =============================================================================
# COLLECTION-TIME EXECUTION SCENARIOS
# Tests for functions executed at module level during collection
# =============================================================================

# test_collection_executed.py tests
TEST_COLL_USES_COMPUTED = "tests/test_collection_executed.py::TestUsingComputedValues::test_uses_computed_value"
TEST_COLL_USES_STRING = "tests/test_collection_executed.py::TestUsingComputedValues::test_uses_computed_string"
TEST_COLL_USES_BOTH = "tests/test_collection_executed.py::TestUsingComputedValues::test_uses_both_computed"
TEST_COLL_USES_STATIC = "tests/test_collection_executed.py::TestUsingStaticConstant::test_uses_static_constant"
TEST_COLL_COMPUTED_PLUS_STATIC = "tests/test_collection_executed.py::TestUsingStaticConstant::test_computed_plus_static"
TEST_COLL_CALLS_HELPER = "tests/test_collection_executed.py::TestCallingHelper::test_calls_helper"
TEST_COLL_NOT_CALLS_HELPER = "tests/test_collection_executed.py::TestCallingHelper::test_does_not_call_helper"

ALL_COLLECTION_EXECUTED_TESTS = [
    TEST_COLL_USES_COMPUTED, TEST_COLL_USES_STRING, TEST_COLL_USES_BOTH,
    TEST_COLL_USES_STATIC, TEST_COLL_COMPUTED_PLUS_STATIC,
    TEST_COLL_CALLS_HELPER, TEST_COLL_NOT_CALLS_HELPER,
]


# -----------------------------------------------------------------------------
# Scenario: Modify compute_constant() which is executed at module level
# Since COMPUTED_VALUE = compute_constant() runs during collection,
# ALL tests in that file depend on compute_constant() body.
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_collection_time_function",
    description="Collection-time execution - ALL tests selected when module-level executed function changes",
    modifications=[
        Modification(
            file="src/collection_executed.py",
            action="replace",
            target="def compute_constant():\n    \"\"\"A function that might be called at module level during import.\n\n    If a test file does:\n        COMPUTED_VALUE = compute_constant()\n    at module level, then all tests in that file depend on this function.\n    \"\"\"\n    return 42",
            content="def compute_constant():\n    \"\"\"A function that might be called at module level during import.\n\n    If a test file does:\n        COMPUTED_VALUE = compute_constant()\n    at module level, then all tests in that file depend on this function.\n    \"\"\"\n    result = 42\n    return result",
        )
    ],
    # ALL tests in test_collection_executed.py should be selected because
    # compute_constant() is executed at module level (during collection).
    # This makes it a common dependency for ALL tests in the file.
    expected_selected=ALL_COLLECTION_EXECUTED_TESTS,
    expected_deselected=[],
))


# -----------------------------------------------------------------------------
# Scenario: Modify helper_not_at_module_level() which is NOT executed at module level
# With file-level tracking, ALL tests importing collection_executed.py are selected.
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_helper_not_collection_time",
    description="Non-collection function - all tests importing file selected (file-level)",
    modifications=[
        Modification(
            file="src/collection_executed.py",
            action="replace",
            target="def helper_not_at_module_level():\n    \"\"\"A function NOT called at module level in any test file.\n\n    Only tests that explicitly call this function depend on it.\n    \"\"\"\n    return \"helper_result\"",
            content="def helper_not_at_module_level():\n    \"\"\"A function NOT called at module level in any test file.\n\n    Only tests that explicitly call this function depend on it.\n    \"\"\"\n    result = \"helper_result\"\n    return result",
        )
    ],
    # All tests importing collection_executed.py
    expected_selected=ALL_COLLECTION_EXECUTED_TESTS,
    expected_deselected=[],
))


# =============================================================================
# CLASS RE-EXPORT SCENARIOS (from package import ClassName)
# Tests that classes imported via package re-exports are tracked correctly
#
# This tests the pattern used by pandas, django, etc.:
#   from pandas import Series  # Series defined in pandas.core.series
#   from src.models import User  # User defined in src/models/user.py
#
# BUG: Currently the tracker checks hasattr(User, '__file__') which is False
# for classes. It should also check User.__module__ to find the defining module.
# =============================================================================

# test_user_only.py tests (imports ONLY User)
TEST_USER_ONLY_CREATE = "tests/test_user_only.py::TestUserOnly::test_create_user"
TEST_USER_ONLY_DISPLAY = "tests/test_user_only.py::TestUserOnly::test_user_display"

# test_product_only.py tests (imports ONLY Product)
TEST_PRODUCT_ONLY_CREATE = "tests/test_product_only.py::TestProductOnly::test_create_product"
TEST_PRODUCT_ONLY_PRICE = "tests/test_product_only.py::TestProductOnly::test_product_price"

ALL_USER_ONLY_TESTS = [TEST_USER_ONLY_CREATE, TEST_USER_ONLY_DISPLAY]
ALL_PRODUCT_ONLY_TESTS = [TEST_PRODUCT_ONLY_CREATE, TEST_PRODUCT_ONLY_PRICE]


# -----------------------------------------------------------------------------
# Scenario: from package import ClassName - track to defining module
#
# Setup:
# - test_user_only.py does: `from src.models import User`
# - test_product_only.py does: `from src.models import Product`
# - User is defined in src/models/user.py
# - Product is defined in src/models/product.py
#
# When user.py changes:
# - test_user_only.py tests SHOULD run (imports User from user.py)
# - test_product_only.py tests SHOULD ALSO run because src/models/__init__.py
#   imports BOTH user.py and product.py, so Python executes user.py even when
#   tests only import Product from the package.
#
# This reflects the core rule: never deselect files that Python actually
# imports during test execution (package __init__ side effects included).
# -----------------------------------------------------------------------------
register(Scenario(
    name="from_package_import_class",
    description="from package import ClassName - only tests importing User run when user.py changes",
    modifications=[
        Modification(
            file="src/models/user.py",
            action="replace",
            target="return self.name",
            content="display = self.name\n        return display",
        )
    ],
    # Both User-only and Product-only tests run because package __init__ imports both
    expected_selected=ALL_USER_ONLY_TESTS + ALL_PRODUCT_ONLY_TESTS,
    expected_deselected=[],
))


# -----------------------------------------------------------------------------
# Scenario: Modify product.py - verify Product class is tracked correctly
# Same pattern as above but for the Product class.
#
# Note: Due to Python's package import semantics with __init__.py:
# - test_product_only.py tests SHOULD run (imports Product from product.py)
# - test_user_only.py tests SHOULD ALSO run because src/models/__init__.py
#   imports BOTH user.py and product.py, so Python executes product.py even
#   when tests only import User from the package.
#
# This behavior is symmetric with from_package_import_class.
# -----------------------------------------------------------------------------
register(Scenario(
    name="from_package_import_class_product",
    description="from package import ClassName - tests run when product.py changes (via __init__.py)",
    modifications=[
        Modification(
            file="src/models/product.py",
            action="replace",
            target='return f"${self.price:.2f}"',
            content='formatted = f"${self.price:.2f}"\n        return formatted',
        )
    ],
    # Both User-only and Product-only tests run because package __init__ imports both
    expected_selected=ALL_USER_ONLY_TESTS + ALL_PRODUCT_ONLY_TESTS,
    expected_deselected=[],
))


# =============================================================================
# CHANGE DETECTION VERIFICATION SCENARIOS
# These scenarios verify that file changes are properly detected by the
# bitmap-based schema. The previous refactoring updated writes but not reads,
# causing change detection to fail.
# =============================================================================

# -----------------------------------------------------------------------------
# Scenario: Verify change detection works end-to-end
# This is a fundamental sanity check that changes to source files trigger
# the appropriate test selection. If this fails, the bitmap schema read
# operations are still pointing at the old empty tables.
#
# Note: We use a real code change (not a comment) because comments are
# stripped by AST parsing and don't trigger checksum changes.
# -----------------------------------------------------------------------------
register(Scenario(
    name="verify_change_detection",
    description="Verify that file changes are actually detected end-to-end",
    modifications=[
        Modification(
            file="src/math_utils.py",
            action="append",
            content="\n\ndef _change_detection_trigger():\n    return 42\n"
        )
    ],
    # All tests that depend on math_utils.py should be selected
    expected_selected=ALL_CALC_TESTS + ALL_MATH_TESTS,
    # String utils tests should NOT be selected (no dependency on math_utils)
    expected_deselected=ALL_STR_TESTS,
))
