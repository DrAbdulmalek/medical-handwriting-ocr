"""
Audio transcription processor for medical recordings.

Provides speech-to-text capabilities using OpenAI Whisper with support for
Arabic and English languages, language auto-detection, word-level timestamps,
and medical terminology post-processing.

Typical use-cases:
    - Doctor dictations
    - Patient consultation recordings
    - Medical lecture transcriptions
    - Voice notes from healthcare professionals
"""

import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-loaded optional dependencies
# ---------------------------------------------------------------------------

try:
    import faster_whisper
    HAS_FASTER_WHISPER = True
except ImportError:
    HAS_FASTER_WHISPER = False
    logger.info("faster-whisper not installed – falling back to openai-whisper")

try:
    import whisper as openai_whisper
    HAS_WHISPER = True
except ImportError:
    HAS_WHISPER = False
    logger.warning("Neither faster-whisper nor openai-whisper installed. Audio processing disabled.")


# ---------------------------------------------------------------------------
# Pydantic data models
# ---------------------------------------------------------------------------


class TranscriptionSegment(BaseModel):
    """A single timed segment of a transcription."""

    id: int = Field(..., description="Segment index (0-based)")
    start: float = Field(..., description="Start time in seconds")
    end: float = Field(..., description="End time in seconds")
    text: str = Field(..., description="Transcribed text for this segment")

    # Word-level timestamps (may be empty for some models)
    words: List[Dict[str, Union[float, str]]] = Field(
        default_factory=list,
        description="List of {word, start, end} dicts when available",
    )

    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Segment-level confidence score",
    )

    avg_logprob: float = Field(
        default=0.0,
        description="Average log-probability of tokens in the segment",
    )


class MedicalTerm(BaseModel):
    """A medical term extracted from a transcription."""

    term: str = Field(..., description="The medical term as found in text")
    normalized: str = Field(..., description="Normalised/lowercased form")
    start: float = Field(default=0.0, description="Start time in the audio (seconds)")
    end: float = Field(default=0.0, description="End time in the audio (seconds)")
    category: str = Field(
        default="general",
        description="Term category: diagnosis, medication, procedure, anatomy, lab, general",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence that this is a real medical term",
    )


class TranscriptionResult(BaseModel):
    """Full transcription result for an audio file."""

    text: str = Field(..., description="Full transcribed text")
    language: str = Field(..., description="Detected or specified language code")
    language_probability: float = Field(
        default=1.0,
        description="Probability of the detected language",
    )
    segments: List[TranscriptionSegment] = Field(
        default_factory=list,
        description="Timed segments of the transcription",
    )
    duration: float = Field(default=0.0, description="Audio duration in seconds")
    medical_terms: List[MedicalTerm] = Field(
        default_factory=list,
        description="Extracted medical terminology",
    )
    processing_time: float = Field(
        default=0.0,
        description="Wall-clock processing time in seconds",
    )


class DiarizedTranscription(BaseModel):
    """Transcription result combined with speaker labels."""

    text: str = Field(..., description="Full transcribed text")
    language: str = Field(default="en", description="Detected language code")
    segments: List[Dict] = Field(
        default_factory=list,
        description="Segments with speaker labels: {speaker, start, end, text}",
    )
    duration: float = Field(default=0.0, description="Audio duration in seconds")
    medical_terms: List[MedicalTerm] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Medical terminology helpers
# ---------------------------------------------------------------------------

# Common Arabic medical keywords mapped to categories
_ARABIC_MEDICAL_TERMS: Dict[str, str] = {
    # Diagnoses
    "سرطان": "diagnosis",
    "السكري": "diagnosis",
    "ضغط الدم": "diagnosis",
    "ارتفاع ضغط الدم": "diagnosis",
    "القلب": "diagnosis",
    "الربو": "diagnosis",
    "التهاب": "diagnosis",
    "حساسية": "diagnosis",
    "الصداع": "diagnosis",
    "التهاب الكبد": "diagnosis",
    "الفشل الكلوي": "diagnosis",
    # Medications
    "دواء": "medication",
    "قرص": "medication",
    "حقنة": "medication",
    "كبسولة": "medication",
    "مضاد حيوي": "medication",
    "مسكن": "medication",
    "فيتامين": "medication",
    "أنسولين": "medication",
    # Procedures
    "عملية": "procedure",
    "جراحة": "procedure",
    "فحص": "procedure",
    "أشعة": "procedure",
    "تحليل": "procedure",
    "تخطيط": "procedure",
    "تنظير": "procedure",
    "خزعة": "procedure",
    # Anatomy
    "الرئة": "anatomy",
    "الكبد": "anatomy",
    "الكلية": "anatomy",
    "المعدة": "anatomy",
    "الدماغ": "anatomy",
    "العين": "anatomy",
    "الأذن": "anatomy",
    "الأنف": "anatomy",
    "الحنجرة": "anatomy",
    "العمود الفقري": "anatomy",
    # Lab
    "تحليل دم": "lab",
    "صورة دم": "lab",
    "سكر الدم": "lab",
    "هرمون": "lab",
    "كوليسترول": "lab",
}

# English medical terminology patterns (regex)
_ENGLISH_MEDICAL_PATTERNS: List[tuple] = [
    # (compiled regex, category)
    (re.compile(r"\b(?:hypertension|diabetes|cancer|asthma|arthritis|pneumonia|bronchitis)\b", re.I), "diagnosis"),
    (re.compile(r"\b(?:metformin|insulin|amoxicillin|ibuprofen|paracetamol|aspirin)\b", re.I), "medication"),
    (re.compile(r"\b(?:biopsy|surgery|MRI|CT scan|X-ray|ultrasound|endoscopy)\b", re.I), "procedure"),
    (re.compile(r"\b(?:heart|lung|liver|kidney|brain|stomach|intestine|pancreas)\b", re.I), "anatomy"),
    (re.compile(r"\b(?:hemoglobin|glucose|cholesterol|creatinine|potassium|sodium)\b", re.I), "lab"),
]

# Supported language codes mapped to whisper / faster-whisper codes
_SUPPORTED_LANGUAGES = {
    "ar": "arabic",
    "en": "english",
    "arabic": "arabic",
    "english": "english",
    "auto": None,  # auto-detect
}


# ---------------------------------------------------------------------------
# AudioProcessor
# ---------------------------------------------------------------------------


class AudioProcessor:
    """
    Transcribe medical audio recordings using OpenAI Whisper.

    Supports Arabic and English transcription with automatic language detection,
    word-level timestamps, and medical terminology extraction.

    Attributes:
        model_size: Whisper model identifier (e.g. ``"base"``, ``"medium"``,
            ``"large-v3"``).
        device: Compute device – ``"cuda"`` or ``"cpu"``.
        compute_type: Precision for faster-whisper (``"float16"`` / ``"int8"``).
    """

    def __init__(
        self,
        model_size: str = "medium",
        device: Optional[str] = None,
        compute_type: str = "float16",
    ) -> None:
        self.model_size = model_size
        self.device = device or ("cuda" if self._has_cuda() else "cpu")
        self.compute_type = compute_type

        self._model = None
        self._whisper_model = None  # openai-whisper fallback

        logger.info(
            "AudioProcessor initialised  model=%s  device=%s  compute_type=%s",
            self.model_size,
            self.device,
            self.compute_type,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def transcribe_audio(
        self,
        file_path: Union[str, Path],
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        """
        Transcribe an audio file.

        Args:
            file_path: Path to the audio file (WAV, MP3, FLAC, OGG, M4A …).
            language: ISO 639-1 language code (``"ar"``, ``"en"``) or ``None``
                for auto-detection.

        Returns:
            A :class:`TranscriptionResult` with full text, segments, and
            extracted medical terms.

        Raises:
            FileNotFoundError: If *file_path* does not exist.
            RuntimeError: If no whisper backend is available.
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Audio file not found: {file_path}")

        t0 = time.time()
        logger.info("Transcribing audio: %s  language=%s", file_path.name, language)

        lang_code = self._normalise_language(language)

        if HAS_FASTER_WHISPER:
            segments, info = self._transcribe_faster_whisper(str(file_path), lang_code)
        elif HAS_WHISPER:
            segments, info = self._transcribe_whisper(str(file_path), lang_code)
        else:
            raise RuntimeError(
                "No whisper backend available. Install faster-whisper or openai-whisper."
            )

        transcription_segments = self._build_segments(segments)
        full_text = " ".join(seg.text for seg in transcription_segments).strip()

        medical_terms = self.extract_medical_terms(
            full_text, transcription_segments
        )

        processing_time = time.time() - t0
        logger.info(
            "Transcription complete: %.1fs audio → %.2f chars in %.1fs  lang=%s",
            info.get("duration", 0),
            len(full_text),
            processing_time,
            info.get("language", "unknown"),
        )

        return TranscriptionResult(
            text=full_text,
            language=info.get("language", lang_code or "en"),
            language_probability=info.get("language_probability", 1.0),
            segments=transcription_segments,
            duration=info.get("duration", 0.0),
            medical_terms=medical_terms,
            processing_time=processing_time,
        )

    def transcribe_with_diarization(
        self,
        file_path: Union[str, Path],
    ) -> DiarizedTranscription:
        """
        Transcribe audio and perform speaker diarization.

        Falls back to single-speaker transcription if pyannote is not available.

        Args:
            file_path: Path to the audio file.

        Returns:
            A :class:`DiarizedTranscription` with speaker-labelled segments.
        """
        # First get raw transcription
        result = self.transcribe_audio(file_path)

        # Attempt diarization
        try:
            from app.media.speaker_diarization import SpeakerDiarization

            diarizer = SpeakerDiarization()
            diarization = diarizer.diarize(str(file_path))

            # Lazy import to avoid circular deps at module level
            from app.media.speaker_diarization import SpeakerDiarization as SD
            combined = SD.merge_with_transcription(diarization, result)  # type: ignore[attr-defined]

            return DiarizedTranscription(
                text=combined.full_text,
                language=result.language,
                segments=[
                    {
                        "speaker": seg.speaker,
                        "start": seg.start,
                        "end": seg.end,
                        "text": seg.text,
                    }
                    for seg in combined.segments
                ],
                duration=result.duration,
                medical_terms=result.medical_terms,
            )
        except Exception as exc:
            logger.warning("Diarization unavailable (%s), returning single-speaker result", exc)
            # Wrap plain segments with a default speaker label
            return DiarizedTranscription(
                text=result.text,
                language=result.language,
                segments=[
                    {
                        "speaker": "SPEAKER_00",
                        "start": seg.start,
                        "end": seg.end,
                        "text": seg.text,
                    }
                    for seg in result.segments
                ],
                duration=result.duration,
                medical_terms=result.medical_terms,
            )

    def extract_medical_terms(
        self,
        transcription: str,
        segments: Optional[List[TranscriptionSegment]] = None,
    ) -> List[MedicalTerm]:
        """
        Extract medical terminology from a transcription.

        Uses curated Arabic dictionaries and English regex patterns to find
        diagnoses, medications, procedures, anatomical terms, and lab values.

        Args:
            transcription: The full transcribed text.
            segments: Optional timed segments for temporal localisation.

        Returns:
            A list of :class:`MedicalTerm` entries sorted by position.
        """
        terms: List[MedicalTerm] = []
        seen: set = set()

        # --- Arabic terms ---
        for term_str, category in _ARABIC_MEDICAL_TERMS.items():
            positions = [m.start() for m in re.finditer(re.escape(term_str), transcription)]
            for pos in positions:
                normalised = term_str.strip()
                if normalised in seen:
                    continue
                seen.add(normalised)

                # Approximate audio time from character position
                start_t, end_t = self._char_pos_to_time(transcription, pos, len(term_str), segments)
                terms.append(
                    MedicalTerm(
                        term=term_str,
                        normalized=normalised,
                        start=start_t,
                        end=end_t,
                        category=category,
                        confidence=0.9,
                    )
                )

        # --- English terms ---
        for pattern, category in _ENGLISH_MEDICAL_PATTERNS:
            for match in pattern.finditer(transcription):
                matched_text = match.group()
                if matched_text.lower() in seen:
                    continue
                seen.add(matched_text.lower())

                start_t, end_t = self._char_pos_to_time(
                    transcription, match.start(), match.end() - match.start(), segments
                )
                terms.append(
                    MedicalTerm(
                        term=matched_text,
                        normalized=matched_text.lower(),
                        start=start_t,
                        end=end_t,
                        category=category,
                        confidence=0.85,
                    )
                )

        terms.sort(key=lambda t: t.start)
        return terms

    # ------------------------------------------------------------------
    # faster-whisper backend
    # ------------------------------------------------------------------

    def _transcribe_faster_whisper(
        self,
        file_path: str,
        language: Optional[str],
    ) -> tuple:
        """Run transcription via faster-whisper. Returns (segments_iter, info_dict)."""
        model = self._get_faster_whisper_model()

        segments_iter, info = model.transcribe(
            file_path,
            language=language,
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
        )

        collected = list(segments_iter)
        return collected, {
            "language": info.language,
            "language_probability": info.language_probability,
            "duration": info.duration,
        }

    def _get_faster_whisper_model(self):  # type: ignore[return]
        """Lazy-load faster-whisper model."""
        if self._model is None:
            logger.info("Loading faster-whisper model '%s' on %s …", self.model_size, self.device)
            self._model = faster_whisper.WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
            logger.info("faster-whisper model loaded")
        return self._model

    # ------------------------------------------------------------------
    # openai-whisper backend (fallback)
    # ------------------------------------------------------------------

    def _transcribe_whisper(
        self,
        file_path: str,
        language: Optional[str],
    ) -> tuple:
        """Run transcription via openai-whisper. Returns (segments, info_dict)."""
        model = self._get_whisper_model()

        result = model.transcribe(
            file_path,
            language=language,
            word_timestamps=True,
        )

        raw_segments = result.get("segments", [])
        info = {
            "language": result.get("language", "en"),
            "language_probability": 1.0,
            "duration": result.get("segments", [{}])[-1].get("end", 0) if raw_segments else 0.0,
        }
        return raw_segments, info

    def _get_whisper_model(self):
        """Lazy-load openai-whisper model."""
        if self._whisper_model is None:
            logger.info("Loading openai-whisper model '%s' …", self.model_size)
            self._whisper_model = openai_whisper.load_model(self.model_size)
            logger.info("openai-whisper model loaded")
        return self._whisper_model

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_segments(raw_segments) -> List[TranscriptionSegment]:
        """Convert raw whisper segments into :class:`TranscriptionSegment` objects."""
        built: List[TranscriptionSegment] = []
        for idx, seg in enumerate(raw_segments):
            # faster-whisper uses attributes, openai-whisper uses dicts
            if hasattr(seg, "start"):
                start = seg.start
                end = seg.end
                text = getattr(seg, "text", "")
                avg_logprob = getattr(seg, "avg_logprob", 0.0)
                words = []
                if hasattr(seg, "words") and seg.words:
                    words = [
                        {"word": w.word, "start": w.start, "end": w.end}
                        for w in seg.words
                        if w.word.strip()
                    ]
            else:
                start = seg.get("start", 0.0)
                end = seg.get("end", 0.0)
                text = seg.get("text", "")
                avg_logprob = seg.get("avg_logprob", 0.0)
                words = seg.get("words", [])

            built.append(
                TranscriptionSegment(
                    id=idx,
                    start=round(start, 3),
                    end=round(end, 3),
                    text=text.strip(),
                    words=words,
                    avg_logprob=round(float(avg_logprob), 4),
                )
            )
        return built

    @staticmethod
    def _char_pos_to_time(
        text: str,
        char_start: int,
        char_length: int,
        segments: Optional[List[TranscriptionSegment]],
    ) -> tuple:
        """Map a character position to an approximate (start, end) time in seconds."""
        if not segments:
            return 0.0, 0.0

        total_chars = len(text)
        if total_chars == 0:
            return 0.0, 0.0

        char_ratio_start = char_start / total_chars
        char_ratio_end = (char_start + char_length) / total_chars

        duration = segments[-1].end if segments else 0.0
        return round(char_ratio_start * duration, 3), round(char_ratio_end * duration, 3)

    @staticmethod
    def _normalise_language(language: Optional[str]) -> Optional[str]:
        """Map user-facing language string to whisper language code."""
        if language is None:
            return None
        code = _SUPPORTED_LANGUAGES.get(language.lower().strip())
        if code is not None:
            return code
        # If already a valid 2-letter code, return as-is
        if len(language.strip()) == 2:
            return language.strip().lower()
        return None

    @staticmethod
    def _has_cuda() -> bool:
        """Check if CUDA is available for GPU acceleration."""
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False


# ---------------------------------------------------------------------------
# Singleton instance (lazy-loaded on first use)
# ---------------------------------------------------------------------------

_audio_processor: Optional[AudioProcessor] = None


def get_audio_processor(
    model_size: str = "medium",
    device: Optional[str] = None,
    compute_type: str = "float16",
) -> AudioProcessor:
    """
    Get or create the shared :class:`AudioProcessor` singleton.

    Subsequent calls with different parameters are ignored – the first call
    determines the configuration.
    """
    global _audio_processor
    if _audio_processor is None:
        _audio_processor = AudioProcessor(
            model_size=model_size,
            device=device,
            compute_type=compute_type,
        )
    return _audio_processor
