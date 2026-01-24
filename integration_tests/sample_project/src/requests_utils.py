"""Utilities that use ONLY the requests package."""

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


def fetch_data_with_requests(url):
    """Fetch data using requests library.

    Requires: requests
    """
    if not HAS_REQUESTS:
        raise ImportError("requests not installed")
    response = requests.get(url)
    return response.json()
