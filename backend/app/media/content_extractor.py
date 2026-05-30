"""
Universal content extractor – facade over all media processors.

Provides a single entry-point for extracting text and structured data from
any file type (images, audio, video, PDFs, DICOM, web URLs) by auto-detecting
the content type and routing to the appropriate processor.

Typical usage::

    from app.media import ContentExtractor

    extractor = ContentExtractor()
    result = extractor.extract_content("/path/to/consultation.mp4")
    print(result.text)
"""

import logging
import mimetypes
import os
import time
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic data models
# ---------------------------------------------------------------------------


class FileType(str, Enum):
    """Supported file types for content extraction."""

    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    PDF = "pdf"
    DICOM = "dicom"
    TEXT = "text"
    WEB_URL = "web_url"
    UNKNOWN = "unknown"


class ContentBlock(BaseModel):
    """
    A single block of extracted content.

    Blocks preserve source provenance so the caller knows where each
    piece of text originated (e.g. page number, timestamp, speaker).
    """

    text: str = Field(..., description="Extracted text content")
    block_type: str = Field(
        default="text",
        description="Type of content: text, table, image_caption, metadata, speaker_turn",
    )
    source: str = Field(
        default="",
        description="Source identifier (page, timestamp, speaker label, URL, etc.)",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Extraction confidence",
    )
    language: str = Field(default="en", description="Detected language of this block")
    metadata: Dict = Field(
        default_factory=dict,
        description="Arbitrary key-value metadata (bbox, timestamps, etc.)",
    )


class ExtractedContent(BaseModel):
    """
    Complete extraction result for a single file.

    Contains all text blocks, metadata, and processing statistics.
    """

    file_path: str = Field(..., description="Original file path or URL")
    file_type: FileType = Field(..., description="Detected file type")
    title: str = Field(default="", description="Document / page title")
    text: str = Field(default="", description="Full concatenated text")
    blocks: List[ContentBlock] = Field(
        default_factory=list,
        description="Individual content blocks in reading order",
    )
    language: str = Field(default="en", description="Primary detected language")
    languages: List[str] = Field(
        default_factory=list,
        description="All detected languages in the document",
    )
    metadata: Dict = Field(
        default_factory=dict,
        description="File-level metadata (duration, pages, dimensions, etc.)",
    )
    medical_terms: List[Dict] = Field(
        default_factory=list,
        description="Extracted medical terminology",
    )
    errors: List[str] = Field(default_factory=list)
    processing_time: float = Field(default=0.0)


# ---------------------------------------------------------------------------
# MIME-type → FileType mapping
# ---------------------------------------------------------------------------

_MIME_TYPE_MAP: Dict[str, FileType] = {
    # Audio
    "audio/mpeg": FileType.AUDIO,
    "audio/wav": FileType.AUDIO,
    "audio/x-wav": FileType.AUDIO,
    "audio/wave": FileType.AUDIO,
    "audio/ogg": FileType.AUDIO,
    "audio/flac": FileType.AUDIO,
    "audio/mp4": FileType.AUDIO,
    "audio/x-m4a": FileType.AUDIO,
    "audio/aac": FileType.AUDIO,
    "audio/webm": FileType.AUDIO,
    # Video
    "video/mp4": FileType.VIDEO,
    "video/x-msvideo": FileType.VIDEO,
    "video/avi": FileType.VIDEO,
    "video/x-matroska": FileType.VIDEO,
    "video/quicktime": FileType.VIDEO,
    "video/webm": FileType.VIDEO,
    "video/x-flv": FileType.VIDEO,
    # Image
    "image/jpeg": FileType.IMAGE,
    "image/png": FileType.IMAGE,
    "image/tiff": FileType.IMAGE,
    "image/bmp": FileType.IMAGE,
    "image/gif": FileType.IMAGE,
    "image/webp": FileType.IMAGE,
    # PDF
    "application/pdf": FileType.PDF,
    # DICOM
    "application/dicom": FileType.DICOM,
    # Text
    "text/plain": FileType.TEXT,
    "text/html": FileType.TEXT,
    "text/csv": FileType.TEXT,
    "text/markdown": FileType.TEXT,
    "application/json": FileType.TEXT,
    "application/xml": FileType.TEXT,
}

# Extension → FileType fallback
_EXTENSION_MAP: Dict[str, FileType] = {
    # Audio
    ".mp3": FileType.AUDIO,
    ".wav": FileType.AUDIO,
    ".ogg": FileType.AUDIO,
    ".flac": FileType.AUDIO,
    ".m4a": FileType.AUDIO,
    ".aac": FileType.AUDIO,
    ".wma": FileType.AUDIO,
    ".opus": FileType.AUDIO,
    # Video
    ".mp4": FileType.VIDEO,
    ".avi": FileType.VIDEO,
    ".mkv": FileType.VIDEO,
    ".mov": FileType.VIDEO,
    ".webm": FileType.VIDEO,
    ".flv": FileType.VIDEO,
    ".wmv": FileType.VIDEO,
    # Image
    ".jpg": FileType.IMAGE,
    ".jpeg": FileType.IMAGE,
    ".png": FileType.IMAGE,
    ".tiff": FileType.IMAGE,
    ".tif": FileType.IMAGE,
    ".bmp": FileType.IMAGE,
    ".gif": FileType.IMAGE,
    ".webp": FileType.IMAGE,
    # PDF
    ".pdf": FileType.PDF,
    # DICOM
    ".dcm": FileType.DICOM,
    ".dicom": FileType.DICOM,
    # Text
    ".txt": FileType.TEXT,
    ".html": FileType.TEXT,
    ".htm": FileType.TEXT,
    ".csv": FileType.TEXT,
    ".md": FileType.TEXT,
    ".json": FileType.TEXT,
    ".xml": FileType.TEXT,
}


# ---------------------------------------------------------------------------
# ContentExtractor
# ---------------------------------------------------------------------------


class ContentExtractor:
    """
    Universal content extractor facade.

    Auto-detects file types and routes extraction to the appropriate
    specialised processor (OCR, Whisper, video, web crawler, etc.).

    All processors are lazily loaded so that only the required
    dependencies are imported at runtime.
    """

    def __init__(self) -> None:
        self._ocr_engine = None
        self._audio_processor = None
        self._video_processor = None
        self._web_crawler = None

        logger.info("ContentExtractor initialised")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_content(self, file_path: Union[str, Path]) -> ExtractedContent:
        """
        Extract all content from a file or URL.

        Automatically detects the file type, selects the appropriate
        processor, and returns structured extraction results.

        Args:
            file_path: Path to a local file or a web URL.

        Returns:
            An :class:`ExtractedContent` with text, blocks, and metadata.
        """
        file_path_str = str(file_path)

        t0 = time.time()

        # Detect if URL
        if file_path_str.startswith(("http://", "https://")):
            return self._extract_from_url(file_path_str)

        # Local file
        path = Path(file_path)
        if not path.exists():
            return ExtractedContent(
                file_path=file_path_str,
                file_type=FileType.UNKNOWN,
                errors=[f"File not found: {file_path_str}"],
                processing_time=round(time.time() - t0, 2),
            )

        file_type = self.detect_file_type(file_path_str)
        logger.info("Extracting content from %s  (type=%s)", path.name, file_type.value)

        try:
            extractor_fn = self.get_extractor(file_type)
            result = extractor_fn(str(path))
            result.processing_time = round(time.time() - t0, 2)
            return result
        except Exception as exc:
            logger.error("Extraction failed for %s: %s", path.name, exc)
            return ExtractedContent(
                file_path=file_path_str,
                file_type=file_type,
                errors=[str(exc)],
                processing_time=round(time.time() - t0, 2),
            )

    def extract_batch(self, file_paths: List[Union[str, Path]]) -> List[ExtractedContent]:
        """
        Extract content from multiple files.

        Args:
            file_paths: List of file paths or URLs.

        Returns:
            List of :class:`ExtractedContent` in the same order as input.
        """
        results: List[ExtractedContent] = []
        for fp in file_paths:
            results.append(self.extract_content(fp))
        return results

    def detect_file_type(self, file_path: str) -> FileType:
        """
        Detect the type of a file based on MIME type and extension.

        Args:
            file_path: Path to the file.

        Returns:
            A :class:`FileType` enum value.
        """
        # Try MIME type first
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type and mime_type in _MIME_TYPE_MAP:
            return _MIME_TYPE_MAP[mime_type]

        # Fall back to extension
        ext = Path(file_path).suffix.lower()
        if ext in _EXTENSION_MAP:
            return _EXTENSION_MAP[ext]

        logger.debug("Unknown file type for: %s (mime=%s, ext=%s)", file_path, mime_type, ext)
        return FileType.UNKNOWN

    def get_extractor(self, file_type: FileType):
        """
        Get the extraction function for a given file type.

        Args:
            file_type: The detected file type.

        Returns:
            A callable ``(file_path: str) -> ExtractedContent``.
        """
        extractors = {
            FileType.IMAGE: self._extract_image,
            FileType.AUDIO: self._extract_audio,
            FileType.VIDEO: self._extract_video,
            FileType.PDF: self._extract_pdf,
            FileType.DICOM: self._extract_dicom,
            FileType.TEXT: self._extract_text,
        }

        extractor = extractors.get(file_type)
        if extractor is None:
            raise ValueError(f"No extractor available for file type: {file_type}")
        return extractor

    # ------------------------------------------------------------------
    # Type-specific extraction methods
    # ------------------------------------------------------------------

    def _extract_image(self, file_path: str) -> ExtractedContent:
        """Extract text from an image using the OCR engine."""
        from app.ocr_engine import ocr_engine

        regions = ocr_engine.detect_regions(file_path)

        blocks: List[ContentBlock] = []
        texts: List[str] = []

        for region in regions:
            text = region.get("predicted_text", "")
            bbox = region.get("bbox", {})
            confidence = region.get("confidence", 0.0)

            blocks.append(
                ContentBlock(
                    text=text,
                    block_type="text",
                    source=f"region_{region.get('reading_order', 0)}",
                    confidence=confidence,
                    language=self._detect_script(text),
                    metadata={"bbox": bbox},
                )
            )
            if text:
                texts.append(text)

        return ExtractedContent(
            file_path=file_path,
            file_type=FileType.IMAGE,
            text="\n".join(texts),
            blocks=blocks,
            language=self._detect_primary_language(texts),
        )

    def _extract_audio(self, file_path: str) -> ExtractedContent:
        """Transcribe audio using Whisper."""
        from app.media.audio_processor import get_audio_processor

        processor = get_audio_processor()
        result = processor.transcribe_audio(file_path)

        blocks: List[ContentBlock] = []
        for seg in result.segments:
            blocks.append(
                ContentBlock(
                    text=seg.text,
                    block_type="text",
                    source=f"t={seg.start:.1f}-{seg.end:.1f}",
                    confidence=1.0,
                    language=result.language,
                    metadata={
                        "start": seg.start,
                        "end": seg.end,
                        "avg_logprob": seg.avg_logprob,
                    },
                )
            )

        return ExtractedContent(
            file_path=file_path,
            file_type=FileType.AUDIO,
            text=result.text,
            blocks=blocks,
            language=result.language,
            metadata={
                "duration": result.duration,
                "processing_time": result.processing_time,
            },
            medical_terms=[t.model_dump() for t in result.medical_terms],
        )

    def _extract_video(self, file_path: str) -> ExtractedContent:
        """Process video: extract keyframes, audio, and transcription."""
        from app.media.video_processor import get_video_processor

        processor = get_video_processor()
        result = processor.process_video(file_path)

        blocks: List[ContentBlock] = []

        # Transcription blocks
        if result.transcription_text:
            blocks.append(
                ContentBlock(
                    text=result.transcription_text,
                    block_type="text",
                    source="audio_transcription",
                    language=result.transcription_language or "en",
                    metadata={"duration": result.metadata.duration},
                )
            )

        # Keyframe metadata blocks
        for kf in result.keyframes:
            blocks.append(
                ContentBlock(
                    text=f"[Keyframe at {kf.timestamp:.1f}s]",
                    block_type="image_caption",
                    source=kf.file_path,
                    metadata={
                        "frame_number": kf.frame_number,
                        "timestamp": kf.timestamp,
                        "width": kf.width,
                        "height": kf.height,
                    },
                )
            )

        # Video metadata
        video_text = result.transcription_text or ""
        keyframe_text = " ".join(
            f"[Keyframe at {kf.timestamp:.1f}s]" for kf in result.keyframes
        )
        full_text = f"{video_text}\n\n{keyframe_text}".strip() if video_text else keyframe_text

        return ExtractedContent(
            file_path=file_path,
            file_type=FileType.VIDEO,
            title=Path(file_path).stem,
            text=full_text,
            blocks=blocks,
            language=result.transcription_language or "en",
            metadata={
                "duration": result.metadata.duration,
                "width": result.metadata.width,
                "height": result.metadata.height,
                "fps": result.metadata.fps,
                "codec": result.metadata.codec,
                "num_keyframes": len(result.keyframes),
                "has_audio": result.metadata.has_audio,
            },
        )

    def _extract_pdf(self, file_path: str) -> ExtractedContent:
        """Extract text from a PDF file."""
        blocks: List[ContentBlock] = []
        texts: List[str] = []

        try:
            import fitz  # PyMuPDF

            doc = fitz.open(file_path)
            for page_num in range(len(doc)):
                page = doc[page_num]
                page_text = page.get_text("text")

                if page_text.strip():
                    blocks.append(
                        ContentBlock(
                            text=page_text.strip(),
                            block_type="text",
                            source=f"page_{page_num + 1}",
                        )
                    )
                    texts.append(page_text.strip())

                # Also run OCR on page images for scanned PDFs
                try:
                    pix = page.get_pixmap(dpi=200)
                    img_bytes = pix.tobytes("png")

                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
                        tmp.write(img_bytes)
                        tmp.flush()
                        from app.ocr_engine import ocr_engine
                        ocr_regions = ocr_engine.detect_regions(tmp.name)

                        ocr_text = " ".join(
                            r.get("predicted_text", "") for r in ocr_regions
                        ).strip()

                        if ocr_text and ocr_text != page_text.strip():
                            blocks.append(
                                ContentBlock(
                                    text=ocr_text,
                                    block_type="text",
                                    source=f"page_{page_num + 1}_ocr",
                                    metadata={"method": "ocr"},
                                )
                            )
                            if not page_text.strip():
                                texts.append(ocr_text)
                except Exception as exc:
                    logger.debug("OCR fallback failed for PDF page %d: %s", page_num + 1, exc)

            doc.close()
        except ImportError:
            # Fallback: try pdfplumber
            try:
                import pdfplumber

                with pdfplumber.open(file_path) as pdf:
                    for page_num, page in enumerate(pdf.pages):
                        page_text = page.extract_text() or ""
                        if page_text.strip():
                            blocks.append(
                                ContentBlock(
                                    text=page_text.strip(),
                                    block_type="text",
                                    source=f"page_{page_num + 1}",
                                )
                            )
                            texts.append(page_text.strip())
            except ImportError:
                logger.error("Neither PyMuPDF nor pdfplumber installed for PDF extraction")
                return ExtractedContent(
                    file_path=file_path,
                    file_type=FileType.PDF,
                    errors=["No PDF library available. Install PyMuPDF or pdfplumber."],
                )

        return ExtractedContent(
            file_path=file_path,
            file_type=FileType.PDF,
            text="\n\n".join(texts),
            blocks=blocks,
            language=self._detect_primary_language(texts),
        )

    def _extract_dicom(self, file_path: str) -> ExtractedContent:
        """Extract text and metadata from a DICOM file."""
        from app.dicom.reader import DICOMReader

        reader = DICOMReader()
        all_texts = reader.extract_all_text(file_path)
        meta_summary = reader.get_metadata_summary(file_path)

        blocks: List[ContentBlock] = []
        texts: List[str] = []

        for entry in all_texts:
            if entry.source != "pixel_data" and entry.text.strip():
                blocks.append(
                    ContentBlock(
                        text=entry.text,
                        block_type="metadata" if entry.source == "metadata" else "text",
                        source=entry.source,
                        confidence=entry.confidence,
                        metadata=entry.metadata or {},
                    )
                )
                texts.append(entry.text)

        return ExtractedContent(
            file_path=file_path,
            file_type=FileType.DICOM,
            text="\n".join(texts),
            blocks=blocks,
            metadata=meta_summary,
        )

    def _extract_text(self, file_path: str) -> ExtractedContent:
        """Read plain text files."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="latin-1") as f:
                content = f.read()

        return ExtractedContent(
            file_path=file_path,
            file_type=FileType.TEXT,
            text=content,
            blocks=[
                ContentBlock(
                    text=content,
                    block_type="text",
                    source="file",
                )
            ],
            language=self._detect_primary_language([content]),
        )

    def _extract_from_url(self, url: str) -> ExtractedContent:
        """Extract content from a web URL."""
        from app.media.web_crawler import get_web_crawler

        crawler = get_web_crawler()
        crawled = crawler.crawl_url(url, max_depth=0)

        blocks: List[ContentBlock] = []
        texts: List[str] = []
        languages: List[str] = []

        for page in crawled.pages:
            blocks.append(
                ContentBlock(
                    text=page.content,
                    block_type="text",
                    source=page.url,
                    language=page.language,
                    metadata={
                        "title": page.title,
                        "status_code": page.status_code,
                    },
                )
            )
            texts.append(page.content)
            if page.language and page.language not in languages:
                languages.append(page.language)

        # Add extracted references
        for ref in crawled.references:
            blocks.append(
                ContentBlock(
                    text=ref.text,
                    block_type="metadata",
                    source=f"reference_{ref.index}",
                    metadata={
                        "year": ref.year,
                        "doi": ref.doi,
                    },
                )
            )

        # Add extracted articles
        for article in crawled.articles:
            blocks.append(
                ContentBlock(
                    text=article.abstract,
                    block_type="text",
                    source=f"article_{article.pmid or article.doi or ''}",
                    language="en",
                    metadata={
                        "title": article.title,
                        "journal": article.journal,
                        "pmid": article.pmid,
                        "doi": article.doi,
                    },
                )
            )
            if article.abstract:
                texts.append(article.abstract)

        errors = list(crawled.errors) if crawled.errors else []

        return ExtractedContent(
            file_path=url,
            file_type=FileType.WEB_URL,
            title=crawled.pages[0].title if crawled.pages else "",
            text="\n\n".join(texts),
            blocks=blocks,
            language=languages[0] if languages else "en",
            languages=languages,
            errors=errors,
            metadata={
                "total_pages": crawled.total_urls_crawled,
                "total_articles": len(crawled.articles),
                "total_references": len(crawled.references),
                "processing_time": crawled.processing_time,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_script(text: str) -> str:
        """Detect the script of a text snippet (arabic, latin, mixed)."""
        if not text:
            return "en"
        has_arabic = any("\u0600" <= c <= "\u06FF" for c in text)
        has_latin = any(c.isascii() and c.isalpha() for c in text)
        if has_arabic and has_latin:
            return "mixed"
        elif has_arabic:
            return "ar"
        else:
            return "en"

    @staticmethod
    def _detect_primary_language(texts: List[str]) -> str:
        """Detect the primary language across multiple text blocks."""
        arabic_count = 0
        latin_count = 0
        for text in texts:
            if not text:
                continue
            if any("\u0600" <= c <= "\u06FF" for c in text):
                arabic_count += 1
            if any(c.isascii() and c.isalpha() for c in text):
                latin_count += 1
        return "ar" if arabic_count > latin_count else "en"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_content_extractor: Optional[ContentExtractor] = None


def get_content_extractor() -> ContentExtractor:
    """Get or create the shared :class:`ContentExtractor` singleton."""
    global _content_extractor
    if _content_extractor is None:
        _content_extractor = ContentExtractor()
    return _content_extractor
