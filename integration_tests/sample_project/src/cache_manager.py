"""
Cache manager with decorators and context managers.

Demonstrates:
- Context managers (__enter__/__exit__)
- Decorators (function and class decorators)
- Closures
- Higher-order functions
"""

import functools
from contextlib import contextmanager


class CacheManager:
    """A simple cache manager with context manager support."""

    def __init__(self, max_size=100):
        self._cache = {}
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    def __enter__(self):
        """Enter context - clear the cache."""
        self.clear()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context - clear the cache."""
        self.clear()
        return False  # Don't suppress exceptions

    def get(self, key):
        """Get a value from cache."""
        if key in self._cache:
            self._hits += 1
            return self._cache[key]
        self._misses += 1
        return None

    def set(self, key, value):
        """Set a value in cache."""
        if len(self._cache) >= self._max_size:
            # Remove oldest item (simple FIFO)
            oldest = next(iter(self._cache))
            del self._cache[oldest]
        self._cache[key] = value

    def clear(self):
        """Clear the cache."""
        self._cache = {}
        self._hits = 0
        self._misses = 0

    @property
    def stats(self):
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate,
            "size": len(self._cache),
        }


def memoize(func):
    """Decorator to memoize function results."""
    cache = {}

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Create a hashable key from args and kwargs
        key = (args, tuple(sorted(kwargs.items())))
        if key not in cache:
            cache[key] = func(*args, **kwargs)
        return cache[key]

    wrapper.cache = cache
    wrapper.clear_cache = lambda: cache.clear()
    return wrapper


def retry(max_attempts=3, default=None):
    """Decorator factory for retrying failed function calls."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
            if default is not None:
                return default
            raise last_exception

        return wrapper

    return decorator


def log_calls(logger_func=print):
    """Decorator factory for logging function calls."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger_func(f"Calling {func.__name__} with args={args}, kwargs={kwargs}")
            result = func(*args, **kwargs)
            logger_func(f"{func.__name__} returned {result}")
            return result

        return wrapper

    return decorator


@contextmanager
def temporary_cache():
    """Context manager for a temporary cache that auto-clears."""
    cache = CacheManager()
    try:
        yield cache
    finally:
        cache.clear()


def make_counter(start=0):
    """Create a counter closure."""
    count = [start]  # Use list to allow mutation in closure

    def counter():
        count[0] += 1
        return count[0]

    def reset():
        count[0] = start

    counter.reset = reset
    return counter


class Singleton:
    """Decorator to make a class a singleton."""

    def __init__(self, cls):
        self._cls = cls
        self._instance = None

    def __call__(self, *args, **kwargs):
        if self._instance is None:
            self._instance = self._cls(*args, **kwargs)
        return self._instance

    def reset(self):
        """Reset the singleton instance."""
        self._instance = None
