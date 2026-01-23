"""Tests for data_processor - complex class patterns."""

import pytest
from src.data_processor import (
    BaseProcessor,
    NumberProcessor,
    TextProcessor,
    ChainedProcessor,
)


class TestBaseProcessor:
    """Tests for BaseProcessor static and class methods."""

    def test_validate_data_valid(self):
        """Test validation with valid data."""
        assert BaseProcessor.validate_data([1, 2, 3]) is True

    def test_validate_data_empty(self):
        """Test validation with empty data."""
        with pytest.raises(ValueError):
            BaseProcessor.validate_data([])

    def test_factory_create_number(self):
        """Test factory method for number processor."""
        processor = BaseProcessor.create("number", "test")
        assert isinstance(processor, NumberProcessor)
        assert processor.name == "test"

    def test_factory_create_text(self):
        """Test factory method for text processor."""
        processor = BaseProcessor.create("text", "test")
        assert isinstance(processor, TextProcessor)

    def test_factory_create_unknown(self):
        """Test factory method with unknown type."""
        with pytest.raises(ValueError):
            BaseProcessor.create("unknown", "test")


class TestNumberProcessor:
    """Tests for NumberProcessor."""

    def test_process_default_multiplier(self):
        """Test processing with default multiplier."""
        processor = NumberProcessor("test")
        result = processor.process([1, 2, 3])
        assert result == [1, 2, 3]

    def test_process_custom_multiplier(self):
        """Test processing with custom multiplier."""
        processor = NumberProcessor("test", multiplier=2)
        result = processor.process([1, 2, 3])
        assert result == [2, 4, 6]

    def test_processed_count(self):
        """Test processed count property."""
        processor = NumberProcessor("test")
        processor.process([1, 2, 3])
        assert processor.processed_count == 3

    def test_results_property(self):
        """Test results property returns copy."""
        processor = NumberProcessor("test", multiplier=10)
        processor.process([1, 2])
        results = processor.results
        assert results == [10, 20]
        results.append(999)  # Modify copy
        assert processor.results == [10, 20]  # Original unchanged


class TestNumberProcessorStatistics:
    """Tests for NumberProcessor.Statistics nested class."""

    def test_statistics_mean(self):
        """Test statistics mean calculation."""
        processor = NumberProcessor("test")
        processor.process([10, 20, 30])
        stats = processor.get_statistics()
        assert stats.mean() == 20.0

    def test_statistics_total(self):
        """Test statistics total calculation."""
        processor = NumberProcessor("test", multiplier=2)
        processor.process([1, 2, 3])  # Results: [2, 4, 6]
        stats = processor.get_statistics()
        assert stats.total() == 12

    def test_statistics_count(self):
        """Test statistics count."""
        processor = NumberProcessor("test")
        processor.process([1, 2, 3, 4, 5])
        stats = processor.get_statistics()
        assert stats.count() == 5

    def test_statistics_empty(self):
        """Test statistics with no data."""
        processor = NumberProcessor("test")
        processor._results = []  # Bypass process
        stats = processor.get_statistics()
        assert stats.mean() == 0
        assert stats.total() == 0


class TestTextProcessor:
    """Tests for TextProcessor."""

    def test_transform_upper(self):
        """Test uppercase transformation."""
        processor = TextProcessor("test", transform="upper")
        assert processor.process("hello") == "HELLO"

    def test_transform_lower(self):
        """Test lowercase transformation."""
        processor = TextProcessor("test", transform="lower")
        assert processor.process("HELLO") == "hello"

    def test_transform_reverse(self):
        """Test reverse transformation."""
        processor = TextProcessor("test", transform="reverse")
        assert processor.process("hello") == "olleh"

    def test_transform_unknown(self):
        """Test unknown transformation returns original."""
        processor = TextProcessor("test", transform="unknown")
        assert processor.process("hello") == "hello"


class TestChainedProcessor:
    """Tests for ChainedProcessor."""

    def test_empty_chain(self):
        """Test chain with no processors."""
        chain = ChainedProcessor("test")
        # Empty chain should return input unchanged (but it's type-dependent)
        # For this test, we just verify it doesn't crash
        assert chain.processors == []

    def test_add_processor(self):
        """Test adding processor to chain."""
        chain = ChainedProcessor("test")
        num_proc = NumberProcessor("num", multiplier=2)
        chain.add_processor(num_proc)
        assert len(chain.processors) == 1

    def test_chain_single_processor(self):
        """Test chain with single processor."""
        num_proc = NumberProcessor("num", multiplier=3)
        chain = ChainedProcessor("test", processors=[num_proc])
        result = chain.process([1, 2, 3])
        assert result == [3, 6, 9]

    def test_chain_multiple_processors(self):
        """Test chain with multiple processors."""
        proc1 = NumberProcessor("double", multiplier=2)
        proc2 = NumberProcessor("triple", multiplier=3)
        chain = ChainedProcessor("test", processors=[proc1, proc2])
        # First doubles: [2, 4, 6], then triples: [6, 12, 18]
        result = chain.process([1, 2, 3])
        assert result == [6, 12, 18]
