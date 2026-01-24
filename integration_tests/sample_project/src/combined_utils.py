"""Utilities that use BOTH requests and numpy packages."""

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import numpy
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


def process_with_both(url, multiplier):
    """Process that uses both requests and numpy.

    Requires: requests, numpy
    """
    if not HAS_REQUESTS or not HAS_NUMPY:
        raise ImportError("Both requests and numpy required")
    # Simulate: fetch data, process with numpy
    data = [1, 2, 3, 4, 5]  # Would normally come from requests call
    arr = numpy.array(data) * multiplier
    return arr.tolist()
