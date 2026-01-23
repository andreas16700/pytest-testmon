"""Tests for import_only - demonstrates import without execution limitation.

LIMITATION: When a module is imported but specific functions/methods are NOT
called during test execution, those functions are NOT in the test's fingerprint.

Behavior:
- Changing MODULE_CONSTANT: Affects tests using it (module-level code executes on import)
- Changing class_attribute: Affects tests using ImportedClass (class body executes on import)
- Changing method_that_might_not_be_called(): Only affects test that actually CALLS it
- Changing helper_function(): Only affects test that actually CALLS it

Test breakdown:
- test_uses_constant: Uses MODULE_CONSTANT (module-level)
- test_uses_class_attribute: Uses ImportedClass.class_attribute (class-level)
- test_calls_method: Actually CALLS ImportedClass().method_that_might_not_be_called()
- test_imports_but_not_calls: Imports ImportedClass but doesn't call its methods
- test_calls_helper: Actually CALLS helper_function()
"""

from src.import_only import (
    MODULE_CONSTANT,
    DERIVED_CONSTANT,
    ImportedClass,
    imported_function,
    helper_function,
)


class TestModuleLevelCode:
    """Tests that use module-level code (executed on import)."""

    def test_uses_constant(self):
        """Test that uses MODULE_CONSTANT.

        This test will be affected if MODULE_CONSTANT changes because
        module-level code executes on import.
        """
        assert MODULE_CONSTANT == "import_only_module"

    def test_uses_derived_constant(self):
        """Test that uses DERIVED_CONSTANT.

        This depends on both MODULE_CONSTANT and the derivation logic.
        """
        assert DERIVED_CONSTANT == "import_only_module_derived"


class TestClassLevelCode:
    """Tests that use class-level code (executed on import)."""

    def test_uses_class_attribute(self):
        """Test that uses class attribute.

        Class body executes on import, so this test depends on
        ImportedClass class-level code.
        """
        assert ImportedClass.class_attribute == "class_level"

    def test_instantiates_class_only(self):
        """Test that creates instance but doesn't call methods.

        LIMITATION: This test imports ImportedClass and instantiates it,
        but does NOT call any methods. Changes to the method bodies
        will NOT affect this test's fingerprint.
        """
        obj = ImportedClass()
        # Just checking the object exists, not calling any methods
        assert obj is not None
        assert hasattr(obj, "method_that_might_not_be_called")


class TestMethodExecution:
    """Tests that actually execute methods."""

    def test_calls_method(self):
        """Test that actually calls the method.

        This test WILL be affected if method_that_might_not_be_called() changes
        because we actually execute it.
        """
        obj = ImportedClass()
        result = obj.method_that_might_not_be_called()
        assert result == "method executed"

    def test_calls_another_method(self):
        """Test that calls another_uncalled_method.

        This test WILL be affected if another_uncalled_method() changes.
        """
        obj = ImportedClass()
        result = obj.another_uncalled_method()
        assert result == "another method executed"


class TestFunctionExecution:
    """Tests for function-level execution tracking."""

    def test_calls_imported_function(self):
        """Test that calls imported_function.

        This test WILL be affected if imported_function() changes.
        """
        result = imported_function()
        assert result == "function executed"

    def test_calls_helper_function(self):
        """Test that calls helper_function.

        This test WILL be affected if helper_function() changes.
        """
        result = helper_function(21)
        assert result == 42

    def test_imports_but_not_calls_function(self):
        """Test that imports but doesn't call helper_function.

        LIMITATION: This test has helper_function in scope but never calls it.
        Changes to helper_function body will NOT affect this test.
        """
        # We imported helper_function at the top, but don't call it here
        # Instead, just verify it exists
        assert callable(helper_function)
        # This test does NOT depend on helper_function's implementation
