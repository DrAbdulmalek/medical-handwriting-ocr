"""
Reports API router.
Provides endpoints for generating performance reports in PDF/Excel format.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional

from app.database import get_db
from app.reporting.generator import ReportGenerator, ReportConfig

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/generate")
async def generate_report(
    format: str = Query("pdf", regex="^(pdf|excel)$"),
    days: int = Query(30, ge=1, le=365),
    title: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Generate a performance report for the specified period.
    
    Query Parameters:
        - format: 'pdf' or 'excel'
        - days: Number of days to include in the report
        - title: Custom report title (optional)
    """
    period_end = datetime.now()
    period_start = period_end - timedelta(days=days)
    
    config = ReportConfig(
        title=title or f"Medical OCR Report - Last {days} Days",
        period_start=period_start,
        period_end=period_end,
        format=format,
    )

    generator = ReportGenerator(db)
    report_bytes = generator.generate_correction_report(config)

    if not report_bytes:
        raise HTTPException(500, "Failed to generate report")

    media_type = "application/pdf" if format == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    filename = f"ocr_report_{period_start.strftime('%Y%m%d')}_{period_end.strftime('%Y%m%d')}.{format}"

    return Response(
        content=report_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/summary")
async def get_summary(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db)
):
    """Get a quick statistics summary (JSON) without generating a file."""
    from sqlalchemy import text

    period_start = datetime.now() - timedelta(days=days)

    result = db.execute(text("""
        SELECT
            COUNT(*) as total,
            COUNT(CASE WHEN corrected_text IS NOT NULL THEN 1 END) as corrected,
            COUNT(CASE WHEN status = 'gold_standard' THEN 1 END) as gold,
            AVG(confidence) as avg_conf
        FROM text_regions
        WHERE created_at >= :start
    """), {"start": period_start}).fetchone()

    return {
        "period_days": days,
        "total_regions": result.total,
        "corrected": result.corrected,
        "gold_standard": result.gold,
        "avg_confidence": float(result.avg_conf) if result.avg_conf else 0,
        "correction_rate": round(
            (result.corrected / result.total * 100) if result.total > 0 else 0, 1
        ),
    }
