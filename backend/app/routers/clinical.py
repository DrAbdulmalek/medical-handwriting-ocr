"""
Clinical Decision Support Router
==================================
Endpoints for medical guideline tracking, clinical QA, drug interaction
checking, dosage validation, and real-time processing progress tracking.
"""

import logging
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/clinical", tags=["clinical"])


# ─────────────────────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────────────────────

class DrugInteractionRequest(BaseModel):
    """Request body for drug interaction checking."""
    drugs: List[str] = Field(..., min_length=2, description="List of drug names to check")

    class Config:
        json_schema_extra = {
            "example": {
                "drugs": ["Warfarin", "Aspirin", "Metformin"]
            }
        }


class DosageValidationRequest(BaseModel):
    """Request body for dosage validation."""
    drug_name: str = Field(..., description="Name of the drug")
    dose: float = Field(..., gt=0, description="Prescribed dose in mg")
    frequency: str = Field(default="once daily", description="Dosing frequency")
    patient_weight: Optional[float] = Field(default=None, gt=0, description="Patient weight in kg")
    patient_age: Optional[int] = Field(default=None, gt=0, description="Patient age in years")
    renal_function: Optional[str] = Field(default=None, description="Renal function: normal, mild_impairment, moderate_impairment, severe_impairment")


class GuidelineSubscribeRequest(BaseModel):
    """Request body for guideline subscription."""
    condition: str = Field(..., description="Medical condition to track")
    callback_url: Optional[str] = Field(default=None, description="Webhook URL for notifications")
    sources: Optional[List[str]] = Field(default=None, description="Specific sources to track")


# ─────────────────────────────────────────────────────────────
# Guidelines Endpoints
# ─────────────────────────────────────────────────────────────

@router.get(
    "/guidelines",
    summary="Get latest medical guidelines",
    description="Retrieve the latest medical guidelines from tracked sources.",
)
async def get_guidelines(
    condition: Optional[str] = Query(default=None, description="Filter by condition"),
    source: Optional[str] = Query(default=None, description="Filter by source (WHO, CDC, AHA, etc.)"),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Get latest medical guidelines."""
    try:
        from app.clinical.guideline_tracker import get_guideline_tracker

        tracker = get_guideline_tracker()
        guidelines = tracker.get_latest_guidelines(condition=condition)

        if source:
            guidelines = [g for g in guidelines if g.source.name == source.upper()]

        guidelines = guidelines[:limit]

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": f"Retrieved {len(guidelines)} guideline(s)",
                "data": {
                    "total": len(guidelines),
                    "guidelines": [
                        {
                            "id": g.id,
                            "title": g.title,
                            "source": g.source.value,
                            "condition": g.condition,
                            "version": g.version,
                            "published_date": str(g.published_date) if g.published_date else None,
                            "summary": g.summary[:300] + "..." if g.summary and len(g.summary) > 300 else g.summary,
                            "url": g.url,
                        }
                        for g in guidelines
                    ],
                },
            },
        )

    except Exception as e:
        logger.error("Guideline retrieval failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Guideline retrieval failed: {str(e)}")


@router.get(
    "/guidelines/updates",
    summary="Check for guideline updates",
    description="Check tracked sources for new or updated medical guidelines.",
)
async def check_guideline_updates(
    source: Optional[str] = Query(default=None, description="Check specific source only"),
    since: Optional[str] = Query(default=None, description="ISO date string to check updates since"),
):
    """Check for guideline updates."""
    try:
        from app.clinical.guideline_tracker import get_guideline_tracker

        tracker = get_guideline_tracker()
        updates = tracker.check_updates(source=source)

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": f"Found {len(updates)} update(s)",
                "data": {
                    "total_updates": len(updates),
                    "updates": [
                        {
                            "guideline_id": u.guideline_id,
                            "title": u.title,
                            "update_type": u.update_type,
                            "description": u.description,
                            "previous_version": u.previous_version,
                            "new_version": u.new_version,
                            "effective_date": str(u.effective_date) if u.effective_date else None,
                        }
                        for u in updates
                    ],
                },
            },
        )

    except Exception as e:
        logger.error("Guideline update check failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Update check failed: {str(e)}")


@router.post(
    "/guidelines/subscribe",
    summary="Subscribe to guideline updates",
    description="Subscribe to receive notifications when guidelines for a condition are updated.",
    status_code=201,
)
async def subscribe_guidelines(request: GuidelineSubscribeRequest):
    """Subscribe to guideline updates for a specific condition."""
    try:
        from app.clinical.guideline_tracker import get_guideline_tracker

        tracker = get_guideline_tracker()
        subscription = tracker.subscribe_condition(
            condition=request.condition,
            callback_url=request.callback_url,
        )

        return JSONResponse(
            status_code=201,
            content={
                "success": True,
                "message": f"Subscribed to updates for '{request.condition}'",
                "data": {
                    "subscription_id": str(subscription.id),
                    "condition": subscription.condition,
                    "callback_url": subscription.callback_url,
                    "created_at": str(subscription.created_at),
                },
            },
        )

    except Exception as e:
        logger.error("Guideline subscription failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Subscription failed: {str(e)}")


# ─────────────────────────────────────────────────────────────
# Drug Interaction & Dosage Endpoints
# ─────────────────────────────────────────────────────────────

@router.post(
    "/drug/interactions",
    summary="Check drug interactions",
    description="Check for potential drug-drug interactions between a list of medications.",
)
async def check_drug_interactions(request: DrugInteractionRequest):
    """Check for drug-drug interactions."""
    try:
        from app.clinical.clinical_qa import get_clinical_qa

        qa_engine = get_clinical_qa()
        report = qa_engine.check_drug_interactions(drug_list=request.drugs)

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": f"Checked {len(request.drugs)} drug(s), found {len(report.interactions)} interaction(s)",
                "data": report.model_dump(),
            },
        )

    except Exception as e:
        logger.error("Drug interaction check failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Interaction check failed: {str(e)}")


@router.post(
    "/dosage/validate",
    summary="Validate drug dosage",
    description="Validate a prescribed drug dosage against patient parameters and clinical guidelines.",
)
async def validate_dosage(request: DosageValidationRequest):
    """Validate drug dosage for a patient."""
    try:
        from app.clinical.clinical_qa import get_clinical_qa

        qa_engine = get_clinical_qa()
        validation = qa_engine.validate_dosage(
            drug=request.drug_name,
            dose=request.dose,
            frequency=request.frequency,
            patient_weight=request.patient_weight,
            patient_age=request.patient_age,
            renal_function=request.renal_function,
        )

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": validation.model_dump(),
            },
        )

    except Exception as e:
        logger.error("Dosage validation failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Dosage validation failed: {str(e)}")


# ─────────────────────────────────────────────────────────────
# Clinical QA Endpoint
# ─────────────────────────────────────────────────────────────

@router.post(
    "/qa/ask",
    summary="Ask a clinical question",
    description=(
        "Ask a clinical question and receive an evidence-based answer "
        "with citations from indexed medical literature."
    ),
)
async def ask_clinical_question(
    question: str = Query(..., description="Clinical question"),
    patient_context: Optional[str] = Query(default=None, description="Optional patient context for personalized answers"),
):
    """Answer a clinical question with evidence-based support."""
    try:
        from app.clinical.clinical_qa import get_clinical_qa

        qa_engine = get_clinical_qa()
        answer = qa_engine.ask_clinical_question(
            question=question,
            patient_context=patient_context,
        )

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": {
                    "question": question,
                    "answer": answer.answer,
                    "confidence": answer.confidence,
                    "evidence": [
                        {
                            "source": e.source,
                            "title": e.title,
                            "relevance": e.relevance_score,
                            "excerpt": e.excerpt[:200] + "..." if e.excerpt and len(e.excerpt) > 200 else e.excerpt,
                        }
                        for e in answer.evidence
                    ],
                    "warnings": answer.warnings,
                },
            },
        )

    except Exception as e:
        logger.error("Clinical QA failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Clinical QA failed: {str(e)}")


# ─────────────────────────────────────────────────────────────
# Progress Tracking Endpoints
# ─────────────────────────────────────────────────────────────

@router.get(
    "/progress/{session_id}",
    summary="Get processing progress",
    description="Get the current progress status of a processing session.",
)
async def get_progress(session_id: str):
    """Retrieve the progress of a processing session."""
    try:
        from app.clinical.progress_tracker import get_progress_tracker

        tracker = get_progress_tracker()
        progress = tracker.get_progress(session_id)

        if progress is None:
            raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": progress.model_dump(),
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Progress check failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Progress check failed: {str(e)}")


@router.websocket("/progress/ws/{session_id}")
async def progress_websocket(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for real-time progress updates."""
    await websocket.accept()

    try:
        from app.clinical.progress_tracker import get_progress_tracker

        tracker = get_progress_tracker()

        async for progress_update in tracker.subscribe_progress(session_id):
            await websocket.send_json(progress_update.model_dump())

            if progress_update.status == "completed" or progress_update.status == "failed":
                break

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for session %s", session_id)
    except Exception as e:
        logger.error("Progress WebSocket error: %s", str(e), exc_info=True)
        try:
            await websocket.send_json({"error": str(e)})
        except Exception:
            pass
        await websocket.close()
