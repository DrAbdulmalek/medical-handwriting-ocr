"""
Load training data from the training-hub into the HF Space.

This module is imported by the HF Gradio app to:
1. Load ground truth patterns at startup (for auto-correction)
2. Load model configs
3. Provide training data statistics

The data is read from: /data/training_hub/ (HF Space persistent storage)
Which is synced from GitHub via scripts/sync_hf_github.py
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ── HF Space persistent data directory ──
# This path survives container restarts on HF Spaces
DATA_DIR = Path(os.environ.get("TRAINING_HUB_DIR", "/data/training_hub"))


def load_config() -> Optional[Dict]:
    """Load config.yaml from the training hub data directory."""
    config_path = DATA_DIR / "config.yaml"
    if not config_path.exists():
        # Fallback: try local app directory
        config_path = Path(__file__).parent.parent.parent / "data" / "config.yaml"

    if not config_path.exists():
        logger.info("No config.yaml found (training hub not synced yet)")
        return None

    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except ImportError:
        logger.warning("PyYAML not installed — config.yaml not loaded")
        return None
    except Exception as e:
        logger.error("Failed to load config: %s", e)
        return None


def load_ground_truth() -> Dict[str, str]:
    """
    Load ground truth corrections as a raw→corrected mapping.
    Returns: {raw_text: corrected_text}
    Used by the OCR engine for auto-correction.
    """
    gt_dir = DATA_DIR / "training_data" / "ground_truth"
    if not gt_dir.exists():
        gt_dir = Path(__file__).parent.parent.parent / "data" / "ground_truth"

    if not gt_dir.exists():
        logger.info("No ground truth directory found")
        return {}

    mapping: Dict[str, str] = {}
    for jsonl_file in sorted(gt_dir.glob("*.jsonl")):
        try:
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    gt_text = entry.get("ground_truth", "")
                    engine_preds = entry.get("engine_predictions", {})
                    # Add ground truth as correction for each engine prediction
                    if gt_text:
                        mapping[gt_text] = gt_text  # identity: GT is correct
                        for engine_text in engine_preds.values():
                            if engine_text and engine_text != gt_text:
                                mapping[engine_text] = gt_text
            logger.info("Loaded GT from %s (%d patterns total)", jsonl_file.name, len(mapping))
        except Exception as e:
            logger.error("Failed to load %s: %s", jsonl_file.name, e)

    return mapping


def load_word_corrections(limit: int = 5000) -> List[Dict]:
    """
    Load recent word-level corrections from the hub.
    Used for training data preview and statistics.
    """
    crops_dir = DATA_DIR / "training_data" / "word_crops"
    if not crops_dir.exists():
        crops_dir = Path(__file__).parent.parent.parent / "data" / "word_crops"

    if not crops_dir.exists():
        return []

    corrections: List[Dict] = []
    for jsonl_file in sorted(crops_dir.glob("corrections_*.jsonl"), reverse=True):
        try:
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    corrections.append(json.loads(line))
                    if len(corrections) >= limit:
                        return corrections
        except Exception as e:
            logger.error("Failed to load %s: %s", jsonl_file.name, e)

    return corrections


def get_training_stats() -> Dict:
    """Get training data statistics for the dashboard."""
    config = load_config()
    gt = load_ground_truth()
    corrections = load_word_corrections(limit=10000)

    # Count by language
    lang_counts = {"arabic": 0, "english": 0, "mixed": 0, "numeric": 0, "unknown": 0}
    changed_count = 0
    for c in corrections:
        lang_counts[c.get("language", "unknown")] = lang_counts.get(c.get("language", "unknown"), 0) + 1
        if c.get("is_changed", False):
            changed_count += 1

    # Model readiness
    model_status = {}
    if config and "models" in config:
        for model_id, model_cfg in config["models"].items():
            data_ref = model_cfg.get("training_data_ref", "")
            data_path = DATA_DIR / data_ref
            file_count = len(list(data_path.glob("*.jsonl"))) if data_path.exists() else 0
            min_samples = model_cfg.get("min_samples_for_training", 50)

            model_status[model_id] = {
                "name": model_cfg.get("name", model_id),
                "data_files": file_count,
                "ready_for_training": file_count >= 1 and len(corrections) >= min_samples,
                "min_samples": min_samples,
                "current_samples": len(corrections),
                "training_data_ref": data_ref,
                "hf_model_id": model_cfg.get("hf_model_id"),
            }

    return {
        "ground_truth_patterns": len(gt),
        "total_corrections": len(corrections),
        "changed_corrections": changed_count,
        "language_distribution": lang_counts,
        "model_readiness": model_status,
        "config_loaded": config is not None,
        "data_dir": str(DATA_DIR),
        "data_dir_exists": DATA_DIR.exists(),
    }


def get_model_configs() -> Dict[str, Dict]:
    """Load model configuration files."""
    configs_dir = DATA_DIR / "models" / "configs"
    if not configs_dir.exists():
        configs_dir = Path(__file__).parent.parent.parent / "data" / "model_configs"

    configs = {}
    if not configs_dir.exists():
        return configs

    for f in configs_dir.glob("*.yaml"):
        try:
            import yaml
            with open(f, "r", encoding="utf-8") as fh:
                configs[f.stem] = yaml.safe_load(fh)
        except Exception:
            pass
    for f in configs_dir.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                configs[f.stem] = json.load(fh)
        except Exception:
            pass

    return configs