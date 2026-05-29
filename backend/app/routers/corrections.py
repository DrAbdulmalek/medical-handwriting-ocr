from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
from app.models import RegionCorrection, RegionResponse
from uuid import UUID

router = APIRouter(prefix="/api", tags=["corrections"])


@router.post("/correct")
async def submit_correction(
    correction: RegionCorrection,
    db: Session = Depends(get_db)
):
    """
    Save user correction and update status
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
            status = 'pending',
            corrected_at = NOW(),
            user_id = :user,
            correction_count = correction_count + 1
        WHERE id = :id
    """), {
        "corrected": correction.corrected_text,
        "user": correction.user_id,
        "id": str(correction.region_id)
    })

    db.commit()

    return {
        "success": True,
        "message": "Correction saved successfully",
        "region_id": correction.region_id,
        "previous_text": region.predicted_text,
        "corrected_text": correction.corrected_text
    }


@router.get("/pending", response_model=list[RegionResponse])
async def get_pending_corrections(
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """
    Get regions that need review (low confidence or corrected)
    """
    result = db.execute(text("""
        SELECT id, bbox, predicted_text, confidence, corrected_text, status
        FROM text_regions
        WHERE status = 'pending'
        ORDER BY created_at DESC
        LIMIT :limit
    """), {"limit": limit})

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
    Promote correction to gold standard (for medical review)
    """
    db.execute(text("""
        UPDATE text_regions
        SET status = 'gold_standard',
            reviewed_at = NOW(),
            reviewer_id = :reviewer
        WHERE id = :id
    """), {
        "reviewer": reviewer_id,
        "id": str(region_id)
    })

    db.commit()

    return {"success": True, "message": "Promoted to gold standard"}
