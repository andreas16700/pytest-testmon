"""Tests for generators - generator functions and iterators."""

import pytest
from src.generators import (
    fibonacci,
    infinite_counter,
    range_generator,
    filter_generator,
    map_generator,
    take,
    chunk,
    flatten,
    NumberIterator,
    ReversibleIterator,
    pipeline,
    double,
    square,
    evens_only,
    odds_only,
)


class TestFibonacci:
    """Tests for fibonacci generator."""

    def test_fibonacci_first_ten(self):
        """Test first 10 Fibonacci numbers."""
        result = list(fibonacci(10))
        assert result == [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]

    def test_fibonacci_zero(self):
        """Test fibonacci with n=0."""
        result = list(fibonacci(0))
        assert result == []

    def test_fibonacci_one(self):
        """Test fibonacci with n=1."""
        result = list(fibonacci(1))
        assert result == [0]


class TestInfiniteCounter:
    """Tests for infinite_counter generator."""

    def test_infinite_counter_default(self):
        """Test infinite counter with defaults."""
        counter = infinite_counter()
        result = [next(counter) for _ in range(5)]
        assert result == [0, 1, 2, 3, 4]

    def test_infinite_counter_custom_start(self):
        """Test infinite counter with custom start."""
        counter = infinite_counter(start=10)
        result = [next(counter) for _ in range(3)]
        assert result == [10, 11, 12]

    def test_infinite_counter_custom_step(self):
        """Test infinite counter with custom step."""
        counter = infinite_counter(start=0, step=5)
        result = [next(counter) for _ in range(4)]
        assert result == [0, 5, 10, 15]


class TestRangeGenerator:
    """Tests for range_generator."""

    def test_range_basic(self):
        """Test basic range."""
        result = list(range_generator(0, 5))
        assert result == [0, 1, 2, 3, 4]

    def test_range_with_step(self):
        """Test range with custom step."""
        result = list(range_generator(0, 10, 2))
        assert result == [0, 2, 4, 6, 8]

    def test_range_negative_step(self):
        """Test range with negative step."""
        result = list(range_generator(5, 0, -1))
        assert result == [5, 4, 3, 2, 1]


class TestFilterGenerator:
    """Tests for filter_generator."""

    def test_filter_even(self):
        """Test filtering even numbers."""
        result = list(filter_generator(lambda x: x % 2 == 0, [1, 2, 3, 4, 5, 6]))
        assert result == [2, 4, 6]

    def test_filter_none_match(self):
        """Test filter when nothing matches."""
        result = list(filter_generator(lambda x: x > 100, [1, 2, 3]))
        assert result == []


class TestMapGenerator:
    """Tests for map_generator."""

    def test_map_double(self):
        """Test mapping double function."""
        result = list(map_generator(lambda x: x * 2, [1, 2, 3]))
        assert result == [2, 4, 6]

    def test_map_strings(self):
        """Test mapping on strings."""
        result = list(map_generator(str.upper, ["a", "b", "c"]))
        assert result == ["A", "B", "C"]


class TestTake:
    """Tests for take generator."""

    def test_take_less_than_available(self):
        """Test taking fewer items than available."""
        result = list(take(3, [1, 2, 3, 4, 5]))
        assert result == [1, 2, 3]

    def test_take_more_than_available(self):
        """Test taking more items than available."""
        result = list(take(10, [1, 2, 3]))
        assert result == [1, 2, 3]

    def test_take_from_infinite(self):
        """Test taking from infinite generator."""
        result = list(take(5, infinite_counter()))
        assert result == [0, 1, 2, 3, 4]


class TestChunk:
    """Tests for chunk generator."""

    def test_chunk_even_split(self):
        """Test chunking with even split."""
        result = list(chunk([1, 2, 3, 4, 5, 6], 2))
        assert result == [[1, 2], [3, 4], [5, 6]]

    def test_chunk_uneven_split(self):
        """Test chunking with uneven split."""
        result = list(chunk([1, 2, 3, 4, 5], 2))
        assert result == [[1, 2], [3, 4], [5]]

    def test_chunk_larger_than_input(self):
        """Test chunk size larger than input."""
        result = list(chunk([1, 2], 5))
        assert result == [[1, 2]]


class TestFlatten:
    """Tests for flatten generator."""

    def test_flatten_nested_lists(self):
        """Test flattening nested lists."""
        result = list(flatten([[1, 2], [3, [4, 5]]]))
        assert result == [1, 2, 3, 4, 5]

    def test_flatten_preserves_strings(self):
        """Test that strings are not flattened."""
        result = list(flatten(["abc", ["def"]]))
        assert result == ["abc", "def"]

    def test_flatten_deeply_nested(self):
        """Test deeply nested structure."""
        result = list(flatten([1, [2, [3, [4, [5]]]]]))
        assert result == [1, 2, 3, 4, 5]


class TestNumberIterator:
    """Tests for NumberIterator class."""

    def test_iterate_range(self):
        """Test iterating over range."""
        it = NumberIterator(0, 5)
        result = list(it)
        assert result == [0, 1, 2, 3, 4]

    def test_reset(self):
        """Test iterator reset."""
        it = NumberIterator(0, 3)
        list(it)  # Exhaust iterator
        it.reset()
        result = list(it)
        assert result == [0, 1, 2]


class TestReversibleIterator:
    """Tests for ReversibleIterator class."""

    def test_forward_iteration(self):
        """Test forward iteration."""
        it = ReversibleIterator([1, 2, 3])
        result = list(it)
        assert result == [1, 2, 3]

    def test_reverse_direction(self):
        """Test reversing iteration direction."""
        it = ReversibleIterator([1, 2, 3])
        next(it)  # Get 1
        next(it)  # Get 2
        it.reverse()
        result = [next(it), next(it), next(it)]
        assert result == [3, 2, 1]


class TestPipeline:
    """Tests for pipeline functions."""

    def test_double(self):
        """Test double function."""
        result = list(double([1, 2, 3]))
        assert result == [2, 4, 6]

    def test_square(self):
        """Test square function."""
        result = list(square([1, 2, 3]))
        assert result == [1, 4, 9]

    def test_evens_only(self):
        """Test evens_only filter."""
        result = list(evens_only([1, 2, 3, 4, 5, 6]))
        assert result == [2, 4, 6]

    def test_odds_only(self):
        """Test odds_only filter."""
        result = list(odds_only([1, 2, 3, 4, 5, 6]))
        assert result == [1, 3, 5]

    def test_pipeline_composition(self):
        """Test composing multiple pipeline functions."""
        # Double then filter evens (all should be even after doubling)
        data = [1, 2, 3, 4, 5]
        result = list(evens_only(double(data)))
        assert result == [2, 4, 6, 8, 10]
