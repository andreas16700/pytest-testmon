"""Pure Python utilities - NO external dependencies."""


def pure_python_function(data):
    """Function that uses NO external dependencies.

    This function only uses Python stdlib.
    """
    return sum(data) / len(data) if data else 0
