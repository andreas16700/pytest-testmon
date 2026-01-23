"""
Test scenarios for ezmon integration testing.

Each scenario defines:
- name: Human-readable name
- description: What we're testing
- modifications: List of file modifications to make
- expected_selected: Test files that SHOULD run
- expected_deselected: Test files that should NOT run

IMPORTANT: Ezmon uses AST-based fingerprinting, so modifications must
change the code structure, not just comments!
"""

from dataclasses import dataclass
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
    expected_selected: List[str]  # Test files that should run
    expected_deselected: List[str]  # Test files that should NOT run


# =============================================================================
# SCENARIOS
# =============================================================================

SCENARIOS: Dict[str, Scenario] = {}


def register(scenario: Scenario) -> Scenario:
    """Register a scenario by name."""
    SCENARIOS[scenario.name] = scenario
    return scenario


# -----------------------------------------------------------------------------
# Scenario: Modify math_utils
# Expected: test_math_utils AND test_calculator should run (dependency chain)
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_math_utils",
    description="Change math_utils.add() - should affect math_utils and calculator tests",
    modifications=[
        Modification(
            file="src/math_utils.py",
            action="replace",
            # Change actual code, not just a comment (AST must change!)
            target="return a + b",
            content="result = a + b\n    return result",
        )
    ],
    expected_selected=[
        "test_math_utils.py",
        "test_calculator.py",
    ],
    expected_deselected=[
        "test_string_utils.py",
        "test_formatter.py",
    ],
))


# -----------------------------------------------------------------------------
# Scenario: Modify string_utils
# Expected: test_string_utils AND test_formatter should run
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_string_utils",
    description="Change string_utils.uppercase() - should affect string_utils and formatter tests",
    modifications=[
        Modification(
            file="src/string_utils.py",
            action="replace",
            # Change actual code (AST must change!)
            target="return s.upper()",
            content="upper_result = s.upper()\n    return upper_result",
        )
    ],
    expected_selected=[
        "test_string_utils.py",
        "test_formatter.py",
    ],
    expected_deselected=[
        "test_math_utils.py",
        "test_calculator.py",
    ],
))


# -----------------------------------------------------------------------------
# Scenario: Modify calculator only
# Expected: only test_calculator should run (not test_math_utils)
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_calculator_only",
    description="Change calculator.Calculator.clear_history() - should only affect calculator tests",
    modifications=[
        Modification(
            file="src/calculator.py",
            action="replace",
            # Change actual code (use list() instead of [])
            target="self.history = []",
            content="self.history = list()",
        )
    ],
    expected_selected=[
        "test_calculator.py",
    ],
    expected_deselected=[
        "test_math_utils.py",
        "test_string_utils.py",
        "test_formatter.py",
    ],
))


# -----------------------------------------------------------------------------
# Scenario: Modify formatter only
# Expected: only test_formatter should run (not test_string_utils)
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_formatter_only",
    description="Change formatter.Formatter.set_style() - should only affect formatter tests",
    modifications=[
        Modification(
            file="src/formatter.py",
            action="replace",
            target="self.style = style",
            content="self.style = str(style)",
        )
    ],
    expected_selected=[
        "test_formatter.py",
    ],
    expected_deselected=[
        "test_math_utils.py",
        "test_string_utils.py",
        "test_calculator.py",
    ],
))


# -----------------------------------------------------------------------------
# Scenario: Modify test file only
# Expected: only that test file should run
# -----------------------------------------------------------------------------
register(Scenario(
    name="modify_test_only",
    description="Change only test_math_utils.py - should only affect that test file",
    modifications=[
        Modification(
            file="tests/test_math_utils.py",
            action="replace",
            # Add an actual code change (extra variable)
            target="assert add(2, 3) == 5",
            content="result = add(2, 3)\n        assert result == 5",
        )
    ],
    expected_selected=[
        "test_math_utils.py",
    ],
    expected_deselected=[
        "test_calculator.py",
        "test_string_utils.py",
        "test_formatter.py",
    ],
))


# -----------------------------------------------------------------------------
# Scenario: No changes
# Expected: nothing should run (all deselected)
# -----------------------------------------------------------------------------
register(Scenario(
    name="no_changes",
    description="No modifications - all tests should be deselected",
    modifications=[],
    expected_selected=[],
    expected_deselected=[
        "test_math_utils.py",
        "test_calculator.py",
        "test_string_utils.py",
        "test_formatter.py",
    ],
))


# -----------------------------------------------------------------------------
# Scenario: Add new test
# Expected: only new test should run
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
        "test_new.py",
    ],
    expected_deselected=[
        "test_math_utils.py",
        "test_calculator.py",
        "test_string_utils.py",
        "test_formatter.py",
    ],
))


# -----------------------------------------------------------------------------
# Scenario: Delete a source file (break imports)
# This tests graceful handling of broken imports
# -----------------------------------------------------------------------------
# Note: This scenario is tricky because deleting math_utils.py would break
# calculator.py imports. We'll skip this for now.


# -----------------------------------------------------------------------------
# Scenario: Multiple modifications
# Expected: affected tests from both changes should run
# -----------------------------------------------------------------------------
register(Scenario(
    name="multiple_modifications",
    description="Change both math_utils and string_utils - both dependency chains affected",
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
    expected_selected=[
        "test_math_utils.py",
        "test_calculator.py",
        "test_string_utils.py",
        "test_formatter.py",
    ],
    expected_deselected=[],
))
