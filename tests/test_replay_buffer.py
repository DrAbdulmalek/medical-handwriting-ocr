"""
Tests for the Replay Buffer module.
"""

import pytest
import json
import random
from pathlib import Path
from training.replay_buffer import (
    ReplayBuffer, SampleMetadata, HardExampleMiner,
    DiversitySampler, StratifiedSampler,
)


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


# =============================================================================
# Tests for SampleMetadata
# =============================================================================

class TestSampleMetadata:
    """Tests for the SampleMetadata data class."""

    def test_from_dict_basic(self):
        d = {
            "region_id": "r1",
            "file_name": "crop.png",
            "text": "Osteoblastoma",
            "script_class": "latin",
            "confidence": 0.65,
        }
        meta = SampleMetadata.from_dict(d)
        assert meta.sample_id == "r1"
        assert meta.text == "Osteoblastoma"
        assert meta.original_confidence == 0.65

    def test_to_dict_roundtrip(self):
        meta = SampleMetadata(
            sample_id="r2",
            image_path="crop2.png",
            text="مرحبا",
            script_class="arabic",
            is_medical_term=False,
            original_confidence=0.9,
        )
        d = meta.to_dict()
        assert d["region_id"] == "r2"
        assert d["text"] == "مرحبا"
        assert d["script_class"] == "arabic"

    def test_is_hard_example_high_cer(self):
        meta = SampleMetadata(
            sample_id="h1", text="test", cer_before=0.5
        )
        assert meta.is_hard_example(0.3) is True

    def test_is_hard_example_low_confidence(self):
        meta = SampleMetadata(
            sample_id="h2", text="test", original_confidence=0.4
        )
        assert meta.is_hard_example(0.3) is True

    def test_is_hard_example_easy(self):
        meta = SampleMetadata(
            sample_id="e1", text="test", original_confidence=0.95
        )
        assert meta.is_hard_example(0.3) is False

    def test_improvement(self):
        meta = SampleMetadata(
            sample_id="i1", text="test", cer_before=0.5, cer_after=0.2
        )
        assert meta.improvement() == 0.3

    def test_improvement_none(self):
        meta = SampleMetadata(sample_id="i2", text="test")
        assert meta.improvement() is None


# =============================================================================
# Tests for HardExampleMiner
# =============================================================================

class TestHardExampleMiner:
    """Tests for the HardExampleMiner sampler."""

    def test_rejects_easy_samples(self):
        miner = HardExampleMiner(capacity=10, threshold=0.3)
        easy = SampleMetadata(
            sample_id="easy", text="hello",
            original_confidence=0.95,
        )
        assert miner.add(easy) is False
        assert len(miner.hard_examples) == 0

    def test_accepts_hard_samples(self):
        miner = HardExampleMiner(capacity=10, threshold=0.3)
        hard = SampleMetadata(
            sample_id="hard", text="complex_term",
            original_confidence=0.3,
        )
        assert miner.add(hard) is True
        assert len(miner.hard_examples) == 1

    def test_eviction_on_capacity(self):
        miner = HardExampleMiner(capacity=3, threshold=0.3)
        for i in range(4):
            s = SampleMetadata(
                sample_id=f"hard_{i}",
                text=f"term_{i}",
                original_confidence=0.2,
            )
            miner.add(s)
        assert len(miner.hard_examples) == 3

    def test_statistics(self):
        miner = HardExampleMiner(capacity=10, threshold=0.3)
        miner.add(SampleMetadata(sample_id="h1", text="term1", original_confidence=0.1))
        stats = miner.statistics()
        assert stats["total_hard"] == 1
        assert stats["unique_errors"] == 1


# =============================================================================
# Tests for DiversitySampler
# =============================================================================

class TestDiversitySampler:
    """Tests for the DiversitySampler."""

    def test_creates_clusters(self):
        sampler = DiversitySampler(capacity=50, n_clusters=5)
        for i in range(20):
            sampler.add(SampleMetadata(
                sample_id=f"s_{i}", text=f"word_{i}",
                script_class="latin", original_confidence=0.8,
            ))
        stats = sampler.statistics()
        assert stats["total_clusters"] > 0
        assert stats["total_samples"] == 20

    def test_capacity_enforcement(self):
        sampler = DiversitySampler(capacity=10, n_clusters=3)
        for i in range(20):
            sampler.add(SampleMetadata(
                sample_id=f"s_{i}", text=f"word_{i}",
                script_class="latin", original_confidence=0.8,
            ))
        total = sum(len(v) for v in sampler.clusters.values())
        assert total <= 10


# =============================================================================
# Tests for StratifiedSampler
# =============================================================================

class TestStratifiedSampler:
    """Tests for the StratifiedSampler."""

    def test_maintains_proportions(self):
        sampler = StratifiedSampler(
            capacity=100,
            stratify_by=["script_class"],
        )
        for i in range(70):
            sampler.add(SampleMetadata(
                sample_id=f"l_{i}", text=f"word_{i}",
                script_class="latin", original_confidence=0.8,
            ))
        for i in range(30):
            sampler.add(SampleMetadata(
                sample_id=f"a_{i}", text=f"كلمة_{i}",
                script_class="arabic", original_confidence=0.8,
            ))
        selected = sampler.sample(50)
        latin_count = sum(1 for s in selected if s.script_class == "latin")
        arabic_count = len(selected) - latin_count
        assert 25 < latin_count < 45
        assert 10 < arabic_count < 25

    def test_empty_buffer(self):
        sampler = StratifiedSampler()
        assert sampler.sample(10) == []


# =============================================================================
# Tests for Enhanced ReplayBuffer with advanced samplers
# =============================================================================

class TestEnhancedReplayBuffer:
    """Tests for the ReplayBuffer with advanced samplers."""

    @pytest.fixture
    def advanced_buffer(self, tmp_path):
        return ReplayBuffer(
            capacity=100,
            persist_path=str(tmp_path / "adv_buffer.json"),
            enable_advanced_samplers=True,
        )

    def test_advanced_samplers_populated(self, advanced_buffer):
        for i in range(20):
            advanced_buffer.add({
                "file_name": f"img_{i}.png", "text": f"term_{i}",
                "script_class": "latin" if i % 2 == 0 else "arabic",
                "confidence": 0.3 if i % 5 == 0 else 0.8,
                "region_id": f"r_{i}",
            })
        stats = advanced_buffer.get_statistics()
        assert stats["advanced_samplers_enabled"] is True
        assert "hard_examples" in stats

    def test_get_hard_samples(self, advanced_buffer):
        for i in range(30):
            advanced_buffer.add({
                "file_name": f"img_{i}.png",
                "text": f"hard_term_{i}" if i < 10 else f"easy_term_{i}",
                "confidence": 0.2 if i < 10 else 0.9,
                "script_class": "latin", "region_id": f"r_{i}",
            })
        hard = advanced_buffer.get_hard_samples(5)
        assert len(hard) <= 5

    def test_get_mixed_batch(self, advanced_buffer):
        for i in range(40):
            advanced_buffer.add({
                "file_name": f"img_{i}.png", "text": f"term_{i}",
                "script_class": random.choice(["arabic", "latin"]),
                "confidence": 0.3 if i < 10 else 0.8,
                "region_id": f"r_{i}",
            })
        mixed = advanced_buffer.get_mixed_batch(20)
        assert len(mixed) <= 20

    def test_merge_with_strategies(self, advanced_buffer):
        for i in range(50):
            advanced_buffer.add({
                "file_name": f"old_{i}.png", "text": f"old_{i}",
                "script_class": "latin", "confidence": 0.7,
                "region_id": f"old_{i}",
            })
        new_samples = [
            {"file_name": f"new_{i}.png", "text": f"new_{i}",
             "script_class": "arabic", "confidence": 0.85, "region_id": f"new_{i}"}
            for i in range(10)
        ]
        for strategy in ["stratified", "hard", "diverse", "mixed"]:
            combined = advanced_buffer.merge_with_new(
                [dict(s) for s in new_samples],
                replay_ratio=0.5, strategy=strategy,
            )
            assert len(combined) >= 10

    def test_clear_resets_samplers(self, advanced_buffer):
        advanced_buffer.add({
            "file_name": "img.png", "text": "hard",
            "confidence": 0.1, "script_class": "latin", "region_id": "r1",
        })
        advanced_buffer.clear()
        stats = advanced_buffer.get_statistics()
        assert stats["current_size"] == 0
        assert stats["hard_examples"]["total_hard"] == 0
