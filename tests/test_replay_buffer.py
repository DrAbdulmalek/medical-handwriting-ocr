"""
Tests for the Replay Buffer module.
"""

import pytest
import json
import random
from pathlib import Path
from training.replay_buffer import ReplayBuffer


class TestReplayBuffer:
    """Tests for the ReplayBuffer class."""

    @pytest.fixture
    def buffer(self, tmp_path):
        """Create a replay buffer with temp path."""
        return ReplayBuffer(
            capacity=100,
            persist_path=str(tmp_path / "test_buffer.json"),
            stratify_by_script=True,
            min_per_class=5,
        )

    @pytest.fixture
    def sample(self):
        """Create a sample training entry."""
        return {
            "file_name": "images/train_000001.png",
            "text": "Osteoblastoma",
            "script_class": "latin",
            "confidence": 0.75,
            "correction_count": 1,
            "region_id": "uuid-001",
        }

    def test_initial_state(self, buffer):
        """Test buffer starts empty."""
        assert len(buffer.buffer) == 0
        assert buffer.capacity == 100
        stats = buffer.get_statistics()
        assert stats["current_size"] == 0

    def test_add_when_not_full(self, buffer, sample):
        """Test that samples are always added when buffer is not full."""
        result = buffer.add(sample)
        assert result is True
        assert len(buffer.buffer) == 1

    def test_add_multiple_not_full(self, buffer):
        """Test adding multiple samples when buffer has space."""
        for i in range(50):
            buffer.add({
                "file_name": f"images/train_{i:06d}.png",
                "text": f"word_{i}",
                "script_class": "latin",
                "confidence": 0.8,
                "correction_count": 1,
                "region_id": f"uuid-{i}",
            })
        assert len(buffer.buffer) == 50

    def test_reservoir_sampling(self, buffer):
        """Test reservoir sampling behavior when buffer is full."""
        random.seed(42)
        
        # Fill buffer
        for i in range(100):
            buffer.add({
                "file_name": f"images/train_{i:06d}.png",
                "text": f"word_{i}",
                "script_class": "latin",
                "confidence": 0.8,
                "region_id": f"uuid-{i}",
            })
        assert len(buffer.buffer) == 100
        
        # Add more samples - buffer should stay at capacity
        for i in range(50):
            buffer.add({
                "file_name": f"images/new_{i:06d}.png",
                "text": f"new_word_{i}",
                "script_class": "latin",
                "confidence": 0.85,
                "region_id": f"uuid-new-{i}",
            })
        assert len(buffer.buffer) == 100

    def test_stratified_representation(self, buffer):
        """Test that stratification maintains class balance."""
        classes = ["arabic", "latin", "mixed"]
        samples_per_class = 30
        
        # Add balanced samples
        for cls in classes:
            for i in range(samples_per_class):
                buffer.add({
                    "file_name": f"images/{cls}_{i:06d}.png",
                    "text": f"word_{cls}_{i}",
                    "script_class": cls,
                    "confidence": 0.8,
                    "region_id": f"uuid-{cls}-{i}",
                })
        
        # Check class counts
        stats = buffer.get_statistics()
        dist = stats["class_distribution"]
        
        for cls in classes:
            assert dist.get(cls, 0) == samples_per_class

    def test_merge_with_new(self, buffer):
        """Test merging new samples with replay buffer."""
        # Fill buffer with old samples
        for i in range(80):
            buffer.add({
                "file_name": f"images/old_{i:06d}.png",
                "text": f"old_{i}",
                "script_class": "latin",
                "confidence": 0.7,
                "region_id": f"uuid-old-{i}",
            })
        
        new_samples = [
            {
                "file_name": f"images/new_{i:06d}.png",
                "text": f"new_{i}",
                "script_class": "latin",
                "confidence": 0.85,
                "region_id": f"uuid-new-{i}",
            }
            for i in range(20)
        ]
        
        combined = buffer.merge_with_new(new_samples, replay_ratio=0.5)
        
        # Combined should have new samples + replay samples
        assert len(combined) >= 20  # At least the new samples
        assert buffer.get_statistics()["current_size"] == 100

    def test_get_stratified_batch(self, buffer):
        """Test getting a stratified batch."""
        for cls in ["arabic", "latin", "mixed"]:
            for i in range(20):
                buffer.add({
                    "file_name": f"images/{cls}_{i:06d}.png",
                    "text": f"{cls}_{i}",
                    "script_class": cls,
                    "confidence": 0.8,
                    "region_id": f"uuid-{cls}-{i}",
                })
        
        batch = buffer.get_stratified_batch(15)
        assert len(batch) <= 15
        
        # Check all classes are represented
        classes_in_batch = set(s["script_class"] for s in batch)
        assert len(classes_in_batch) == 3

    def test_save_and_load(self, buffer, tmp_path):
        """Test persisting and loading buffer state."""
        for i in range(30):
            buffer.add({
                "file_name": f"images/train_{i:06d}.png",
                "text": f"word_{i}",
                "script_class": "latin",
                "confidence": 0.8,
                "region_id": f"uuid-{i}",
            })
        
        buffer.save()
        
        # Create new buffer and load
        new_buffer = ReplayBuffer(
            capacity=100,
            persist_path=str(tmp_path / "test_buffer.json"),
        )
        loaded = new_buffer.load()
        
        assert loaded is True
        assert len(new_buffer.buffer) == 30
        assert new_buffer.buffer[0]["text"] == "word_0"

    def test_clear(self, buffer):
        """Test clearing the buffer."""
        buffer.add({"text": "test", "script_class": "latin"})
        assert len(buffer.buffer) == 1
        
        buffer.clear()
        assert len(buffer.buffer) == 0
        assert buffer.get_statistics()["current_size"] == 0

    def test_get_statistics(self, buffer):
        """Test statistics computation."""
        for i in range(50):
            buffer.add({
                "text": f"word_{i}",
                "script_class": "latin" if i % 2 == 0 else "arabic",
                "confidence": 0.8,
                "region_id": f"uuid-{i}",
            })
        
        stats = buffer.get_statistics()
        assert stats["current_size"] == 50
        assert stats["capacity"] == 100
        assert stats["class_distribution"]["latin"] == 25
        assert stats["class_distribution"]["arabic"] == 25
        assert 0 < stats["utilization"] < 1
