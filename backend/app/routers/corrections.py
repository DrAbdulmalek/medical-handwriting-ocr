from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
from app.models import RegionCorrection, RegionResponse
from uuid import UUID
from typing import Optional

router = APIRouter(prefix="/api", tags=["corrections"])

@router.post("/correct")
async def submit_correction(
    correction: RegionCorrection,
    db: Session = Depends(get_db),
    device_id: Optional[str] = Header(None, alias="X-Device-ID")
):
    """
    Save user correction and update status.
    Supports mobile devices via X-Device-ID header.
    """
    # Verify region exists
    result = db.execute(text("""
        SELECT id, predicted_text, confidence, status
        FROM text_regions
        WHERE id = :id
    """), {"id": str(correction.region_id)})

    region = result.fetchone()
    if not region:
        raise HTTPException(404, "Region not found")

    # Update with correction
    db.execute(text("""
        UPDATE text_regions
        SET corrected_text = :corrected,
            status = 'corrected',
            corrected_at = NOW(),
            user_id = :user,
            device_id = :device,
            correction_count = correction_count + 1,
            updated_at = NOW()
        WHERE id = :id
    """), {
        "corrected": correction.corrected_text,
        "user": correction.user_id,
        "device": device_id,
        "id": str(correction.region_id)
    })

    db.commit()

    return {
        "success": True,
        "message": "Correction saved successfully",
        "region_id": correction.region_id,
        "previous_text": region.predicted_text,
        "corrected_text": correction.corrected_text,
        "device_id": device_id
    }

@router.get("/pending", response_model=list[RegionResponse])
async def get_pending_corrections(
    limit: int = 50,
    since: Optional[str] = None,  # ISO timestamp for mobile sync
    user_id: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Get regions that need review (low confidence or corrected).
    Supports incremental sync with 'since' parameter for mobile.
    """
    params = {"limit": limit}

    since_filter = ""
    if since:
        since_filter = "AND tr.updated_at > :since"
        params["since"] = since

    user_filter = ""
    if user_id:
        user_filter = "AND (tr.user_id = :user OR tr.user_id IS NULL)"
        params["user"] = user_id

    result = db.execute(text(f"""
        SELECT tr.id, tr.bbox, tr.predicted_text, tr.confidence, 
               tr.corrected_text, tr.status, tr.updated_at, tr.device_id
        FROM text_regions tr
        WHERE tr.status IN ('pending', 'corrected')
        {since_filter}
        {user_filter}
        ORDER BY tr.updated_at DESC
        LIMIT :limit
    """), params)

    regions = []
    for row in result:
        regions.append(RegionResponse(
            id=row.id,
            bbox=row.bbox,
            predicted_text=row.predicted_text,
            confidence=row.confidence,
            corrected_text=row.corrected_text,
            status=row.status
        ))

    return regions

@router.post("/approve/{region_id}")
async def approve_correction(
    region_id: UUID,
    reviewer_id: str = "system",
    db: Session = Depends(get_db)
):
    """
    Promote correction to gold standard (for medical review).
    """
    db.execute(text("""
        UPDATE text_regions
        SET status = 'gold_standard',
            reviewed_at = NOW(),
            reviewer_id = :reviewer,
            updated_at = NOW()
        WHERE id = :id
    """), {
        "reviewer": reviewer_id,
        "id": str(region_id)
    })

    db.commit()

    return {"success": True, "message": "Promoted to gold standard"}
