"""
Generators and iterators module.

Demonstrates:
- Generator functions (yield)
- Generator expressions
- Custom iterators
- Infinite generators
- Generator pipelines
"""


def fibonacci(n):
    """Generate first n Fibonacci numbers."""
    a, b = 0, 1
    count = 0
    while count < n:
        yield a
        a, b = b, a + b
        count += 1


def infinite_counter(start=0, step=1):
    """Generate infinite sequence of numbers."""
    current = start
    while True:
        yield current
        current += step


def range_generator(start, stop, step=1):
    """Custom range implementation as a generator."""
    current = start
    if step > 0:
        while current < stop:
            yield current
            current += step
    else:
        while current > stop:
            yield current
            current += step


def filter_generator(predicate, iterable):
    """Filter items from iterable using predicate."""
    for item in iterable:
        if predicate(item):
            yield item


def map_generator(func, iterable):
    """Map function over iterable."""
    for item in iterable:
        yield func(item)


def take(n, iterable):
    """Take first n items from iterable."""
    count = 0
    for item in iterable:
        if count >= n:
            break
        yield item
        count += 1


def chunk(iterable, size):
    """Split iterable into chunks of given size."""
    chunk_list = []
    for item in iterable:
        chunk_list.append(item)
        if len(chunk_list) >= size:
            yield chunk_list
            chunk_list = []
    if chunk_list:
        yield chunk_list


def flatten(nested):
    """Flatten nested iterables."""
    for item in nested:
        if hasattr(item, "__iter__") and not isinstance(item, (str, bytes)):
            yield from flatten(item)
        else:
            yield item


class NumberIterator:
    """Custom iterator class for numbers."""

    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.current = start

    def __iter__(self):
        return self

    def __next__(self):
        if self.current >= self.end:
            raise StopIteration
        value = self.current
        self.current += 1
        return value

    def reset(self):
        """Reset iterator to start."""
        self.current = self.start


class ReversibleIterator:
    """Iterator that can go forward and backward."""

    def __init__(self, data):
        self._data = list(data)
        self._index = 0
        self._forward = True

    def __iter__(self):
        return self

    def __next__(self):
        if self._forward:
            if self._index >= len(self._data):
                raise StopIteration
            value = self._data[self._index]
            self._index += 1
        else:
            if self._index < 0:
                raise StopIteration
            value = self._data[self._index]
            self._index -= 1
        return value

    def reverse(self):
        """Reverse direction of iteration."""
        self._forward = not self._forward
        if self._forward:
            self._index = 0
        else:
            self._index = len(self._data) - 1


def pipeline(*generators):
    """Create a generator pipeline."""
    def run(data):
        result = data
        for gen in generators:
            result = gen(result)
        return result
    return run


# Pre-built pipeline functions
def double(iterable):
    """Double each value."""
    return map_generator(lambda x: x * 2, iterable)


def square(iterable):
    """Square each value."""
    return map_generator(lambda x: x ** 2, iterable)


def evens_only(iterable):
    """Filter to only even numbers."""
    return filter_generator(lambda x: x % 2 == 0, iterable)


def odds_only(iterable):
    """Filter to only odd numbers."""
    return filter_generator(lambda x: x % 2 != 0, iterable)
