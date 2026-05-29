"""
Replay Buffer for continual learning.
Implements reservoir sampling to maintain a representative subset of historical data.
Prevents catastrophic forgetting during incremental TrOCR fine-tuning.
"""

import random
import json
import logging
from typing import List, Dict, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class ReplayBuffer:
    """
    Reservoir sampling-based replay buffer for continual learning.
    
    Maintains a fixed-size buffer of historical training samples that
    are combined with new corrections during fine-tuning to prevent
    catastrophic forgetting.
    """

    def __init__(
        self,
        capacity: int = 2000,
        persist_path: str = "./replay_buffer.json",
        stratify_by_script: bool = True,
        min_per_class: int = 50,
    ):
        """
        Args:
            capacity: Maximum number of samples to keep in buffer.
            persist_path: File path for saving/loading buffer state.
            stratify_by_script: Whether to maintain balanced representation
                               across script classes (arabic/latin/mixed).
            min_per_class: Minimum samples to retain per script class.
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

    def add(self, sample: Dict) -> bool:
        """
        Add a sample using reservoir sampling.
        
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
            # Buffer not full - always add
            self.buffer.append(sample)
            self._class_counts[script_class] = self._class_counts.get(script_class, 0) + 1
            self._metadata["total_accepted"] += 1
            return True
        else:
            # Reservoir sampling: replace with probability capacity/total_seen
            probability = self.capacity / self._total_seen
            
            # Stratified: ensure minimum per-class representation
            if self.stratify_by_script:
                current_count = self._class_counts.get(script_class, 0)
                class_ratio = current_count / self.capacity
                min_ratio = self.min_per_class / self.capacity
                
                if class_ratio <= min_ratio:
                    # Under-represented class - higher acceptance probability
                    probability = max(probability, min_ratio * 2)

            if random.random() < probability:
                # Find and replace a sample from over-represented class
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
                    return True

            self._metadata["total_rejected"] += 1
            return False

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
        
        # Shuffle final batch
        random.shuffle(batch)
        return batch[:total]

    def merge_with_new(self, new_samples: List[Dict], replay_ratio: float = 0.2) -> List[Dict]:
        """
        Merge new samples with replay buffer samples.
        
        Args:
            new_samples: Fresh correction samples to train on.
            replay_ratio: Fraction of replay buffer to include (e.g., 0.2 = 20%).
        
        Returns:
            Combined training set.
        """
        # Add new samples to buffer first
        for sample in new_samples:
            self.add(sample)
        
        # Get replay portion
        replay_count = max(1, int(len(new_samples) * replay_ratio))
        replay_samples = self.get_stratified_batch(replay_count)
        
        combined = new_samples + replay_samples
        random.shuffle(combined)
        
        logger.info(
            f"Training set: {len(new_samples)} new + {len(replay_samples)} replay = {len(combined)} total"
        )
        
        return combined

    def get_statistics(self) -> Dict:
        """Get buffer statistics."""
        return {
            "capacity": self.capacity,
            "current_size": len(self.buffer),
            "utilization": len(self.buffer) / self.capacity if self.capacity > 0 else 0,
            "total_seen": self._total_seen,
            "class_distribution": dict(self._class_counts),
            "metadata": self._metadata,
        }

    def save(self) -> None:
        """Persist buffer to disk."""
        state = {
            "buffer": self.buffer,
            "total_seen": self._total_seen,
            "class_counts": self._class_counts,
            "metadata": self._metadata,
        }
        
        self._metadata["last_updated"] = datetime.now().isoformat()
        
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
            
            logger.info(f"Buffer loaded: {len(self.buffer)} samples")
            return True
        except Exception as e:
            logger.error(f"Failed to load buffer: {e}")
            return False

    def clear(self) -> None:
        """Clear the buffer."""
        self.buffer = []
        self._total_seen = 0
        self._class_counts = {"arabic": 0, "latin": 0, "mixed": 0, "numeric": 0, "unknown": 0}
        logger.info("Buffer cleared")
