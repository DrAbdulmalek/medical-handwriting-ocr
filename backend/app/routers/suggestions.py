from fastapi import APIRouter, Depends, Query
from typing import Optional

from backend.app.suggestion_engine import SuggestionEngine, get_suggestion_engine

router = APIRouter(prefix="/suggestions", tags=["suggestions"])

@router.get("/")
async def get_suggestions(
    text: str = Query(..., min_length=1),
    context_before: Optional[str] = Query(None),
    context_after: Optional[str] = Query(None),
    script_class: Optional[str] = Query(None),
    is_medical: bool = Query(False),
    engine: SuggestionEngine = Depends(get_suggestion_engine)
):
    suggestions = engine.get_suggestions(
        text=text, context_before=context_before,
        context_after=context_after, script_class=script_class,
        is_medical=is_medical
    )

    return {
        "original": text,
        "suggestions_count": len(suggestions),
        "suggestions": [s.to_dict() for s in suggestions]
    }

@router.post("/feedback")
async def record_correction(
    original: str, corrected: str,
    accepted_suggestion: Optional[str] = None,
    engine: SuggestionEngine = Depends(get_suggestion_engine)
):
    engine.add_historical_correction(original, corrected)
    return {"success": True, "message": "Correction recorded"}
