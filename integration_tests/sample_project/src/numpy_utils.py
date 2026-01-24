"""Utilities that use ONLY the numpy package."""

try:
    import numpy
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


def calculate_with_numpy(data):
    """Perform calculations using numpy.

    Requires: numpy
    """
    if not HAS_NUMPY:
        raise ImportError("numpy not installed")
    arr = numpy.array(data)
    return {
        "mean": float(numpy.mean(arr)),
        "sum": float(numpy.sum(arr)),
        "max": float(numpy.max(arr)),
    }
