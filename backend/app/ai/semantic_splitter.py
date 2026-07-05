"""
Semantic Text Splitter for Medical Documents.

Splits text at semantic boundaries using sentence-level embedding similarity,
ensuring each chunk maintains coherent medical context.  Leverages
sentence-transformers models (e.g. ``all-MiniLM-L6-v2`` or domain-specific
medical embeddings) with a lazy-load strategy so models are only pulled into
memory on first use.
"""

import re
import math
import logging
from typing import Optional, List, Tuple
from uuid import uuid4
from datetime import datetime

import numpy as np
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Data Models
# =============================================================================


class SplitPoint(BaseModel):
    """A candidate position where the text may be split."""

    position: int = Field(description="Character offset in the source text")
    similarity_score: float = Field(description="Cosine similarity between left and right windows at this point")
    reason: str = Field(default="", description="Human-readable reason the splitter chose this point")
    is_section_boundary: bool = Field(default=False, description="True if the split coincides with a detected section header")


class SemanticChunk(BaseModel):
    """A single chunk produced by semantic splitting."""

    id: str = Field(default_factory=lambda: str(uuid4()), description="Unique chunk identifier")
    text: str = Field(description="Chunk text content")
    chunk_index: int = Field(description="Zero-based order of this chunk")
    char_start: int = Field(description="Start character offset in source text")
    char_end: int = Field(description="End character offset (exclusive) in source text")
    language: str = Field(default="mixed", description="Detected language: 'ar', 'en', or 'mixed'")
    split_points: List[SplitPoint] = Field(default_factory=list, description="Split points bounding this chunk")
    token_estimate: int = Field(default=0, description="Rough token count (~4 chars/token)")
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# =============================================================================
# Pre-compiled Patterns
# =============================================================================

_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?؟。\n])\s+|(?<=[،,])\s+",  # Arabic "؟" and comma "،" included
    re.UNICODE,
)

_SECTION_HEADER_RE = re.compile(
    r"(?:الشكوى|التشخيص|الأدوية|الفحص السريري|التاريخ المرضي|"
    r"النتائج|الخطة العلاجية|الملاحظات|"
    r"(?i)(?:Chief Complaint|Diagnosis|Medication|Clinical Exam|"
    r"Medical History|Results|Treatment Plan|Notes|Assessment|Plan|Vital Signs))",
    re.UNICODE,
)


# =============================================================================
# SemanticSplitter
# =============================================================================


class SemanticSplitter:
    """
    Splits medical text at natural semantic boundaries.

    The algorithm works as follows:

    1.  **Segment** the source text into individual sentences.
    2.  **Encode** each sentence into an embedding vector using a
        sentence-transformers model (lazy-loaded on first call).
    3.  **Measure** the cosine similarity between consecutive sentence
        embeddings.
    4.  **Identify split points** where similarity drops below a threshold,
        or where the cumulative character count exceeds ``max_chunk_size``.
    5.  **Assemble** chunks by grouping sentences between split points,
        optionally respecting section header boundaries.

    The model defaults to ``sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2``
    which supports Arabic, English, and many other languages.  Users may
    override via the constructor.
    """

    DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

    def __init__(
        self,
        model_name: Optional[str] = None,
        similarity_threshold: float = 0.5,
        buffer_size: int = 3,
        max_chunk_size: int = 500,
        embedding_batch_size: int = 32,
    ):
        """
        Args:
            model_name: HuggingFace model identifier for sentence-transformers.
            similarity_threshold: Minimum cosine similarity between adjacent
                sentences before a split point is considered.
            buffer_size: Number of adjacent sentence pairs used in the
                sliding-window similarity comparison.
            max_chunk_size: Hard ceiling on chunk length (in characters).
            embedding_batch_size: Sentences per batch when encoding.
        """
        self.model_name = model_name or self.DEFAULT_MODEL
        self.similarity_threshold = similarity_threshold
        self.buffer_size = buffer_size
        self.max_chunk_size = max_chunk_size
        self.embedding_batch_size = embedding_batch_size

        # Lazy-loaded state
        self._model = None
        self._model_loaded = False

        logger.info(
            "SemanticSplitter initialised (model=%s, threshold=%.2f, max_chunk=%d)",
            self.model_name,
            self.similarity_threshold,
            self.max_chunk_size,
        )

    # ------------------------------------------------------------------
    # Lazy Model Loading
    # ------------------------------------------------------------------

    def _load_model(self) -> None:
        """
        Lazy-load the sentence-transformers model on first use.

        Raises:
            ImportError: If ``sentence_transformers`` is not installed.
            RuntimeError: If model loading fails.
        """
        if self._model_loaded:
            return

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            logger.error(
                "sentence-transformers not installed. "
                "Install it with: pip install sentence-transformers"
            )
            raise ImportError(
                "sentence-transformers package is required for SemanticSplitter. "
                "Install with: pip install sentence-transformers"
            )

        try:
            logger.info("Loading sentence-transformers model '%s' …", self.model_name)
            self._model = SentenceTransformer(self.model_name)
            self._model_loaded = True
            logger.info("Model '%s' loaded successfully.", self.model_name)
        except Exception as exc:
            logger.error("Failed to load model '%s': %s", self.model_name, exc)
            raise RuntimeError(f"Cannot load model '{self.model_name}': {exc}") from exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def split_semantically(self, text: str, max_chunk_size: Optional[int] = None) -> List[SemanticChunk]:
        """
        Split *text* into semantically coherent chunks.

        Args:
            text: Source text (may be Arabic, English, or mixed).
            max_chunk_size: Override the configured hard ceiling.

        Returns:
            A list of :class:`SemanticChunk` objects in document order.
        """
        if not text or not text.strip():
            logger.warning("split_semantically called with empty text")
            return []

        hard_limit = max_chunk_size or self.max_chunk_size
        self._load_model()

        # 1. Segment into sentences
        sentences, offsets = self._segment_sentences(text)

        if len(sentences) <= 1:
            return [self._make_chunk(text, 0, len(text), 0)]

        # 2. Encode sentences
        embeddings = self._encode_sentences(sentences)

        # 3. Find split points
        split_points = self.find_split_points(text, embeddings, offsets)

        # 4. Build chunks respecting hard limit
        chunks = self._assemble_chunks(text, sentences, offsets, split_points, hard_limit)

        logger.info(
            "Semantic split produced %d chunks from %d sentences (source=%d chars)",
            len(chunks),
            len(sentences),
            len(text),
        )
        return chunks

    def compute_similarity(self, text1: str, text2: str) -> float:
        """
        Compute cosine similarity between two text passages using the
        loaded embedding model.

        Args:
            text1: First text passage.
            text2: Second text passage.

        Returns:
            Cosine similarity score in ``[0, 1]``.
        """
        self._load_model()

        emb1, emb2 = self._model.encode([text1, text2], normalize_embeddings=True)
        similarity = float(np.dot(emb1, emb2))
        return max(0.0, min(1.0, similarity))

    def find_split_points(
        self,
        text: str,
        embeddings: Optional[np.ndarray] = None,
        offsets: Optional[List[Tuple[int, int]]] = None,
    ) -> List[SplitPoint]:
        """
        Identify candidate split points in *text* based on embedding similarity.

        If *embeddings* is ``None`` the method will segment and encode the text
        internally.

        Args:
            text: Source text.
            embeddings: Pre-computed sentence embeddings ``(n_sentences, dim)``.
            offsets: List of ``(start, end)`` character offsets for each sentence.

        Returns:
            List of :class:`SplitPoint` objects in ascending order of position.
        """
        self._load_model()

        sentences, sent_offsets = offsets or [], []
        if embeddings is None:
            sentences, sent_offsets = self._segment_sentences(text)
            embeddings = self._encode_sentences(sentences)
        else:
            sentences, sent_offsets = self._segment_sentences(text)

        if len(embeddings) < 2:
            return []

        # Normalise embeddings for cosine similarity
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        normed = embeddings / norms

        split_points: List[SplitPoint] = []
        accumulated_chars = 0

        for i in range(len(sentences) - 1):
            # Sliding-window similarity: average over ``buffer_size`` pairs
            window_start = max(0, i - self.buffer_size + 1)
            similarities: List[float] = []
            for j in range(window_start, min(i + 1, len(normed) - 1)):
                sim = float(np.dot(normed[j], normed[j + 1]))
                similarities.append(sim)

            avg_sim = sum(similarities) / len(similarities) if similarities else 1.0
            accumulated_chars += len(sentences[i])

            # Decide whether to split here
            should_split = False
            reason = ""

            # Section boundary detection
            section_match = _SECTION_HEADER_RE.search(sentences[i + 1])
            if section_match:
                should_split = True
                reason = f"Section header detected: '{section_match.group()[:30]}'"
            # Similarity drop
            elif avg_sim < self.similarity_threshold:
                should_split = True
                reason = f"Similarity drop (avg={avg_sim:.3f} < threshold={self.similarity_threshold})"
            # Hard size limit
            elif accumulated_chars >= self.max_chunk_size:
                should_split = True
                reason = f"Max chunk size reached ({accumulated_chars} chars)"

            if should_split:
                # Split position is end of sentence i
                pos = sent_offsets[i][1]
                split_points.append(
                    SplitPoint(
                        position=pos,
                        similarity_score=round(avg_sim, 4),
                        reason=reason,
                        is_section_boundary=bool(section_match),
                    )
                )
                accumulated_chars = 0

        return split_points

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _segment_sentences(text: str) -> Tuple[List[str], List[Tuple[int, int]]]:
        """
        Split *text* into sentences with character offsets.

        Returns:
            Tuple of (sentences, offsets) where offsets[i] = (char_start, char_end).
        """
        raw_parts = _SENTENCE_SPLIT_RE.split(text)
        sentences: List[str] = []
        offsets: List[Tuple[int, int]] = []

        cursor = 0
        for part in raw_parts:
            part = part.strip()
            if not part:
                # Advance cursor past whitespace consumed by the regex
                idx = text.find(part, cursor) if part in text[cursor:] else cursor
                cursor = idx + len(part)
                continue

            start = text.find(part, cursor)
            if start == -1:
                start = cursor
            end = start + len(part)

            sentences.append(part)
            offsets.append((start, end))
            cursor = end

        # Fallback: if segmentation produced nothing, treat whole text as one sentence
        if not sentences and text.strip():
            sentences = [text.strip()]
            offsets = [(0, len(text.strip()))]

        return sentences, offsets

    def _encode_sentences(self, sentences: List[str]) -> np.ndarray:
        """Batch-encode sentences into embedding vectors."""
        try:
            embeddings = self._model.encode(
                sentences,
                batch_size=self.embedding_batch_size,
                normalize_embeddings=False,
                show_progress_bar=False,
            )
            return np.asarray(embeddings)
        except Exception as exc:
            logger.error("Sentence encoding failed: %s", exc)
            raise

    def _assemble_chunks(
        self,
        text: str,
        sentences: List[str],
        offsets: List[Tuple[int, int]],
        split_points: List[SplitPoint],
        hard_limit: int,
    ) -> List[SemanticChunk]:
        """
        Group sentences into chunks using the computed split points,
        enforcing the hard character limit.
        """
        chunks: List[SemanticChunk] = []

        boundaries = [0] + [sp.position for sp in split_points] + [len(text)]
        chunk_idx = 0

        for i in range(len(boundaries) - 1):
            start = boundaries[i]
            end = boundaries[i + 1]
            segment = text[start:end].strip()

            if not segment:
                continue

            # Split oversized segments further
            if len(segment) > hard_limit:
                sub_segments = self._hard_split(segment, hard_limit)
                for sub in sub_segments:
                    sub_start = text.find(sub, start)
                    if sub_start == -1:
                        sub_start = start
                    sub_end = sub_start + len(sub)
                    chunks.append(self._make_chunk(sub, sub_start, sub_end, chunk_idx))
                    chunk_idx += 1
            else:
                # Collect split points that belong to this chunk
                sp_for_chunk = [
                    sp for sp in split_points
                    if start <= sp.position <= end
                ]

                chunks.append(self._make_chunk(segment, start, end, chunk_idx, sp_for_chunk))
                chunk_idx += 1

        return chunks

    def _make_chunk(
        self,
        text: str,
        char_start: int,
        char_end: int,
        index: int,
        split_points: Optional[List[SplitPoint]] = None,
    ) -> SemanticChunk:
        """Construct a :class:`SemanticChunk` from the given fields."""
        has_arabic = any("\u0600" <= c <= "\u06FF" for c in text)
        has_latin = any(c.isascii() and c.isalpha() for c in text)
        language = "mixed" if (has_arabic and has_latin) else ("ar" if has_arabic else "en")

        return SemanticChunk(
            text=text,
            chunk_index=index,
            char_start=char_start,
            char_end=char_end,
            language=language,
            split_points=split_points or [],
            token_estimate=max(1, math.ceil(len(text) / 4)),
        )

    @staticmethod
    def _hard_split(text: str, max_size: int) -> List[str]:
        """
        Force-split a segment that exceeds the hard character limit.
        Tries to break at sentence endings first, then whitespace.
        """
        if len(text) <= max_size:
            return [text]

        parts: List[str] = []
        start = 0
        while start < len(text):
            end = min(start + max_size, len(text))
            if end < len(text):
                # Try sentence boundary
                for sep in (".", "؟", "!\n", "\n", " "):
                    sep_idx = text.rfind(sep, start, end)
                    if sep_idx > start + max_size * 0.3:
                        end = sep_idx + 1
                        break
            segment = text[start:end].strip()
            if segment:
                parts.append(segment)
            start = end
        return parts
