"""
Data processor with complex class patterns.

Demonstrates:
- Class inheritance
- Static methods
- Class methods
- Properties
- Nested classes
- Abstract-like patterns
"""


class BaseProcessor:
    """Base class for all processors."""

    _registry = {}

    def __init__(self, name):
        self.name = name
        self._processed_count = 0

    @classmethod
    def register(cls, processor_type, processor_class):
        """Register a processor type."""
        cls._registry[processor_type] = processor_class

    @classmethod
    def create(cls, processor_type, name):
        """Factory method to create processors."""
        if processor_type not in cls._registry:
            raise ValueError(f"Unknown processor type: {processor_type}")
        return cls._registry[processor_type](name)

    @staticmethod
    def validate_data(data):
        """Validate that data is not empty."""
        if not data:
            raise ValueError("Data cannot be empty")
        return True

    @property
    def processed_count(self):
        """Number of items processed."""
        return self._processed_count

    def process(self, data):
        """Process data - to be overridden by subclasses."""
        raise NotImplementedError("Subclasses must implement process()")


class NumberProcessor(BaseProcessor):
    """Processes numeric data."""

    class Statistics:
        """Nested class for statistics."""

        def __init__(self, data):
            self.data = data

        def mean(self):
            if not self.data:
                return 0
            return sum(self.data) / len(self.data)

        def total(self):
            return sum(self.data)

        def count(self):
            return len(self.data)

    def __init__(self, name, multiplier=1):
        super().__init__(name)
        self.multiplier = multiplier
        self._results = []

    def process(self, data):
        """Multiply each number by the multiplier."""
        self.validate_data(data)
        self._results = [x * self.multiplier for x in data]
        self._processed_count += len(data)
        return self._results

    def get_statistics(self):
        """Get statistics for processed results."""
        return self.Statistics(self._results)

    @property
    def results(self):
        """Get the processed results."""
        return self._results.copy()


class TextProcessor(BaseProcessor):
    """Processes text data."""

    def __init__(self, name, transform="upper"):
        super().__init__(name)
        self.transform = transform

    def process(self, data):
        """Transform text based on the transform type."""
        self.validate_data(data)
        self._processed_count += 1

        if self.transform == "upper":
            return data.upper()
        elif self.transform == "lower":
            return data.lower()
        elif self.transform == "reverse":
            return data[::-1]
        else:
            return data


class ChainedProcessor(BaseProcessor):
    """Chains multiple processors together."""

    def __init__(self, name, processors=None):
        super().__init__(name)
        self.processors = processors or []

    def add_processor(self, processor):
        """Add a processor to the chain."""
        self.processors.append(processor)

    def process(self, data):
        """Run data through all processors in sequence."""
        result = data
        for processor in self.processors:
            result = processor.process(result)
        self._processed_count += 1
        return result


# Register default processor types
BaseProcessor.register("number", NumberProcessor)
BaseProcessor.register("text", TextProcessor)
BaseProcessor.register("chain", ChainedProcessor)
