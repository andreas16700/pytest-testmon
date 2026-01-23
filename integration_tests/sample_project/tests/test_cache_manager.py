"""Tests for cache_manager - decorators and context managers."""

import pytest
from src.cache_manager import (
    CacheManager,
    memoize,
    retry,
    log_calls,
    temporary_cache,
    make_counter,
    Singleton,
)


class TestCacheManager:
    """Tests for CacheManager class."""

    def test_set_and_get(self):
        """Test basic set and get."""
        cache = CacheManager()
        cache.set("key", "value")
        assert cache.get("key") == "value"

    def test_get_missing(self):
        """Test get for missing key."""
        cache = CacheManager()
        assert cache.get("missing") is None

    def test_cache_hits(self):
        """Test cache hit counting."""
        cache = CacheManager()
        cache.set("key", "value")
        cache.get("key")
        cache.get("key")
        assert cache.stats["hits"] == 2

    def test_cache_misses(self):
        """Test cache miss counting."""
        cache = CacheManager()
        cache.get("missing1")
        cache.get("missing2")
        assert cache.stats["misses"] == 2

    def test_max_size(self):
        """Test max size eviction."""
        cache = CacheManager(max_size=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)  # Should evict 'a'
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3

    def test_clear(self):
        """Test cache clearing."""
        cache = CacheManager()
        cache.set("key", "value")
        cache.get("key")
        cache.clear()
        assert cache.get("key") is None
        assert cache.stats["hits"] == 0


class TestCacheManagerContext:
    """Tests for CacheManager as context manager."""

    def test_context_manager_enter(self):
        """Test entering context."""
        cache = CacheManager()
        cache.set("before", "value")
        with cache as c:
            assert c.get("before") is None  # Cleared on enter
            c.set("inside", "value")
            assert c.get("inside") == "value"

    def test_context_manager_exit(self):
        """Test exiting context clears cache."""
        cache = CacheManager()
        with cache:
            cache.set("key", "value")
        assert cache.get("key") is None  # Cleared on exit

    def test_temporary_cache(self):
        """Test temporary_cache context manager."""
        with temporary_cache() as cache:
            cache.set("temp", "data")
            assert cache.get("temp") == "data"
        # Cache is cleared after context


class TestMemoizeDecorator:
    """Tests for memoize decorator."""

    def test_memoize_caches_result(self):
        """Test that memoize caches results."""
        call_count = [0]

        @memoize
        def expensive_func(x):
            call_count[0] += 1
            return x * 2

        assert expensive_func(5) == 10
        assert expensive_func(5) == 10  # Should use cache
        assert call_count[0] == 1  # Only called once

    def test_memoize_different_args(self):
        """Test memoize with different arguments."""
        @memoize
        def add(a, b):
            return a + b

        assert add(1, 2) == 3
        assert add(3, 4) == 7
        assert len(add.cache) == 2

    def test_memoize_clear_cache(self):
        """Test memoize clear_cache function."""
        @memoize
        def func(x):
            return x

        func(1)
        func(2)
        assert len(func.cache) == 2
        func.clear_cache()
        assert len(func.cache) == 0


class TestRetryDecorator:
    """Tests for retry decorator."""

    def test_retry_success_first_try(self):
        """Test retry with success on first try."""
        @retry(max_attempts=3)
        def always_works():
            return "success"

        assert always_works() == "success"

    def test_retry_success_after_failures(self):
        """Test retry with success after failures."""
        attempts = [0]

        @retry(max_attempts=3)
        def fails_twice():
            attempts[0] += 1
            if attempts[0] < 3:
                raise ValueError("Not yet")
            return "success"

        assert fails_twice() == "success"
        assert attempts[0] == 3

    def test_retry_all_fail_with_default(self):
        """Test retry with all failures and default."""
        @retry(max_attempts=2, default="default")
        def always_fails():
            raise ValueError("Always fails")

        assert always_fails() == "default"

    def test_retry_all_fail_raises(self):
        """Test retry raises when all attempts fail."""
        @retry(max_attempts=2)
        def always_fails():
            raise ValueError("Always fails")

        with pytest.raises(ValueError):
            always_fails()


class TestLogCallsDecorator:
    """Tests for log_calls decorator."""

    def test_log_calls_captures_call(self):
        """Test that log_calls logs function calls."""
        logs = []

        @log_calls(logger_func=logs.append)
        def greet(name):
            return f"Hello, {name}"

        result = greet("World")
        assert result == "Hello, World"
        assert len(logs) == 2  # Call and return logged
        assert "Calling greet" in logs[0]
        assert "returned Hello, World" in logs[1]


class TestMakeCounter:
    """Tests for make_counter closure."""

    def test_counter_increments(self):
        """Test counter increments."""
        counter = make_counter()
        assert counter() == 1
        assert counter() == 2
        assert counter() == 3

    def test_counter_custom_start(self):
        """Test counter with custom start."""
        counter = make_counter(start=10)
        assert counter() == 11
        assert counter() == 12

    def test_counter_reset(self):
        """Test counter reset."""
        counter = make_counter(start=5)
        counter()
        counter()
        counter.reset()
        assert counter() == 6


class TestSingletonDecorator:
    """Tests for Singleton decorator."""

    def test_singleton_same_instance(self):
        """Test that singleton returns same instance."""
        @Singleton
        class MyClass:
            def __init__(self, value):
                self.value = value

        instance1 = MyClass(1)
        instance2 = MyClass(2)  # Should return same instance
        assert instance1 is instance2
        assert instance1.value == 1  # First value preserved

    def test_singleton_reset(self):
        """Test singleton reset."""
        creation_count = [0]  # Use mutable to track creations

        @Singleton
        class Counter:
            def __init__(self):
                creation_count[0] += 1
                self.value = creation_count[0]

        c1 = Counter()
        c2 = Counter()
        assert creation_count[0] == 1  # Only created once
        assert c1.value == 1
        Counter.reset()
        c3 = Counter()
        assert creation_count[0] == 2  # New instance after reset
        assert c3.value == 2
