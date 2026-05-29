from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import datetime


class RegionCorrection(BaseModel):
    region_id: UUID
    corrected_text: str
    user_id: Optional[str] = "anonymous"


class RegionResponse(BaseModel):
    id: UUID
    bbox: dict
    predicted_text: str
    confidence: float
    corrected_text: Optional[str] = None
    status: str
    crop_url: Optional[str] = None

    class Config:
        from_attributes = True


class DocumentUpload(BaseModel):
    file_name: str
    page_count: int = 1


class OCRResult(BaseModel):
    document_id: UUID
    page_id: UUID
    regions: List[RegionResponse]
    total_regions: int
    needs_review: int  # Count of low-confidence regions


class StatsResponse(BaseModel):
    total_documents: int
    total_regions: int
    corrected_regions: int
    pending_corrections: int
    avg_confidence: float
    model_version: str
