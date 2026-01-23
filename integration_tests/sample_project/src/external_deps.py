"""External dependencies module - demonstrates external module dependency limitation.

This module uses external dependencies. ezmon tracks changes to the installed
packages via the environment's system_packages hash. When ANY package changes
(added, removed, or updated), ALL tests are marked as affected.

LIMITATION: The granularity is "all or nothing" - there's no tracking of
which tests use which external packages.
"""

# For demonstration purposes, we use optional external imports
# These could be any pip packages

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


def fetch_data_with_requests(url):
    """Fetch data using requests library.

    Requires: requests
    """
    if not HAS_REQUESTS:
        raise ImportError("requests not installed")
    response = requests.get(url)
    return response.json()


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


def process_with_both(url, multiplier):
    """Process that uses both requests and numpy.

    Requires: requests, numpy
    """
    if not HAS_REQUESTS or not HAS_NUMPY:
        raise ImportError("Both requests and numpy required")
    # Simulate: fetch data, process with numpy
    data = [1, 2, 3, 4, 5]  # Would normally come from fetch_data_with_requests
    arr = numpy.array(data) * multiplier
    return arr.tolist()


def pure_python_function(data):
    """Function that uses NO external dependencies.

    This function only uses Python stdlib.
    """
    return sum(data) / len(data) if data else 0
