"""
Tests for the Deployment Manager module.
"""

import pytest
import json
import shutil
from pathlib import Path
from training.deployment_manager import DeploymentManager


class TestDeploymentManager:
    """Tests for the DeploymentManager class."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create a deployment manager with temp directory."""
        mgr = DeploymentManager(models_dir=str(tmp_path / "models"))
        return mgr

    @pytest.fixture
    def sample_model_dir(self, tmp_path):
        """Create a sample trained model directory."""
        model_dir = tmp_path / "sample_model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text('{"model_type": "trocr"}')
        (model_dir / "pytorch_model.bin").write_text("fake weights")
        return str(model_dir)

    def test_initialization(self, manager):
        """Test that manager creates required directories."""
        assert manager.production_dir.exists()
        assert manager.staging_dir.exists()
        assert manager.archive_dir.exists()

    def test_register_version(self, manager, sample_model_dir):
        """Test registering a new model version."""
        version = manager.register_version(
            model_path=sample_model_dir,
            version_name="v2.1-trained",
            base_model="trocr-base-handwritten",
            trained_on_count=500,
            cer_score=0.08,
            wer_score=0.12,
            medical_term_accuracy=0.85,
            training_duration=3600,
            notes="Weekly training with EWC",
        )
        
        assert version["version_id"].startswith("v001_")
        assert version["cer_score"] == 0.08
        assert version["is_active"] is False

    def test_register_creates_staging_copy(self, manager, sample_model_dir):
        """Test that staging copy is created."""
        manager.register_version(
            model_path=sample_model_dir,
            version_name="test",
            base_model="base",
            trained_on_count=100,
            cer_score=0.1,
            wer_score=0.15,
        )
        
        staged = list(manager.staging_dir.iterdir())
        assert len(staged) == 1
        assert (staged[0] / "config.json").exists()

    def test_promote_to_production(self, manager, sample_model_dir):
        """Test promoting a staged version."""
        version = manager.register_version(
            model_path=sample_model_dir,
            version_name="v2.0",
            base_model="base",
            trained_on_count=500,
            cer_score=0.07,
            wer_score=0.10,
        )
        
        result = manager.promote_to_production(version["version_id"])
        assert result["success"] is True

    def test_promote_rejects_regression(self, manager, sample_model_dir):
        """Test that large CER regression is rejected."""
        # Register good version
        v1 = manager.register_version(
            model_path=sample_model_dir,
            version_name="v1.0",
            base_model="base",
            trained_on_count=300,
            cer_score=0.05,
            wer_score=0.08,
        )
        manager.promote_to_production(v1["version_id"])
        
        # Register worse version
        v2 = manager.register_version(
            model_path=sample_model_dir,
            version_name="v2.0-worse",
            base_model="base",
            trained_on_count=500,
            cer_score=0.15,  # Much worse
            wer_score=0.20,
        )
        
        result = manager.promote_to_production(v2["version_id"], max_cer_regression=0.02)
        assert result["success"] is False
        assert "regression" in result["error"].lower()

    def test_get_active_version(self, manager, sample_model_dir):
        """Test getting the active version."""
        assert manager.get_active_version() is None
        
        v1 = manager.register_version(
            model_path=sample_model_dir,
            version_name="v1.0",
            base_model="base",
            trained_on_count=100,
            cer_score=0.1,
            wer_score=0.15,
        )
        manager.promote_to_production(v1["version_id"])
        
        active = manager.get_active_version()
        assert active is not None
        assert active["is_active"] is True

    def test_version_history(self, manager, sample_model_dir):
        """Test version history tracking."""
        for i in range(3):
            manager.register_version(
                model_path=sample_model_dir,
                version_name=f"v{i}.0",
                base_model="base",
                trained_on_count=100 * (i + 1),
                cer_score=0.1 - (i * 0.01),
                wer_score=0.15 - (i * 0.01),
            )
        
        history = manager.get_version_history()
        assert len(history) == 3

    def test_deployment_summary(self, manager, sample_model_dir):
        """Test deployment summary generation."""
        summary = manager.get_deployment_summary()
        assert summary["total_versions"] == 0
        assert summary["active_version"] is None
        
        v1 = manager.register_version(
            model_path=sample_model_dir,
            version_name="v1.0",
            base_model="base",
            trained_on_count=100,
            cer_score=0.08,
            wer_score=0.12,
        )
        manager.promote_to_production(v1["version_id"])
        
        summary = manager.get_deployment_summary()
        assert summary["total_versions"] == 1
        assert summary["active_version"] is not None
        assert summary["active_cer"] == 0.08
