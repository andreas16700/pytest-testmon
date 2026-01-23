"""Import-only module - demonstrates import without execution limitation.

When a module is imported but no code is actually executed from it,
ezmon may not track the dependency correctly because coverage.py only
reports lines that were actually executed.

Module-level code (imports, class definitions) IS executed on import,
but function/method bodies are NOT executed until called.
"""

# This constant is evaluated at import time
MODULE_CONSTANT = "import_only_module"


# This class is defined at import time (class body executes)
class ImportedClass:
    """A class that might be imported but methods not called."""

    class_attribute = "class_level"

    def method_that_might_not_be_called(self):
        """Method body only executes when called."""
        return "method executed"

    def another_uncalled_method(self):
        """Another method that might not be called."""
        return "another method executed"


def imported_function():
    """Function defined but might not be called."""
    return "function executed"


def helper_function(x):
    """Helper function that might be imported but not used."""
    return x * 2


# Module-level code that executes on import
_initialized = True
DERIVED_CONSTANT = MODULE_CONSTANT + "_derived"
