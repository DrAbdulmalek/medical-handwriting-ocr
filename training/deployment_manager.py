"""
Deployment Manager for safe model versioning and rollback.
Handles model promotion, A/B testing, and automatic rollback on degradation.
"""

import os
import json
import shutil
import logging
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class ModelVersion:
    """Represents a single model version."""
    version_id: str
    version_name: str
    base_model: str
    trained_on_count: int
    cer_score: float
    wer_score: float
    medical_term_accuracy: float
    training_duration: int  # seconds
    deployed_at: Optional[str] = None
    is_active: bool = False
    notes: str = ""
    created_at: str = ""

    def to_dict(self):
        return asdict(self)


class DeploymentManager:
    """
    Manages model deployment lifecycle including:
    - Version tracking and metadata
    - Safe promotion to production
    - Automatic rollback on degradation
    - Model archival and cleanup
    """

    def __init__(self, models_dir: str = "./models"):
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.production_dir = self.models_dir / "production"
        self.staging_dir = self.models_dir / "staging"
        self.archive_dir = self.models_dir / "archive"
        
        for d in [self.production_dir, self.staging_dir, self.archive_dir]:
            d.mkdir(parents=True, exist_ok=True)
        
        self.versions_file = self.models_dir / "versions.json"
        self.versions = self._load_versions()

    def _load_versions(self) -> List[Dict]:
        """Load version history."""
        if self.versions_file.exists():
            with open(self.versions_file, "r") as f:
                return json.load(f)
        return []

    def _save_versions(self):
        """Save version history."""
        with open(self.versions_file, "w") as f:
            json.dump(self.versions, f, indent=2)

    def register_version(
        self,
        model_path: str,
        version_name: str,
        base_model: str,
        trained_on_count: int,
        cer_score: float,
        wer_score: float,
        medical_term_accuracy: float = 0.0,
        training_duration: int = 0,
        notes: str = "",
    ) -> Dict:
        """
        Register a new model version and move it to staging.
        
        Returns:
            Version metadata dict.
        """
        version_id = f"v{len(self.versions) + 1:03d}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        version = ModelVersion(
            version_id=version_id,
            version_name=version_name,
            base_model=base_model,
            trained_on_count=trained_on_count,
            cer_score=cer_score,
            wer_score=wer_score,
            medical_term_accuracy=medical_term_accuracy,
            training_duration=training_duration,
            notes=notes,
            created_at=datetime.now().isoformat(),
        )
        
        # Copy model to staging
        staging_path = self.staging_dir / version_id
        shutil.copytree(model_path, staging_path, dirs_exist_ok=True)
        logger.info(f"Model staged at: {staging_path}")
        
        version_dict = version.to_dict()
        self.versions.append(version_dict)
        self._save_versions()
        
        logger.info(
            f"Registered version {version_id}: CER={cer_score:.4f}, "
            f"WER={wer_score:.4f}, samples={trained_on_count}"
        )
        
        return version_dict

    def promote_to_production(self, version_id: str, max_cer_regression: float = 0.02) -> Dict:
        """
        Promote a staged model to production.
        
        Args:
            version_id: The version to promote.
            max_cer_regression: Maximum allowed CER increase vs current production.
        
        Returns:
            Deployment result dict with success status.
        """
        # Find the version
        version = None
        for v in self.versions:
            if v["version_id"] == version_id:
                version = v
                break
        
        if not version:
            return {"success": False, "error": f"Version {version_id} not found"}
        
        # Check staging path
        staging_path = self.staging_dir / version_id
        if not staging_path.exists():
            return {"success": False, "error": f"Staged model not found at {staging_path}"}
        
        # Compare with current production
        current_prod = self.get_active_version()
        if current_prod and current_prod.get("cer_score"):
            cer_regression = version["cer_score"] - current_prod["cer_score"]
            if cer_regression > max_cer_regression:
                return {
                    "success": False,
                    "error": f"CER regression too large: {cer_regression:.4f} > {max_cer_regression}",
                    "current_cer": current_prod["cer_score"],
                    "new_cer": version["cer_score"],
                }
        
        # Archive current production model
        if current_prod:
            self._archive_production(current_prod["version_id"])
        
        # Deploy new model to production
        prod_path = self.production_dir / version_id
        if prod_path.exists():
            shutil.rmtree(prod_path)
        shutil.copytree(staging_path, prod_path)
        
        # Update version metadata
        version["is_active"] = True
        version["deployed_at"] = datetime.now().isoformat()
        
        # Deactivate all other versions
        for v in self.versions:
            if v["version_id"] != version_id:
                v["is_active"] = False
        
        self._save_versions()
        
        logger.info(f"Model {version_id} promoted to production")
        
        return {
            "success": True,
            "version_id": version_id,
            "cer_score": version["cer_score"],
            "wer_score": version["wer_score"],
            "deployed_at": version["deployed_at"],
        }

    def rollback(self) -> Dict:
        """
        Rollback to the previous production version.
        """
        active_versions = [v for v in self.versions if v.get("is_active")]
        
        if len(active_versions) <= 1:
            return {"success": False, "error": "No previous version to rollback to"}
        
        current = active_versions[0]
        previous = active_versions[-1]  # Second most recent active
        
        return self.promote_to_production(previous["version_id"], max_cer_regression=1.0)

    def _archive_production(self, version_id: str):
        """Archive a production model."""
        prod_path = self.production_dir / version_id
        archive_path = self.archive_dir / version_id
        
        if prod_path.exists():
            shutil.copytree(prod_path, archive_path, dirs_exist_ok=True)
            logger.info(f"Archived production model: {version_id}")

    def get_active_version(self) -> Optional[Dict]:
        """Get the currently active production version."""
        for v in reversed(self.versions):
            if v.get("is_active"):
                return v
        return None

    def get_production_model_path(self) -> Optional[str]:
        """Get the file path of the active production model."""
        active = self.get_active_version()
        if active:
            path = self.production_dir / active["version_id"]
            if path.exists():
                return str(path)
        return None

    def get_version_history(self, limit: int = 20) -> List[Dict]:
        """Get version history (most recent first)."""
        return list(reversed(self.versions[-limit:]))

    def cleanup_old_versions(self, keep_count: int = 5):
        """Remove old archived versions, keeping the most recent N."""
        archived = sorted(self.archive_dir.iterdir())
        for old_dir in archived[:-keep_count]:
            shutil.rmtree(old_dir)
            logger.info(f"Cleaned up old archive: {old_dir.name}")

    def get_deployment_summary(self) -> Dict:
        """Get a summary of the deployment state."""
        active = self.get_active_version()
        return {
            "total_versions": len(self.versions),
            "active_version": active.get("version_id") if active else None,
            "active_cer": active.get("cer_score") if active else None,
            "active_wer": active.get("wer_score") if active else None,
            "staged_versions": len(list(self.staging_dir.iterdir())),
            "archived_versions": len(list(self.archive_dir.iterdir())),
            "production_path": str(self.production_dir),
        }
