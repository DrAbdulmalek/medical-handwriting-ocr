"""
Media Processing Router
=======================
Endpoints for audio transcription (Whisper), video processing,
speaker diarization, web crawling, and universal content extraction.
"""

import os
import uuid
import logging
import tempfile
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Query, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/media", tags=["media"])


# ─────────────────────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────────────────────

class CrawlRequest(BaseModel):
    """Request body for web crawling."""
    url: str = Field(..., description="URL to crawl")
    max_depth: int = Field(default=1, ge=1, le=5, description="Maximum crawl depth")
    extract_references: bool = Field(default=True, description="Extract references from the page")

    class Config:
        json_schema_extra = {
            "example": {
                "url": "https://pubmed.ncbi.nlm.nih.gov/",
                "max_depth": 1,
                "extract_references": True
            }
        }


class TranscribeOptions(BaseModel):
    """Options for audio/video transcription."""
    language: Optional[str] = Field(default=None, description="Language code (ar, en, or None for auto)")
    model_size: str = Field(default="base", description="Whisper model size: tiny, base, small, medium, large")
    word_timestamps: bool = Field(default=True, description="Include word-level timestamps")
    medical_terms: bool = Field(default=True, description="Extract medical terminology")


# ─────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────

@router.post(
    "/audio/transcribe",
    summary="Transcribe audio file",
    description=(
        "Upload an audio file (MP3, WAV, M4A, OGG) for transcription using Whisper. "
        "Supports Arabic and English with automatic language detection."
    ),
)
async def transcribe_audio(
    file: UploadFile = File(...),
    language: Optional[str] = Form(default=None),
    model_size: str = Form(default="base"),
    word_timestamps: bool = Form(default=True),
):
    """Transcribe an uploaded audio file."""
    allowed_extensions = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".wma", ".aac"}
    file_ext = os.path.splitext(file.filename or "")[1].lower()

    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format: {file_ext}. Supported: {', '.join(sorted(allowed_extensions))}"
        )

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    with tempfile.NamedTemporaryFile(suffix=file_ext, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        from app.media.audio_processor import get_audio_processor
        processor = get_audio_processor()

        result = processor.transcribe_audio(
            file_path=tmp_path,
            language=language,
            model_size=model_size,
        )

        logger.info(
            "Audio transcribed: %s — %.2fs, %d segments",
            file.filename, result.duration, len(result.segments)
        )

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": "Audio transcribed successfully",
                "data": {
                    "file_name": file.filename,
                    "language": result.language,
                    "language_confidence": result.language_confidence,
                    "duration": result.duration,
                    "full_text": result.full_text,
                    "segments": [
                        {
                            "start": seg.start,
                            "end": seg.end,
                            "text": seg.text,
                            "confidence": seg.confidence,
                            "words": seg.words if word_timestamps else None,
                        }
                        for seg in result.segments
                    ],
                },
            },
        )

    except Exception as e:
        logger.error("Audio transcription failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.post(
    "/video/transcribe",
    summary="Transcribe video file",
    description=(
        "Upload a video file (MP4, AVI, MOV, MKV) for transcription. "
        "Extracts audio track and transcribes using Whisper."
    ),
)
async def transcribe_video(
    file: UploadFile = File(...),
    language: Optional[str] = Form(default=None),
    model_size: str = Form(default="base"),
):
    """Transcribe audio from an uploaded video file."""
    allowed_extensions = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
    file_ext = os.path.splitext(file.filename or "")[1].lower()

    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported video format: {file_ext}"
        )

    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=file_ext, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        from app.media.video_processor import get_video_processor
        processor = get_video_processor()

        result = processor.transcribe_video(
            file_path=tmp_path,
            language=language,
            model_size=model_size,
        )

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": "Video transcribed successfully",
                "data": {
                    "file_name": file.filename,
                    "duration": result.duration,
                    "audio_path": result.audio_path,
                    "transcription": {
                        "full_text": result.transcription.full_text,
                        "language": result.transcription.language,
                        "segments": [
                            {"start": s.start, "end": s.end, "text": s.text}
                            for s in result.transcription.segments
                        ],
                    },
                    "metadata": result.metadata,
                },
            },
        )

    except Exception as e:
        logger.error("Video transcription failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Video transcription failed: {str(e)}")

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.post(
    "/video/keyframes",
    summary="Extract key frames from video",
    description="Extract representative key frames from a video file at specified intervals.",
)
async def extract_keyframes(
    file: UploadFile = File(...),
    interval: float = Form(default=5.0, description="Interval in seconds between key frames"),
    max_frames: int = Form(default=20, description="Maximum number of frames to extract"),
):
    """Extract key frames from an uploaded video."""
    contents = await file.read()
    file_ext = os.path.splitext(file.filename or "")[1].lower()

    with tempfile.NamedTemporaryFile(suffix=file_ext or ".mp4", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        from app.media.video_processor import get_video_processor
        processor = get_video_processor()

        frames = processor.extract_keyframes(tmp_path, interval=interval, max_frames=max_frames)

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": f"Extracted {len(frames)} key frame(s)",
                "data": {
                    "total_frames": len(frames),
                    "interval": interval,
                    "frames": [
                        {"path": f.path, "timestamp": f.timestamp, "scene_score": f.scene_score}
                        for f in frames
                    ],
                },
            },
        )

    except Exception as e:
        logger.error("Keyframe extraction failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Keyframe extraction failed: {str(e)}")

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.post(
    "/diarize",
    summary="Speaker diarization",
    description=(
        "Identify and separate different speakers in an audio recording. "
        "Supports doctor/patient/nurse role identification."
    ),
)
async def diarize_speakers(
    file: UploadFile = File(...),
    num_speakers: Optional[int] = Form(default=None, description="Expected number of speakers (auto-detect if None)"),
):
    """Perform speaker diarization on an uploaded audio file."""
    contents = await file.read()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        from app.media.speaker_diarization import get_diarization_processor
        processor = get_diarization_processor()

        result = processor.diarize(file_path=tmp_path, num_speakers=num_speakers)

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": f"Diarization complete: {len(result.segments)} segments, {result.num_speakers} speakers",
                "data": {
                    "num_speakers": result.num_speakers,
                    "duration": result.duration,
                    "segments": [
                        {
                            "speaker": seg.speaker_label,
                            "start": seg.start,
                            "end": seg.end,
                            "role": seg.role,
                            "confidence": seg.confidence,
                        }
                        for seg in result.segments
                    ],
                },
            },
        )

    except Exception as e:
        logger.error("Diarization failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Diarization failed: {str(e)}")

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.post(
    "/web/crawl",
    summary="Crawl a web page for medical content",
    description=(
        "Crawl a URL and extract structured medical content. "
        "Supports PubMed, NEJM, WHO, and other medical literature sites."
    ),
)
async def crawl_web(request: CrawlRequest):
    """Crawl a URL for medical content extraction."""
    try:
        from app.media.web_crawler import get_web_crawler
        crawler = get_web_crawler()

        result = crawler.crawl_url(
            url=request.url,
            max_depth=request.max_depth,
            extract_references=request.extract_references,
        )

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": "Crawl completed successfully",
                "data": {
                    "url": request.url,
                    "title": result.title,
                    "content": result.content[:500] + "..." if len(result.content) > 500 else result.content,
                    "content_length": len(result.content),
                    "content_type": result.content_type,
                    "references": result.references,
                    "metadata": result.metadata,
                },
            },
        )

    except Exception as e:
        logger.error("Web crawl failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Crawl failed: {str(e)}")


@router.get(
    "/web/pubmed",
    summary="Search PubMed",
    description="Search PubMed for medical articles and literature.",
)
async def search_pubmed(
    q: str = Query(..., description="Search query"),
    max_results: int = Query(default=10, ge=1, le=50),
):
    """Search PubMed for medical articles matching the query."""
    try:
        from app.media.web_crawler import get_web_crawler
        crawler = get_web_crawler()

        articles = crawler.search_pubmed(query=q, max_results=max_results)

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": f"Found {len(articles)} article(s)",
                "data": {
                    "query": q,
                    "total_results": len(articles),
                    "articles": [
                        {
                            "pmid": art.pmid,
                            "title": art.title,
                            "authors": art.authors,
                            "journal": art.journal,
                            "publication_date": str(art.publication_date) if art.publication_date else None,
                            "abstract": art.abstract[:300] + "..." if art.abstract and len(art.abstract) > 300 else art.abstract,
                            "doi": art.doi,
                            "url": art.url,
                        }
                        for art in articles
                    ],
                },
            },
        )

    except Exception as e:
        logger.error("PubMed search failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"PubMed search failed: {str(e)}")


@router.post(
    "/extract",
    summary="Universal content extraction",
    description=(
        "Upload any file and automatically detect its type for optimal extraction. "
        "Supports 20+ file types: images, PDFs, audio, video, documents, and more."
    ),
)
async def extract_content_universal(
    file: UploadFile = File(...),
    extract_medical: bool = Form(default=True, description="Extract medical entities from text"),
):
    """Auto-detect file type and extract content optimally."""
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    file_ext = os.path.splitext(file.filename or "")[1].lower()

    with tempfile.NamedTemporaryFile(suffix=file_ext or ".bin", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        from app.media.content_extractor import get_content_extractor
        extractor = get_content_extractor()

        result = extractor.extract_content(
            file_path=tmp_path,
            extract_medical=extract_medical,
        )

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": f"Extracted content from {result.file_type.value}",
                "data": {
                    "file_name": file.filename,
                    "file_type": result.file_type.value,
                    "content_blocks": [
                        {
                            "type": block.block_type,
                            "content": block.content[:200] + "..." if len(block.content) > 200 else block.content,
                            "confidence": block.confidence,
                            "metadata": block.metadata,
                        }
                        for block in result.content_blocks
                    ],
                    "total_blocks": len(result.content_blocks),
                    "processing_time": result.processing_time,
                },
            },
        )

    except Exception as e:
        logger.error("Content extraction failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
