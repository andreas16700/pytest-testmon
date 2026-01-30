"""
Module demonstrating dynamic imports using importlib.import_module().

This module loads other modules dynamically at runtime, which tests
that the dependency tracker captures imports even when the module
name is passed as a string variable.
"""

import importlib


def load_module_by_name(module_name: str):
    """
    Dynamically load a module by name and return it.

    Uses importlib.import_module() with a string argument,
    which is a common pattern for plugin systems.
    """
    return importlib.import_module(module_name)


def get_math_add():
    """
    Dynamically import math_utils and return its add function.

    The module name is stored in a variable, not hardcoded in
    the import statement.
    """
    module_name = "src.math_utils"
    math_module = importlib.import_module(module_name)
    return math_module.add


def get_string_capitalize():
    """
    Dynamically import string_utils and return its capitalize function.
    """
    module_name = "src.string_utils"
    string_module = importlib.import_module(module_name)
    return string_module.capitalize


def compute_with_dynamic_import(a: int, b: int) -> int:
    """
    Perform computation using dynamically imported module.

    This function:
    1. Stores module name in a variable
    2. Uses importlib.import_module() to load it
    3. Calls a function from the loaded module
    """
    module_name = "src.math_utils"
    math_module = importlib.import_module(module_name)
    return math_module.add(a, b)


def format_with_dynamic_import(text: str) -> str:
    """
    Format text using dynamically imported string_utils.
    """
    module_name = "src.string_utils"
    string_module = importlib.import_module(module_name)
    return string_module.capitalize(text)
