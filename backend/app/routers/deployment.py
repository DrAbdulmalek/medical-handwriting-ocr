"""
Model Deployment Router
=======================
Endpoints for registering, activating, comparing, and managing
OCR model versions in a production deployment pipeline.

All endpoints operate against the ``model_versions`` table defined
in ``docker/init.sql`` and use raw SQLAlchemy ``text()`` queries.
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter(prefix="/deploy", tags=["deployment"])

# ─────────────────────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────────────────────


class ModelVersionBase(BaseModel):
    """Base fields shared across request/response models."""

    version_name: str = Field(
        ..., min_length=1, max_length=100,
        description="Human-readable version identifier, e.g. 'v2.3.0-FT'",
        examples=["v2.3.0-FT"],
    )
    base_model: Optional[str] = Field(
        None, max_length=200,
        description="Upstream model this version was fine-tuned from.",
        examples=["microsoft/trocr-base-handwritten"],
    )
    trained_on_count: int = Field(
        0, ge=0,
        description="Number of training samples used for fine-tuning.",
    )
    cer_score: Optional[float] = Field(
        None, ge=0.0, le=100.0,
        description="Character Error Rate (%) — lower is better.",
    )
    wer_score: Optional[float] = Field(
        None, ge=0.0, le=100.0,
        description="Word Error Rate (%) — lower is better.",
    )
    medical_term_accuracy: Optional[float] = Field(
        None, ge=0.0, le=100.0,
        description="Medical term recognition accuracy (%) — higher is better.",
    )
    training_duration: Optional[int] = Field(
        None, ge=0,
        description="Training time in seconds.",
    )
    notes: Optional[str] = Field(
        None, max_length=2000,
        description="Free-form notes about this version.",
    )


class ModelVersionCreate(ModelVersionBase):
    """Schema for registering a new model version."""

    pass


class ModelVersionOut(ModelVersionBase):
    """Full model version representation returned by the API."""

    id: uuid.UUID
    deployed_at: Optional[datetime] = None
    is_active: bool = False

    model_config = {"from_attributes": True}


class DeploymentStatus(BaseModel):
    """Response for the current deployment status."""

    status: str = Field(..., description="Overall deployment state.")
    active_model: Optional[uuid.UUID] = None
    active_version_name: Optional[str] = None
    active_base_model: Optional[str] = None
    cer_score: Optional[float] = None
    wer_score: Optional[float] = None
    medical_term_accuracy: Optional[float] = None
    deployed_at: Optional[datetime] = None
    total_versions: int = 0
    last_updated: Optional[datetime] = None


class MetricsComparison(BaseModel):
    """Performance metrics compared across all registered versions."""

    versions: List[ModelVersionOut]
    summary: dict = Field(
        default_factory=dict,
        description=(
            "Aggregate statistics: best/worst CER, WER, "
            "medical_term_accuracy, and overall count."
        ),
    )


class MessageResponse(BaseModel):
    """Generic success / info message."""

    message: str
    model_id: Optional[uuid.UUID] = None
    version_name: Optional[str] = None


# ─────────────────────────────────────────────────────────────
# Constants & Helpers
# ─────────────────────────────────────────────────────────────

SELECT_ALL_COLUMNS = (
    "id, version_name, base_model, trained_on_count, "
    "cer_score, wer_score, medical_term_accuracy, "
    "training_duration, deployed_at, is_active, notes"
)


def _row_to_dict(row) -> dict:
    """Convert a SQLAlchemy Row to a plain dict suitable for Pydantic."""
    return dict(row._mapping)


def _find_model_by_id(db: Session, model_id: uuid.UUID) -> dict:
    """Fetch a model version by primary key; raise 404 if missing."""
    result = db.execute(
        text(f"SELECT {SELECT_ALL_COLUMNS} FROM model_versions WHERE id = :id"),
        {"id": str(model_id)},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model version '{model_id}' not found.",
        )
    return _row_to_dict(row)


# ─────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────


@router.get(
    "/status",
    response_model=DeploymentStatus,
    summary="Current deployment status",
    description=(
        "Returns the currently active model version together with its "
        "key performance metrics.  If no model has been activated yet "
        "the fields ``active_model`` and ``active_version_name`` will "
        "be ``null``."
    ),
    responses={200: {"description": "Deployment status retrieved."}},
)
async def deployment_status(db: Session = Depends(get_db)):
    """Retrieve the status of the currently deployed model version."""

    # Total number of registered versions
    total_result = db.execute(text("SELECT COUNT(*) FROM model_versions"))
    total_versions = total_result.scalar() or 0

    # Currently active version (at most one)
    active_result = db.execute(
        text(f"SELECT {SELECT_ALL_COLUMNS} FROM model_versions "
             "WHERE is_active = TRUE LIMIT 1")
    )
    active_row = active_result.fetchone()

    if active_row is None:
        return DeploymentStatus(
            status="no_active_model",
            total_versions=total_versions,
        )

    active = _row_to_dict(active_row)
    return DeploymentStatus(
        status="active",
        active_model=active["id"],
        active_version_name=active["version_name"],
        active_base_model=active["base_model"],
        cer_score=active["cer_score"],
        wer_score=active["wer_score"],
        medical_term_accuracy=active["medical_term_accuracy"],
        deployed_at=active["deployed_at"],
        total_versions=total_versions,
        last_updated=active["deployed_at"],
    )


@router.get(
    "/models",
    response_model=List[ModelVersionOut],
    summary="List all model versions",
    description=(
        "Returns every registered model version, sorted by the requested "
        "column and direction (default: most recently deployed first)."
    ),
    responses={200: {"description": "List of model versions."}},
)
async def list_models(
    sort_by: Optional[str] = Query(
        "deployed_at",
        alias="sort",
        description="Column to sort by: deployed_at, version_name, cer_score, wer_score.",
    ),
    order: Optional[str] = Query(
        "desc",
        description="Sort order: 'asc' or 'desc'.",
    ),
    db: Session = Depends(get_db),
):
    """List all registered model versions with optional sorting."""

    allowed_sort = {
        "deployed_at", "version_name", "cer_score",
        "wer_score", "training_duration", "medical_term_accuracy",
    }
    col = sort_by if sort_by in allowed_sort else "deployed_at"
    direction = "DESC" if order.lower() == "desc" else "ASC"

    result = db.execute(
        text(f"SELECT {SELECT_ALL_COLUMNS} FROM model_versions "
             f"ORDER BY {col} {direction} NULLS LAST")
    )
    return [_row_to_dict(r) for r in result.fetchall()]


@router.get(
    "/models/{model_id}",
    response_model=ModelVersionOut,
    summary="Get a specific model version",
    description="Retrieve detailed information for a single model version by its UUID.",
    responses={
        200: {"description": "Model version found."},
        404: {"description": "Model version not found."},
    },
)
async def get_model(model_id: uuid.UUID, db: Session = Depends(get_db)):
    """Return detailed information about a specific model version."""
    return _find_model_by_id(db, model_id)


@router.post(
    "/models",
    response_model=ModelVersionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new model version",
    description=(
        "Create a new model version entry.  This is typically called after "
        "a training run completes.  The new version is **not** automatically "
        "activated — use ``POST /deploy/activate/{model_id}`` to promote it."
    ),
    responses={
        201: {"description": "Model version registered."},
        409: {"description": "A version with the same version_name already exists."},
    },
)
async def create_model(
    body: ModelVersionCreate,
    db: Session = Depends(get_db),
):
    """Register a new model version in the catalogue."""

    # Guard against duplicate version names
    dup = db.execute(
        text("SELECT id FROM model_versions WHERE version_name = :vn"),
        {"vn": body.version_name},
    )
    if dup.fetchone() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Version '{body.version_name}' already exists.",
        )

    new_id = str(uuid.uuid4())

    db.execute(
        text(
            "INSERT INTO model_versions "
            "(id, version_name, base_model, trained_on_count, "
            " cer_score, wer_score, medical_term_accuracy, "
            " training_duration, deployed_at, is_active, notes) "
            "VALUES "
            "(:id, :version_name, :base_model, :trained_on_count, "
            " :cer_score, :wer_score, :medical_term_accuracy, "
            " :training_duration, :deployed_at, :is_active, :notes)"
        ),
        {
            "id": new_id,
            "version_name": body.version_name,
            "base_model": body.base_model,
            "trained_on_count": body.trained_on_count,
            "cer_score": body.cer_score,
            "wer_score": body.wer_score,
            "medical_term_accuracy": body.medical_term_accuracy,
            "training_duration": body.training_duration,
            "deployed_at": None,
            "is_active": False,
            "notes": body.notes,
        },
    )
    db.commit()

    # Return the newly created row
    result = db.execute(
        text(f"SELECT {SELECT_ALL_COLUMNS} FROM model_versions WHERE id = :id"),
        {"id": new_id},
    )
    return _row_to_dict(result.fetchone())


@router.post(
    "/activate/{model_id}",
    response_model=MessageResponse,
    summary="Activate a model version",
    description=(
        "Set a model version as the **active** production model.  Any "
        "previously active version is automatically deactivated.  "
        "The ``deployed_at`` timestamp is set to the current time."
    ),
    responses={
        200: {"description": "Model activated."},
        404: {"description": "Model version not found."},
    },
)
async def activate_model(model_id: uuid.UUID, db: Session = Depends(get_db)):
    """Promote a model version to active production status."""

    model = _find_model_by_id(db, model_id)

    now = datetime.now(timezone.utc)

    # Deactivate any currently active version
    db.execute(
        text("UPDATE model_versions "
             "SET is_active = FALSE, deployed_at = NULL "
             "WHERE is_active = TRUE")
    )

    # Activate the target
    db.execute(
        text("UPDATE model_versions "
             "SET is_active = TRUE, deployed_at = :now "
             "WHERE id = :id"),
        {"id": str(model_id), "now": now},
    )
    db.commit()

    return MessageResponse(
        message=f"Model '{model['version_name']}' is now active.",
        model_id=model_id,
        version_name=model["version_name"],
    )


@router.post(
    "/rollback/{model_id}",
    response_model=MessageResponse,
    summary="Rollback to a previous model version",
    description=(
        "Immediately switch the active production model to the specified "
        "(typically older) version.  This deactivates the current model "
        "and activates the target version."
    ),
    responses={
        200: {"description": "Rollback successful."},
        404: {"description": "Model version not found."},
    },
)
async def rollback_model(model_id: uuid.UUID, db: Session = Depends(get_db)):
    """Roll back the production model to the specified version."""

    model = _find_model_by_id(db, model_id)

    now = datetime.now(timezone.utc)

    # Deactivate current
    db.execute(
        text("UPDATE model_versions "
             "SET is_active = FALSE, deployed_at = NULL "
             "WHERE is_active = TRUE")
    )

    # Activate the rollback target
    db.execute(
        text("UPDATE model_versions "
             "SET is_active = TRUE, deployed_at = :now "
             "WHERE id = :id"),
        {"id": str(model_id), "now": now},
    )
    db.commit()

    return MessageResponse(
        message=f"Rolled back to model '{model['version_name']}'.",
        model_id=model_id,
        version_name=model["version_name"],
    )


@router.delete(
    "/models/{model_id}",
    response_model=MessageResponse,
    summary="Delete a model version",
    description=(
        "Remove a model version from the catalogue.  **Only inactive "
        "versions can be deleted.**  Attempting to delete the currently "
        "active model returns a 409 Conflict."
    ),
    responses={
        200: {"description": "Model version deleted."},
        404: {"description": "Model version not found."},
        409: {"description": "Cannot delete the active model version."},
    },
)
async def delete_model(model_id: uuid.UUID, db: Session = Depends(get_db)):
    """Delete a model version that is NOT currently active."""

    model = _find_model_by_id(db, model_id)

    if model["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cannot delete the active model version. "
                "Activate a different version first."
            ),
        )

    db.execute(
        text("DELETE FROM model_versions WHERE id = :id"),
        {"id": str(model_id)},
    )
    db.commit()

    return MessageResponse(
        message=f"Model version '{model['version_name']}' deleted.",
        model_id=model_id,
        version_name=model["version_name"],
    )


@router.get(
    "/metrics",
    response_model=MetricsComparison,
    summary="Compare model performance metrics",
    description=(
        "Return every registered model version together with aggregate "
        "summary statistics (best / worst CER, WER, and medical-term "
        "accuracy, plus averages).  Useful for deciding which version "
        "to promote to production."
    ),
    responses={200: {"description": "Metrics comparison returned."}},
)
async def compare_metrics(db: Session = Depends(get_db)):
    """Compare performance metrics across all model versions."""

    result = db.execute(
        text(f"SELECT {SELECT_ALL_COLUMNS} FROM model_versions "
             "ORDER BY deployed_at DESC NULLS LAST")
    )
    versions = [_row_to_dict(r) for r in result.fetchall()]

    # Compute summary statistics over versions that have metric data
    with_cer = [v for v in versions if v["cer_score"] is not None]
    with_wer = [v for v in versions if v["wer_score"] is not None]
    with_med = [v for v in versions if v["medical_term_accuracy"] is not None]

    summary: dict = {
        "total_versions": len(versions),
        "versions_with_metrics": len(with_cer),
    }

    if with_cer:
        cer_vals = [v["cer_score"] for v in with_cer]
        summary["cer_best"] = min(cer_vals)
        summary["cer_best_version"] = min(
            with_cer, key=lambda v: v["cer_score"]
        )["version_name"]
        summary["cer_worst"] = max(cer_vals)
        summary["cer_worst_version"] = max(
            with_cer, key=lambda v: v["cer_score"]
        )["version_name"]
        summary["cer_avg"] = round(sum(cer_vals) / len(cer_vals), 2)

    if with_wer:
        wer_vals = [v["wer_score"] for v in with_wer]
        summary["wer_best"] = min(wer_vals)
        summary["wer_best_version"] = min(
            with_wer, key=lambda v: v["wer_score"]
        )["version_name"]
        summary["wer_worst"] = max(wer_vals)
        summary["wer_worst_version"] = max(
            with_wer, key=lambda v: v["wer_score"]
        )["version_name"]
        summary["wer_avg"] = round(sum(wer_vals) / len(wer_vals), 2)

    if with_med:
        med_vals = [v["medical_term_accuracy"] for v in with_med]
        summary["medical_term_accuracy_best"] = max(med_vals)
        summary["medical_term_accuracy_best_version"] = max(
            with_med, key=lambda v: v["medical_term_accuracy"]
        )["version_name"]
        summary["medical_term_accuracy_worst"] = min(med_vals)
        summary["medical_term_accuracy_worst_version"] = min(
            with_med, key=lambda v: v["medical_term_accuracy"]
        )["version_name"]
        summary["medical_term_accuracy_avg"] = round(
            sum(med_vals) / len(med_vals), 2
        )

    return MetricsComparison(versions=versions, summary=summary)
