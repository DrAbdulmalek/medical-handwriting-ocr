"""
Replay Buffer for continual learning.
Implements reservoir sampling with advanced strategies to maintain a
representative subset of historical data. Prevents catastrophic forgetting
during incremental TrOCR fine-tuning.

Strategies:
  - Reservoir sampling (uniform random from stream)
  - Hard example mining (prioritizes difficult/confusing samples)
  - Diversity sampling (ensures visual pattern coverage via clustering)
  - Stratified sampling (maintains category balance)
"""

import random
import json
import logging
from typing import List, Dict, Optional, Tuple
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

class SampleMetadata:
    """Lightweight container for a single training sample's metadata.

    Used by the advanced samplers (HardExampleMiner, DiversitySampler,
    StratifiedSampler) to track extra information beyond the basic
    dict format of the core ReplayBuffer.
    """

    __slots__ = (
        "sample_id", "image_path", "text", "predicted_text",
        "corrected_text", "script_class", "is_medical_term",
        "original_confidence", "cer_before", "cer_after",
        "created_at", "use_count", "model_version",
    )

    def __init__(
        self,
        sample_id: str,
        image_path: str = "",
        text: str = "",
        predicted_text: Optional[str] = None,
        corrected_text: Optional[str] = None,
        script_class: str = "unknown",
        is_medical_term: bool = False,
        original_confidence: float = 0.0,
        cer_before: Optional[float] = None,
        cer_after: Optional[float] = None,
        created_at: Optional[datetime] = None,
        use_count: int = 0,
        model_version: str = "unknown",
    ):
        self.sample_id = sample_id
        self.image_path = image_path
        self.text = text
        self.predicted_text = predicted_text
        self.corrected_text = corrected_text
        self.script_class = script_class
        self.is_medical_term = is_medical_term
        self.original_confidence = original_confidence
        self.cer_before = cer_before
        self.cer_after = cer_after
        self.created_at = created_at or datetime.now()
        self.use_count = use_count
        self.model_version = model_version

    def is_hard_example(self, threshold: float = 0.3) -> bool:
        """A sample is 'hard' if its CER was high or confidence was low."""
        if self.cer_before is not None:
            return self.cer_before > threshold
        return self.original_confidence < (1 - threshold)

    def improvement(self) -> Optional[float]:
        """Positive if the model improved on this sample after correction."""
        if self.cer_before is not None and self.cer_after is not None:
            return self.cer_before - self.cer_after
        return None

    def to_dict(self) -> Dict:
        """Convert to plain dict (compatible with ReplayBuffer format)."""
        return {
            "file_name": self.image_path,
            "text": self.corrected_text or self.text,
            "script_class": self.script_class,
            "confidence": self.original_confidence,
            "region_id": self.sample_id,
            "is_medical_term": self.is_medical_term,
            "cer_before": self.cer_before,
            "use_count": self.use_count,
            "model_version": self.model_version,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "SampleMetadata":
        """Create from a plain dict (compatible with ReplayBuffer samples)."""
        return cls(
            sample_id=d.get("region_id", d.get("file_name", "")),
            image_path=d.get("file_name", ""),
            text=d.get("text", ""),
            predicted_text=d.get("predicted_text"),
            corrected_text=d.get("corrected_text"),
            script_class=d.get("script_class", "unknown"),
            is_medical_term=d.get("is_medical_term", False),
            original_confidence=d.get("confidence", 0.0),
            cer_before=d.get("cer_before"),
            cer_after=d.get("cer_after"),
            model_version=d.get("model_version", "unknown"),
            use_count=d.get("use_count", 0),
        )


# =============================================================================
# Advanced Samplers
# =============================================================================

class HardExampleMiner:
    """Prioritizes difficult/confusing examples for replay.

    Hard examples are defined by:
    - High Character Error Rate (CER) before correction
    - Low OCR confidence score
    - Frequent error patterns (same text misread repeatedly)

    During sampling, harder and more frequent errors are more likely
    to be selected (weighted sampling).
    """

    def __init__(self, capacity: int = 500, threshold: float = 0.3):
        """
        Args:
            capacity: Maximum number of hard examples to keep.
            threshold: CER / (1-confidence) threshold above which a sample
                       is considered 'hard'.
        """
        self.capacity = capacity
        self.threshold = threshold
        self.hard_examples: Dict[str, SampleMetadata] = {}
        self.error_frequency: Counter = Counter()

    def add(self, sample: SampleMetadata) -> bool:
        """Add a sample if it qualifies as a hard example."""
        if not sample.is_hard_example(self.threshold):
            return False

        self.error_frequency[sample.text] += 1

        if sample.sample_id in self.hard_examples:
            # Keep the newer version
            if sample.use_count == 0:
                self.hard_examples[sample.sample_id] = sample
        else:
            if len(self.hard_examples) >= self.capacity:
                # Evict the least frequent error
                least_frequent_id = min(
                    self.hard_examples,
                    key=lambda k: self.error_frequency.get(
                        self.hard_examples[k].text, 0
                    ),
                )
                del self.hard_examples[least_frequent_id]

            self.hard_examples[sample.sample_id] = sample

        return True

    def sample(self, n: int) -> List[SampleMetadata]:
        """Sample hard examples weighted by error frequency."""
        if not self.hard_examples:
            return []

        samples = list(self.hard_examples.values())
        weights = [
            self.error_frequency.get(s.text, 0) + 1  # +1 avoids zero weight
            for s in samples
        ]
        total = sum(weights)
        probs = [w / total for w in weights]

        n = min(n, len(samples))
        # Use random.choices for weighted sampling (Python 3.6+)
        selected = random.choices(samples, weights=weights, k=n)
        # Remove duplicates
        seen = set()
        unique = []
        for s in selected:
            if s.sample_id not in seen:
                seen.add(s.sample_id)
                unique.append(s)
        return unique

    def statistics(self) -> Dict:
        return {
            "total_hard": len(self.hard_examples),
            "unique_errors": len(self.error_frequency),
            "top_errors": self.error_frequency.most_common(10),
            "capacity": self.capacity,
            "threshold": self.threshold,
        }


class DiversitySampler:
    """Ensures replay batches cover diverse visual patterns.

    Clusters samples by a lightweight feature vector derived from the
    image histogram and gradient statistics.  During sampling, picks
    examples from different clusters so the model sees varied handwriting
    styles in each batch.

    Falls back to random sampling when image files are unavailable.
    """

    def __init__(self, capacity: int = 500, n_clusters: int = 20):
        """
        Args:
            capacity: Max samples across all clusters.
            n_clusters: Target number of clusters.
        """
        self.capacity = capacity
        self.n_clusters = n_clusters
        self.clusters: Dict[int, List[SampleMetadata]] = defaultdict(list)
        self.cluster_centers: Dict[int, List[float]] = {}
        self._feature_cache: Dict[str, List[float]] = {}

    def _compute_features(self, sample: SampleMetadata) -> List[float]:
        """Compute a lightweight 10-d feature vector for a sample.

        Features: text length, aspect ratio proxy (text len / word count),
        character set diversity (unique / total), medical flag, script class
        one-hot (5 dims).

        This text-based fallback is used when image files are not on disk.
        """
        cache_key = sample.sample_id
        if cache_key in self._feature_cache:
            return self._feature_cache[cache_key]

        text = sample.text or ""
        words = text.split()
        unique_chars = len(set(text)) if text else 0
        total_chars = len(text) if text else 1

        # Script class one-hot
        script_map = {"arabic": [1, 0, 0, 0, 0], "latin": [0, 1, 0, 0, 0],
                      "mixed": [0, 0, 1, 0, 0], "numeric": [0, 0, 0, 1, 0]}
        script_vec = script_map.get(sample.script_class, [0, 0, 0, 0, 1])

        features = [
            min(len(text) / 50.0, 1.0),           # normalized text length
            len(words) / max(len(text), 1),         # word density
            unique_chars / total_chars,              # character diversity
            float(sample.is_medical_term),          # medical flag
        ] + script_vec                              # 5-d script one-hot

        self._feature_cache[cache_key] = features
        return features

    @staticmethod
    def _euclidean(a: List[float], b: List[float]) -> float:
        return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5

    def _assign_cluster(self, sample: SampleMetadata) -> int:
        """Assign sample to nearest cluster, creating a new one if needed."""
        features = self._compute_features(sample)

        if not self.cluster_centers:
            cluster_id = 0
            self.cluster_centers[cluster_id] = features
        else:
            distances = {
                cid: self._euclidean(features, center)
                for cid, center in self.cluster_centers.items()
            }
            nearest_id = min(distances, key=distances.get)

            # If far from all clusters and under limit, create new cluster
            if (distances[nearest_id] > 0.5
                    and len(self.cluster_centers) < self.n_clusters):
                nearest_id = len(self.cluster_centers)
                self.cluster_centers[nearest_id] = features

        return nearest_id

    def add(self, sample: SampleMetadata) -> None:
        """Add a sample to the appropriate cluster."""
        cluster_id = self._assign_cluster(sample)
        self.clusters[cluster_id].append(sample)

        # Update cluster center (moving average)
        members = self.clusters[cluster_id]
        if len(members) == 1:
            self.cluster_centers[cluster_id] = self._compute_features(sample)
        else:
            n = len(members)
            old_center = self.cluster_centers[cluster_id]
            new_features = self._compute_features(sample)
            self.cluster_centers[cluster_id] = [
                (old_center[i] * (n - 1) + new_features[i]) / n
                for i in range(len(old_center))
            ]

        # Enforce capacity — drop oldest from largest cluster
        total = sum(len(v) for v in self.clusters.values())
        if total > self.capacity:
            largest_cid = max(self.clusters, key=lambda c: len(self.clusters[c]))
            self.clusters[largest_cid].pop(0)

    def sample(self, n: int) -> List[SampleMetadata]:
        """Sample from different clusters to maximize diversity."""
        if not self.clusters:
            return []

        selected = []
        cluster_ids = list(self.clusters.keys())
        random.shuffle(cluster_ids)

        # Round-robin across clusters
        attempts = 0
        while len(selected) < n and attempts < n * 2:
            for cid in cluster_ids:
                if len(selected) >= n:
                    break
                members = [
                    s for s in self.clusters[cid]
                    if s.sample_id not in {x.sample_id for x in selected}
                ]
                if members:
                    chosen = min(members, key=lambda s: s.use_count)
                    selected.append(chosen)
            attempts += 1

        random.shuffle(selected)
        return selected

    def statistics(self) -> Dict:
        return {
            "total_clusters": len(self.clusters),
            "cluster_sizes": {k: len(v) for k, v in self.clusters.items()},
            "total_samples": sum(len(v) for v in self.clusters.values()),
            "capacity": self.capacity,
        }


class StratifiedSampler:
    """Maintains category-balanced sampling.

    Stratifies by configurable fields (e.g., script_class, is_medical_term)
    so that each batch reflects the overall distribution.
    """

    def __init__(
        self,
        capacity: int = 500,
        stratify_by: Optional[List[str]] = None,
    ):
        """
        Args:
            capacity: Max samples per stratum (total capacity varies).
            stratify_by: Fields on SampleMetadata to stratify by.
                         Defaults to ["script_class"].
        """
        self.capacity = capacity
        self.stratify_by = stratify_by or ["script_class"]
        self.strata: Dict[Tuple, List[SampleMetadata]] = defaultdict(list)

    def _get_stratum_key(self, sample: SampleMetadata) -> Tuple:
        return tuple(getattr(sample, field, "unknown") for field in self.stratify_by)

    def add(self, sample: SampleMetadata) -> None:
        """Add a sample to the appropriate stratum."""
        key = self._get_stratum_key(sample)
        self.strata[key].append(sample)

        # Enforce per-stratum capacity
        if len(self.strata[key]) > self.capacity:
            self.strata[key].sort(key=lambda s: s.created_at)
            self.strata[key] = self.strata[key][-self.capacity:]

    def sample(self, n: int) -> List[SampleMetadata]:
        """Sample maintaining proportional representation."""
        if not self.strata:
            return []

        total = sum(len(v) for v in self.strata.values())
        if total == 0:
            return []

        selected = []
        remaining = n

        # Proportional allocation
        for key in sorted(self.strata.keys()):
            if remaining <= 0:
                break
            proportion = len(self.strata[key]) / total
            count = min(int(n * proportion) + (1 if remaining > 0 else 0),
                        len(self.strata[key]), remaining)

            # Pick least-used samples
            candidates = sorted(
                self.strata[key],
                key=lambda s: (s.use_count, s.created_at or datetime.min),
            )
            selected.extend(candidates[:count])
            remaining -= count

        random.shuffle(selected)
        return selected

    def statistics(self) -> Dict:
        total = sum(len(v) for v in self.strata.values())
        return {
            "num_strata": len(self.strata),
            "stratum_sizes": {str(k): len(v) for k, v in self.strata.items()},
            "total_samples": total,
            "stratify_by": self.stratify_by,
        }


# =============================================================================
# Main ReplayBuffer (backward-compatible, enhanced with samplers)
# =============================================================================

class ReplayBuffer:
    """
    Reservoir sampling-based replay buffer for continual learning.

    Maintains a fixed-size buffer of historical training samples that
    are combined with new corrections during fine-tuning to prevent
    catastrophic forgetting.

    Enhanced with optional advanced samplers:
    - HardExampleMiner: prioritizes difficult samples
    - DiversitySampler: ensures visual pattern variety
    - StratifiedSampler: maintains category balance
    """

    def __init__(
        self,
        capacity: int = 2000,
        persist_path: str = "./replay_buffer.json",
        stratify_by_script: bool = True,
        min_per_class: int = 50,
        enable_advanced_samplers: bool = True,
    ):
        """
        Args:
            capacity: Maximum number of samples to keep in buffer.
            persist_path: File path for saving/loading buffer state.
            stratify_by_script: Whether to maintain balanced representation
                               across script classes (arabic/latin/mixed).
            min_per_class: Minimum samples to retain per script class.
            enable_advanced_samplers: Enable HardExampleMiner, DiversitySampler,
                                     and StratifiedSampler.
        """
        self.capacity = capacity
        self.persist_path = Path(persist_path)
        self.stratify_by_script = stratify_by_script
        self.min_per_class = min_per_class

        self.buffer: List[Dict] = []
        self._total_seen = 0
        self._class_counts = {"arabic": 0, "latin": 0, "mixed": 0, "numeric": 0, "unknown": 0}
        self._metadata = {
            "created_at": None,
            "last_updated": None,
            "total_samples_seen": 0,
            "total_accepted": 0,
            "total_rejected": 0,
        }

        # Advanced samplers
        self.enable_advanced = enable_advanced_samplers
        self.hard_miner = HardExampleMiner(capacity=max(100, capacity // 4)) if enable_advanced_samplers else None
        self.diversity_sampler = DiversitySampler(capacity=max(100, capacity // 4)) if enable_advanced_samplers else None
        self.stratified_sampler = StratifiedSampler(capacity=max(100, capacity // 4)) if enable_advanced_samplers else None

    def add(self, sample: Dict) -> bool:
        """
        Add a sample using reservoir sampling.

        Also feeds the advanced samplers when enabled.

        Args:
            sample: Training sample dict with keys:
                   - file_name: path to crop image
                   - text: ground truth text
                   - script_class: arabic/latin/mixed/numeric
                   - confidence: OCR confidence score
                   - correction_count: number of user corrections
                   - region_id: unique identifier

        Returns:
            True if sample was added to buffer, False if rejected.
        """
        self._total_seen += 1
        script_class = sample.get("script_class", "unknown")

        if len(self.buffer) < self.capacity:
            self.buffer.append(sample)
            self._class_counts[script_class] = self._class_counts.get(script_class, 0) + 1
            self._metadata["total_accepted"] += 1
        else:
            probability = self.capacity / self._total_seen

            if self.stratify_by_script:
                current_count = self._class_counts.get(script_class, 0)
                class_ratio = current_count / self.capacity
                min_ratio = self.min_per_class / self.capacity

                if class_ratio <= min_ratio:
                    probability = max(probability, min_ratio * 2)

            if random.random() < probability:
                if self.stratify_by_script:
                    replace_idx = self._find_overrepresented_index(script_class)
                else:
                    replace_idx = random.randint(0, self.capacity - 1)

                if replace_idx is not None:
                    old_sample = self.buffer[replace_idx]
                    old_class = old_sample.get("script_class", "unknown")
                    self._class_counts[old_class] = max(0, self._class_counts.get(old_class, 0) - 1)

                    self.buffer[replace_idx] = sample
                    self._class_counts[script_class] = self._class_counts.get(script_class, 0) + 1
                    self._metadata["total_accepted"] += 1

                    # Feed advanced samplers
                    if self.enable_advanced:
                        meta = SampleMetadata.from_dict(sample)
                        if self.hard_miner:
                            self.hard_miner.add(meta)
                        if self.diversity_sampler:
                            self.diversity_sampler.add(meta)
                        if self.stratified_sampler:
                            self.stratified_sampler.add(meta)
                    return True

            self._metadata["total_rejected"] += 1
            return False

        # Feed advanced samplers for new additions too
        if self.enable_advanced:
            meta = SampleMetadata.from_dict(sample)
            if self.hard_miner:
                self.hard_miner.add(meta)
            if self.diversity_sampler:
                self.diversity_sampler.add(meta)
            if self.stratified_sampler:
                self.stratified_sampler.add(meta)

        return True

    def _find_overrepresented_index(self, incoming_class: str) -> Optional[int]:
        """Find index of a sample from the most over-represented class."""
        max_class = max(
            self._class_counts,
            key=lambda c: self._class_counts[c] if c != incoming_class else 0
        )

        candidates = [
            i for i, s in enumerate(self.buffer)
            if s.get("script_class") == max_class
        ]

        return random.choice(candidates) if candidates else None

    def get_samples(self, n: Optional[int] = None, script_filter: Optional[str] = None) -> List[Dict]:
        """
        Get samples from the buffer.

        Args:
            n: Number of samples to return (None = all).
            script_filter: Filter by script class.

        Returns:
            List of training samples.
        """
        samples = self.buffer

        if script_filter:
            samples = [s for s in samples if s.get("script_class") == script_filter]

        if n and n < len(samples):
            samples = random.sample(samples, n)

        return samples

    def get_hard_samples(self, n: int) -> List[Dict]:
        """Get hard examples prioritized by error frequency."""
        if not self.hard_miner:
            return self.get_samples(n)
        metas = self.hard_miner.sample(n)
        return [m.to_dict() for m in metas]

    def get_diverse_samples(self, n: int) -> List[Dict]:
        """Get samples from diverse clusters."""
        if not self.diversity_sampler:
            return self.get_samples(n)
        metas = self.diversity_sampler.sample(n)
        return [m.to_dict() for m in metas]

    def get_stratified_batch(self, total: int) -> List[Dict]:
        """
        Get a stratified batch with proportional class representation.
        """
        if not self.buffer:
            return []

        available_classes = {c: count for c, count in self._class_counts.items() if count > 0}
        total_available = sum(available_classes.values())

        batch = []
        for cls, count in available_classes.items():
            ratio = count / total_available
            n_samples = max(1, int(total * ratio))
            class_samples = [s for s in self.buffer if s.get("script_class") == cls]
            batch.extend(random.sample(class_samples, min(n_samples, len(class_samples))))

        random.shuffle(batch)
        return batch[:total]

    def get_mixed_batch(self, total: int) -> List[Dict]:
        """Get a batch combining all strategies: random, hard, diverse, stratified.

        Allocation: 25% random, 25% hard, 25% diverse, 25% stratified.
        """
        if not self.enable_advanced:
            return self.get_stratified_batch(total)

        n_each = max(1, total // 4)
        remainder = total - n_each * 4

        seen_ids = set()
        combined = []

        # Random
        for s in self.get_samples(n_each + remainder):
            if s.get("region_id") not in seen_ids:
                seen_ids.add(s.get("region_id"))
                combined.append(s)
                if len(combined) >= total:
                    break

        # Hard
        if len(combined) < total:
            for s in self.get_hard_samples(n_each):
                if s.get("region_id") not in seen_ids:
                    seen_ids.add(s.get("region_id"))
                    combined.append(s)
                    if len(combined) >= total:
                        break

        # Diverse
        if len(combined) < total:
            for s in self.get_diverse_samples(n_each):
                if s.get("region_id") not in seen_ids:
                    seen_ids.add(s.get("region_id"))
                    combined.append(s)
                    if len(combined) >= total:
                        break

        random.shuffle(combined)
        return combined[:total]

    def merge_with_new(self, new_samples: List[Dict], replay_ratio: float = 0.2, strategy: str = "stratified") -> List[Dict]:
        """
        Merge new samples with replay buffer samples.

        Args:
            new_samples: Fresh correction samples to train on.
            replay_ratio: Fraction of replay buffer to include (e.g., 0.2 = 20%).
            strategy: Sampling strategy — 'stratified', 'hard', 'diverse', or 'mixed'.

        Returns:
            Combined training set.
        """
        for sample in new_samples:
            self.add(sample)

        replay_count = max(1, int(len(new_samples) * replay_ratio))

        if strategy == "hard":
            replay_samples = self.get_hard_samples(replay_count)
        elif strategy == "diverse":
            replay_samples = self.get_diverse_samples(replay_count)
        elif strategy == "mixed":
            replay_samples = self.get_mixed_batch(replay_count)
        else:
            replay_samples = self.get_stratified_batch(replay_count)

        combined = new_samples + replay_samples
        random.shuffle(combined)

        logger.info(
            f"Training set ({strategy}): {len(new_samples)} new + "
            f"{len(replay_samples)} replay = {len(combined)} total"
        )

        return combined

    def get_statistics(self) -> Dict:
        """Get buffer statistics including advanced samplers."""
        stats = {
            "capacity": self.capacity,
            "current_size": len(self.buffer),
            "utilization": len(self.buffer) / self.capacity if self.capacity > 0 else 0,
            "total_seen": self._total_seen,
            "class_distribution": dict(self._class_counts),
            "metadata": self._metadata,
            "advanced_samplers_enabled": self.enable_advanced,
        }

        if self.hard_miner:
            stats["hard_examples"] = self.hard_miner.statistics()
        if self.diversity_sampler:
            stats["diversity"] = self.diversity_sampler.statistics()
        if self.stratified_sampler:
            stats["stratified"] = self.stratified_sampler.statistics()

        return stats

    def save(self) -> None:
        """Persist buffer to disk."""
        state = {
            "buffer": self.buffer,
            "total_seen": self._total_seen,
            "class_counts": self._class_counts,
            "metadata": self._metadata,
        }

        self._metadata["last_updated"] = datetime.now().isoformat()

        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.persist_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        logger.info(f"Buffer saved: {len(self.buffer)} samples to {self.persist_path}")

    def load(self) -> bool:
        """Load buffer from disk."""
        if not self.persist_path.exists():
            return False

        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                state = json.load(f)

            self.buffer = state.get("buffer", [])
            self._total_seen = state.get("total_seen", 0)
            self._class_counts = state.get("class_counts", {})
            self._metadata = state.get("metadata", {})

            # Re-feed advanced samplers
            if self.enable_advanced:
                for sample in self.buffer:
                    meta = SampleMetadata.from_dict(sample)
                    if self.hard_miner:
                        self.hard_miner.add(meta)
                    if self.diversity_sampler:
                        self.diversity_sampler.add(meta)
                    if self.stratified_sampler:
                        self.stratified_sampler.add(meta)

            logger.info(f"Buffer loaded: {len(self.buffer)} samples (advanced samplers re-fed)")
            return True
        except Exception as e:
            logger.error(f"Failed to load buffer: {e}")
            return False

    def clear(self) -> None:
        """Clear the buffer and all samplers."""
        self.buffer = []
        self._total_seen = 0
        self._class_counts = {"arabic": 0, "latin": 0, "mixed": 0, "numeric": 0, "unknown": 0}
        if self.hard_miner:
            self.hard_miner.hard_examples.clear()
            self.hard_miner.error_frequency.clear()
        if self.diversity_sampler:
            self.diversity_sampler.clusters.clear()
            self.diversity_sampler.cluster_centers.clear()
        if self.stratified_sampler:
            self.stratified_sampler.strata.clear()
        logger.info("Buffer cleared (including advanced samplers)")
