"""
Media processing module for Medical Handwriting OCR.

Provides audio transcription, video processing, speaker diarization,
web crawling, and universal content extraction capabilities for
medical multimedia content.

Usage:
    from app.media import AudioProcessor, VideoProcessor
    from app.media import SpeakerDiarization, MedicalWebCrawler
    from app.media import ContentExtractor
"""

from app.media.audio_processor import AudioProcessor, TranscriptionResult, TranscriptionSegment, MedicalTerm
from app.media.video_processor import VideoProcessor, VideoResult, KeyFrame, VideoMetadata
from app.media.speaker_diarization import (
    SpeakerDiarization,
    SpeakerSegment,
    DiarizationResult,
    CombinedSegment,
    SpeakerRoles,
)
from app.media.web_crawler import (
    MedicalWebCrawler,
    CrawledContent,
    Article,
    GuidelineContent,
    Reference,
    CrawledPage,
)
from app.media.content_extractor import ContentExtractor, ExtractedContent, ContentBlock, FileType

__all__ = [
    # Audio processing
    "AudioProcessor",
    "TranscriptionResult",
    "TranscriptionSegment",
    "MedicalTerm",
    # Video processing
    "VideoProcessor",
    "VideoResult",
    "KeyFrame",
    "VideoMetadata",
    # Speaker diarization
    "SpeakerDiarization",
    "SpeakerSegment",
    "DiarizationResult",
    "CombinedSegment",
    "SpeakerRoles",
    # Web crawling
    "MedicalWebCrawler",
    "CrawledContent",
    "Article",
    "GuidelineContent",
    "Reference",
    "CrawledPage",
    # Content extraction
    "ContentExtractor",
    "ExtractedContent",
    "ContentBlock",
    "FileType",
]
