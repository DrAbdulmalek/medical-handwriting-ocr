"""
Medical Text Chunker for RAG (Retrieval-Augmented Generation).

Intelligently splits medical documents into semantically meaningful segments
while respecting medical document structure, handling Arabic RTL text, and
preserving context across chunk boundaries.
"""

import re
import math
import logging
from typing import Optional, List, Dict, Any
from uuid import uuid4
from datetime import datetime

from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Data Models
# =============================================================================


class ChunkingConfig(BaseModel):
    """Configuration for the text chunking process."""

    chunk_size: int = Field(default=512, ge=64, le=4096, description="Target character count per chunk")
    overlap: int = Field(default=50, ge=0, le=512, description="Character overlap between consecutive chunks")
    min_chunk_size: int = Field(default=64, ge=16, description="Minimum characters for a valid chunk")
    respect_sections: bool = Field(default=True, description="Split at section headers when detected")
    respect_paragraphs: bool = Field(default=True, description="Prefer splitting at paragraph boundaries")
    preserve_headers: bool = Field(default=True, description="Repeat section header in subsequent chunks")
    include_metadata: bool = Field(default=True, description="Attach metadata to each chunk")


class ChunkMetadata(BaseModel):
    """Metadata associated with a text chunk."""

    chunk_index: int = Field(description="Zero-based index of this chunk within the source")
    page_number: Optional[int] = Field(default=None, description="Source page number (1-based)")
    section: Optional[str] = Field(default=None, description="Detected section name (e.g. Diagnosis)")
    language: Optional[str] = Field(default=None, description="Primary language: 'ar', 'en', or 'mixed'")
    char_start: int = Field(description="Start character offset in the original text")
    char_end: int = Field(description="End character offset (exclusive) in the original text")
    token_estimate: int = Field(default=0, description="Estimated token count (~4 chars per token)")
    has_medical_terms: bool = Field(default=False, description="Whether the chunk contains known medical terms")
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class Chunk(BaseModel):
    """A single text chunk produced by the MedicalTextChunker."""

    id: str = Field(default_factory=lambda: str(uuid4()), description="Unique chunk identifier")
    text: str = Field(description="Chunk text content")
    metadata: ChunkMetadata = Field(description="Chunk metadata")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="Relevance score (populated by downstream)")


class DocumentChunk(Chunk):
    """Extended chunk with document-level provenance."""

    document_id: Optional[str] = Field(default=None, description="Source document UUID")
    page_id: Optional[str] = Field(default=None, description="Source page UUID")
    region_ids: List[str] = Field(default_factory=list, description="TextRegion UUIDs contributing text")


# =============================================================================
# Section / Structure Detection Patterns
# =============================================================================

# Arabic medical document section headers (common patterns)
_ARABIC_SECTION_PATTERNS: List[re.Pattern] = [
    re.compile(r"الشكوى[^\n]*", re.UNICODE),          # Chief complaint
    re.compile(r"التشخيص[^\n]*", re.UNICODE),          # Diagnosis
    re.compile(r"الأدوية[^\n]*", re.UNICODE),          # Medications
    re.compile(r"الفحص السريري[^\n]*", re.UNICODE),     # Clinical examination
    re.compile(r"التاريخ المرضي[^\n]*", re.UNICODE),   # Medical history
    re.compile(r"النتائج[^\n]*", re.UNICODE),           # Results
    re.compile(r"الوصف[^\n]*", re.UNICODE),             # Description
    re.compile(r"الخطة العلاجية[^\n]*", re.UNICODE),    # Treatment plan
    re.compile(r"الملاحظات[^\n]*", re.UNICODE),         # Notes
]

# English medical section headers
_ENGLISH_SECTION_PATTERNS: List[re.Pattern] = [
    re.compile(r"(?i)\b(?:chief complaint|cc)[.:]\s*[^\n]*"),
    re.compile(r"(?i)\bdiagnosis(?:es)?[.:]\s*[^\n]*"),
    re.compile(r"(?i)\bmedications?[.:]\s*[^\n]*"),
    re.compile(r"(?i)\bclinical exam(?:ination)?[.:]\s*[^\n]*"),
    re.compile(r"(?i)\bmedical history(?: of present illness)?[.:]\s*[^\n]*"),
    re.compile(r"(?i)\bresults?|findings?[.:]\s*[^\n]*"),
    re.compile(r"(?i)\btreatment plan[.:]\s*[^\n]*"),
    re.compile(r"(?i)\bnotes?|impression[.:]\s*[^\n]*"),
    re.compile(r"(?i)\bassessment[.:]\s*[^\n]*"),
    re.compile(r"(?i)\bplan[.:]\s*[^\n]*"),
    re.compile(r"(?i)\bvital signs?[.:]\s*[^\n]*"),
    re.compile(r"(?i)\blab results?[.:]\s*[^\n]*"),
]


# =============================================================================
# MedicalTextChunker
# =============================================================================


class MedicalTextChunker:
    """
    Intelligent text chunker designed for medical documents.

    * Respects medical document structure (sections, paragraphs).
    * Supports both Arabic (RTL) and English (LTR) text, including mixed scripts.
    * Configurable chunk size and overlap to balance granularity and context.
    * Preserves section headers across chunk boundaries.
    * Produces :class:`Chunk` objects with rich metadata.
    """

    def __init__(self, config: Optional[ChunkingConfig] = None):
        """
        Args:
            config: Chunking configuration.  Uses sensible defaults when *None*.
        """
        self.config = config or ChunkingConfig()
        logger.info(
            "MedicalTextChunker initialised (chunk_size=%d, overlap=%d)",
            self.config.chunk_size,
            self.config.overlap,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chunk_text(self, text: str, chunk_size: Optional[int] = None, overlap: Optional[int] = None) -> List[Chunk]:
        """
        Split a single block of text into chunks.

        Args:
            text: The raw text to chunk.  May contain Arabic, English, or mixed content.
            chunk_size: Override the configured chunk size.
            overlap: Override the configured overlap.

        Returns:
            A list of :class:`Chunk` objects sorted by ``metadata.chunk_index``.
        """
        if not text or not text.strip():
            logger.warning("chunk_text called with empty text")
            return []

        size = chunk_size or self.config.chunk_size
        olap = overlap if overlap is not None else self.config.overlap

        # Detect structure first
        sections = self._detect_sections(text)

        if sections and self.config.respect_sections:
            raw_chunks = self._chunk_by_sections(text, sections, size, olap)
        else:
            raw_chunks = self._chunk_by_paragraphs(text, size, olap)

        # Post-process: enforce min size, attach metadata, add headers
        chunks = self._finalise_chunks(raw_chunks, text)

        logger.info("Chunked text into %d chunks (source length=%d chars)", len(chunks), len(text))
        return chunks

    def chunk_document(self, pages: List[Dict[str, Any]]) -> List[DocumentChunk]:
        """
        Chunk a full document represented as a list of page dicts.

        Expected keys per page dict:
            * ``page_number`` (int)
            * ``text`` (str) — the full OCR text for the page
            * ``page_id`` (str, optional)
            * ``document_id`` (str, optional)
            * ``region_ids`` (list[str], optional)

        Args:
            pages: List of page dictionaries with OCR text.

        Returns:
            A list of :class:`DocumentChunk` objects across all pages.
        """
        all_chunks: List[DocumentChunk] = []
        global_char_offset = 0

        for page in pages:
            page_text = page.get("text", "")
            if not page_text.strip():
                global_char_offset += len(page_text)
                continue

            page_number = page.get("page_number")
            page_chunks = self.chunk_text(page_text)

            for idx, chunk in enumerate(page_chunks):
                chunk.metadata.page_number = page_number

                doc_chunk = DocumentChunk(
                    id=chunk.id,
                    text=chunk.text,
                    metadata=chunk.metadata,
                    document_id=page.get("document_id"),
                    page_id=page.get("page_id"),
                    region_ids=page.get("region_ids", []),
                )
                # Adjust char offsets to document-wide positions
                doc_chunk.metadata.char_start += global_char_offset
                doc_chunk.metadata.char_end += global_char_offset
                doc_chunk.metadata.chunk_index = len(all_chunks)
                all_chunks.append(doc_chunk)

            global_char_offset += len(page_text)

        logger.info("Chunked %d pages into %d document chunks", len(pages), len(all_chunks))
        return all_chunks

    def merge_chunks(self, chunks: List[Chunk]) -> str:
        """
        Merge a list of chunks back into a single text body.

        Duplicate section headers that were repeated for context are
        collapsed to a single occurrence.

        Args:
            chunks: Chunks to merge (must be in order).

        Returns:
            A single string of the concatenated text.
        """
        if not chunks:
            return ""

        parts: List[str] = []
        seen_headers: set = set()

        for chunk in chunks:
            section = chunk.metadata.section
            if section and section in seen_headers and self.config.preserve_headers:
                # Strip the duplicated header from this chunk's text
                stripped = self._strip_header(chunk.text, section)
                parts.append(stripped)
            else:
                parts.append(chunk.text)
                if section:
                    seen_headers.add(section)

        return "\n\n".join(parts)

    def get_chunk_metadata(self, chunk: Chunk) -> ChunkMetadata:
        """
        Return the :class:`ChunkMetadata` for a given chunk, optionally
        enriching it with computed fields.

        Args:
            chunk: The chunk whose metadata to retrieve / enrich.

        Returns:
            The (potentially enriched) metadata object.
        """
        meta = chunk.metadata
        # Enrich token estimate if not yet computed
        if meta.token_estimate == 0:
            meta.token_estimate = max(1, math.ceil(len(chunk.text) / 4.0))
        return meta

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect_sections(self, text: str) -> List[Dict[str, Any]]:
        """
        Scan *text* for section headers in Arabic and English.

        Returns a list of dicts: ``{"name": str, "start": int, "end": int}``.
        """
        sections: List[Dict[str, Any]] = []
        seen: set = set()

        all_patterns = _ARABIC_SECTION_PATTERNS + _ENGLISH_SECTION_PATTERNS
        for pattern in all_patterns:
            for match in pattern.finditer(text):
                name = match.group().strip()
                if name and name not in seen:
                    seen.add(name)
                    sections.append({
                        "name": name,
                        "start": match.start(),
                        "end": match.end(),
                    })

        sections.sort(key=lambda s: s["start"])
        return sections

    def _chunk_by_sections(
        self,
        text: str,
        sections: List[Dict[str, Any]],
        chunk_size: int,
        overlap: int,
    ) -> List[Dict[str, str]]:
        """
        Split *text* respecting detected section boundaries.

        Returns list of ``{"text": str, "section": str | None}`` dicts.
        """
        raw: List[Dict[str, str]] = []

        for i, section in enumerate(sections):
            start = section["end"]
            end = sections[i + 1]["start"] if i + 1 < len(sections) else len(text)
            body = text[start:end].strip()
            header = section["name"]

            if not body:
                raw.append({"text": header, "section": header})
                continue

            # If body fits in one chunk, keep it together
            if len(body) <= chunk_size:
                raw.append({"text": f"{header}\n{body}", "section": header})
            else:
                # Split body respecting paragraphs
                sub_chunks = self._split_by_size(
                    body, chunk_size - len(header) - 1, overlap
                )
                for j, sc in enumerate(sub_chunks):
                    chunk_text = f"{header}\n{sc}" if j > 0 or self.config.preserve_headers else sc
                    if j == 0:
                        chunk_text = f"{header}\n{sc}"
                    raw.append({"text": chunk_text, "section": header})

        return raw

    def _chunk_by_paragraphs(self, text: str, chunk_size: int, overlap: int) -> List[Dict[str, str]]:
        """
        Split *text* at paragraph boundaries when no sections are detected.

        Returns list of ``{"text": str, "section": None}`` dicts.
        """
        if self.config.respect_paragraphs:
            paragraphs = re.split(r"\n\s*\n", text)
            # Filter empty paragraphs
            paragraphs = [p.strip() for p in paragraphs if p.strip()]
        else:
            paragraphs = [text.strip()]

        raw: List[Dict[str, str]] = []

        current = ""
        current_paras: List[str] = []

        for para in paragraphs:
            if not current:
                current = para
                current_paras = [para]
            elif len(current) + len(para) + 2 <= chunk_size:
                current_paras.append(para)
                current = "\n\n".join(current_paras)
            else:
                # Flush current buffer
                if len(current.strip()) >= self.config.min_chunk_size:
                    raw.append({"text": current, "section": None})

                # Start new buffer with overlap
                overlap_text = self._extract_overlap_text(current, overlap) if overlap > 0 else ""
                current = f"{overlap_text}\n\n{para}" if overlap_text else para
                current_paras = [para]

        if current.strip():
            raw.append({"text": current, "section": None})

        # Fallback: if still nothing (e.g. huge single paragraph)
        if not raw and text.strip():
            raw = self._split_by_size(text.strip(), chunk_size, overlap)
            raw = [{"text": t, "section": None} for t in raw]

        return raw

    def _split_by_size(self, text: str, chunk_size: int, overlap: int) -> List[str]:
        """
        Naive character-window split respecting sentence boundaries when possible.
        """
        if len(text) <= chunk_size:
            return [text]

        chunks: List[str] = []
        start = 0

        while start < len(text):
            end = min(start + chunk_size, len(text))

            if end < len(text):
                # Try to break at sentence boundary
                last_period = text.rfind(".", start, end)
                last_newline = text.rfind("\n", start, end)
                best_break = max(last_period, last_newline)

                if best_break > start + chunk_size * 0.3:
                    end = best_break + 1

            segment = text[start:end].strip()
            if segment and len(segment) >= self.config.min_chunk_size:
                chunks.append(segment)

            start = end - overlap if overlap > 0 and end < len(text) else end

        return chunks

    def _extract_overlap_text(self, text: str, overlap_size: int) -> str:
        """Extract the tail of *text* (up to *overlap_size* chars) at a sentence boundary."""
        if len(text) <= overlap_size:
            return text

        tail = text[-overlap_size:]
        # Cut at first sentence boundary
        for sep in (". ", "\n", "، "):  # Arabic comma included
            idx = tail.find(sep)
            if idx != -1:
                return tail[idx + len(sep):]
        return tail

    def _finalise_chunks(
        self,
        raw_chunks: List[Dict[str, str]],
        original_text: str,
    ) -> List[Chunk]:
        """
        Convert raw chunk dicts into :class:`Chunk` objects with metadata.
        """
        result: List[Chunk] = []

        for idx, raw in enumerate(raw_chunks):
            chunk_text = raw["text"]
            section = raw.get("section")

            # Compute approximate char offsets in the original text
            char_start = original_text.find(chunk_text[:80])
            if char_start == -1:
                char_start = 0
            char_end = char_start + len(chunk_text)

            language = self._detect_language(chunk_text)
            token_estimate = max(1, math.ceil(len(chunk_text) / 4.0))

            meta = ChunkMetadata(
                chunk_index=idx,
                section=section,
                language=language,
                char_start=char_start,
                char_end=char_end,
                token_estimate=token_estimate,
            )

            chunk = Chunk(id=str(uuid4()), text=chunk_text, metadata=meta)
            result.append(chunk)

        return result

    @staticmethod
    def _detect_language(text: str) -> str:
        """
        Detect primary language of a text snippet.

        Returns ``'ar'``, ``'en'``, or ``'mixed'``.
        """
        has_arabic = any("\u0600" <= c <= "\u06FF" for c in text)
        has_latin = any(c.isascii() and c.isalpha() for c in text)

        if has_arabic and has_latin:
            return "mixed"
        elif has_arabic:
            return "ar"
        elif has_latin:
            return "en"
        return "mixed"

    @staticmethod
    def _strip_header(text: str, header: str) -> str:
        """Remove a leading section header from chunk text if present."""
        stripped = text.strip()
        for candidate in (header, header.rstrip(":"), header.rstrip(".")):
            if stripped.startswith(candidate):
                rest = stripped[len(candidate):].strip()
                return rest if rest else stripped
        return stripped
