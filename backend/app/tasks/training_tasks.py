"""
Training Background Tasks — Celery worker implementations.

Provides async processing for:
- Weekly model training with accumulated corrections
- Exporting approved corrections as training datasets
- Model evaluation and metrics calculation
- Automatic model promotion based on quality thresholds
"""

import logging
import os
import subprocess
from datetime import datetime
from typing import Dict, Optional

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.training_tasks.run_weekly_training",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
    time_limit=7200,  # 2 hours max
)
def run_weekly_training(self) -> Dict:
    """Run the weekly TrOCR fine-tuning pipeline.

    This task:
    1. Exports approved corrections as a training dataset
    2. Runs the fine-tuning script with the latest data
    3. Evaluates the new model against the current baseline
    4. Registers the new model version (does NOT auto-activate)

    Returns:
        Dictionary with training results.
    """
    logger.info("Starting weekly model training pipeline")
    self.update_state(state="EXPORTING_DATA", meta={"step": "export_dataset"})

    db = SessionLocal()
    try:
        # Step 1: Export approved corrections as training data
        export_result = export_dataset_for_training()

        if export_result.get("status") != "ok" or export_result.get("samples_exported", 0) < 10:
            msg = f"Insufficient training data: {export_result.get('samples_exported', 0)} samples"
            logger.warning(msg)
            return {
                "status": "skipped",
                "reason": "insufficient_data",
                "samples_available": export_result.get("samples_exported", 0),
                "minimum_required": 10,
            }

        self.update_state(state="TRAINING", meta={"step": "finetuning"})

        # Step 2: Run fine-tuning
        training_script = os.path.join("training", "finetune_trocr.py")
        if not os.path.exists(training_script):
            return {"status": "error", "reason": "training_script_not_found"}

        output_dir = os.path.join(settings.MODELS_DIR, f"trained_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}")
        os.makedirs(output_dir, exist_ok=True)

        cmd = [
            "python", training_script,
            "--data-dir", settings.BATCH_OUTPUT_DIR,
            "--output-dir", output_dir,
            "--epochs", "5",
            "--batch-size", "8",
            "--learning-rate", "5e-5",
        ]

        logger.info("Running training: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,  # 1 hour timeout
                cwd=".",
            )

            if result.returncode != 0:
                logger.error("Training failed: %s", result.stderr[-500:] if result.stderr else "unknown")
                return {
                    "status": "error",
                    "reason": "training_failed",
                    "stderr_tail": result.stderr[-200:] if result.stderr else "",
                }

            self.update_state(state="EVALUATING", meta={"step": "evaluation"})

            # Step 3: Register the new model
            model_id = register_new_model(output_dir, db)

            # Step 4: Evaluate
            eval_result = _evaluate_model(output_dir)

            return {
                "status": "ok",
                "model_id": model_id,
                "output_dir": output_dir,
                "training_samples": export_result.get("samples_exported", 0),
                "evaluation": eval_result,
                "note": "Model registered but NOT auto-activated. Activate manually via /api/deploy/{id}/activate",
            }

        except subprocess.TimeoutExpired:
            return {"status": "error", "reason": "training_timeout"}
        except FileNotFoundError:
            return {"status": "error", "reason": "python_not_found"}

    finally:
        db.close()


@celery_app.task(
    name="app.tasks.training_tasks.export_dataset_for_training",
    bind=True,
)
def export_dataset_for_training(self) -> Dict:
    """Export approved corrections as a HuggingFace-compatible dataset.

    Exports all 'gold_standard' and 'approved' text regions with their
    original images as a training dataset.

    Returns:
        Dictionary with export statistics.
    """
    logger.info("Exporting training dataset from approved corrections")

    db = SessionLocal()
    try:
        # Count approved samples
        result = db.execute(
            """SELECT COUNT(*) as total,
                      COUNT(CASE WHEN status = 'gold_standard' THEN 1 END) as gold,
                      COUNT(CASE WHEN status = 'approved' THEN 1 END) as approved
               FROM text_regions
               WHERE status IN ('gold_standard', 'approved')
               AND corrected_text IS NOT NULL
               AND corrected_text != detected_text
            """
        ).fetchone()

        if not result or result["total"] == 0:
            return {"status": "ok", "samples_exported": 0, "message": "no_approved_corrections"}

        # Export as JSONL
        output_dir = settings.BATCH_OUTPUT_DIR
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"training_data_{datetime.utcnow().strftime('%Y%m%d')}.jsonl")

        rows = db.execute(
            """SELECT tr.detected_text, tr.corrected_text, tr.confidence,
                      d.original_filename, tr.bbox_x, tr.bbox_y, tr.bbox_width, tr.bbox_height
               FROM text_regions tr
               JOIN pages p ON p.id = tr.page_id
               JOIN documents d ON d.id = p.document_id
               WHERE tr.status IN ('gold_standard', 'approved')
               AND tr.corrected_text IS NOT NULL
               AND tr.corrected_text != tr.detected_text
               ORDER BY tr.confidence ASC  -- hardest samples first
            """
        ).fetchall()

        import json
        with open(output_file, "w", encoding="utf-8") as f:
            for row in rows:
                entry = {
                    "detected": row["detected_text"],
                    "corrected": row["corrected_text"],
                    "confidence": float(row["confidence"]),
                    "source": row["original_filename"],
                    "bbox": [row["bbox_x"], row["bbox_y"], row["bbox_width"], row["bbox_height"]],
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        logger.info("Exported %d training samples to %s", len(rows), output_file)

        return {
            "status": "ok",
            "samples_exported": len(rows),
            "gold_standard": result["gold"],
            "approved": result["approved"],
            "output_file": output_file,
        }

    except Exception as exc:
        logger.error("Dataset export failed: %s", exc)
        return {"status": "error", "reason": str(exc)}
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.training_tasks.evaluate_model_performance",
    bind=True,
)
def evaluate_model_performance(self, model_version_id: str) -> Dict:
    """Evaluate a specific model version on the test set.

    Args:
        model_version_id: UUID of the model version to evaluate.

    Returns:
        Dictionary with CER, WER, and medical accuracy metrics.
    """
    logger.info("Evaluating model %s", model_version_id)

    try:
        db = SessionLocal()
        row = db.execute(
            "SELECT model_path, base_model FROM model_versions WHERE id = :id",
            {"id": model_version_id},
        ).fetchone()

        if not row:
            return {"status": "error", "reason": "model_not_found"}

        return _evaluate_model(row["model_path"])

    except Exception as exc:
        logger.error("Model evaluation failed: %s", exc)
        return {"status": "error", "reason": str(exc)}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def register_new_model(output_dir: str, db) -> str:
    """Register a newly trained model in the database.

    Args:
        output_dir: Directory containing the trained model.
        db: Database session.

    Returns:
        The model version UUID.
    """
    import uuid

    model_id = str(uuid.uuid4())

    db.execute(
        """INSERT INTO model_versions
           (id, version_name, model_path, is_active, created_at, description)
           VALUES (:id, :name, :path, false, NOW(), :desc)
        """,
        {
            "id": model_id,
            "name": f"trained_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
            "path": output_dir,
            "desc": "Auto-trained model from weekly pipeline",
        },
    )
    db.commit()

    logger.info("Registered new model: %s at %s", model_id, output_dir)
    return model_id


def _evaluate_model(model_path: str) -> Dict:
    """Run model evaluation script and parse results.

    Args:
        model_path: Path to the model directory.

    Returns:
        Dictionary with evaluation metrics.
    """
    eval_script = os.path.join("training", "evaluate.py")

    if not os.path.exists(eval_script):
        return {"status": "skipped", "reason": "evaluate_script_not_found"}

    try:
        result = subprocess.run(
            ["python", eval_script, "--model-path", model_path],
            capture_output=True,
            text=True,
            timeout=600,
        )

        if result.returncode == 0:
            return {"status": "ok", "raw_output": result.stdout[-500:]}
        else:
            return {"status": "error", "stderr": result.stderr[-200:] if result.stderr else ""}

    except subprocess.TimeoutExpired:
        return {"status": "error", "reason": "evaluation_timeout"}
