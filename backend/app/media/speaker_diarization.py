"""
Speaker diarization for medical recordings.

Identifies and labels different speakers in audio recordings using
pyannote.audio, with optional role classification (doctor, patient, nurse)
and transcription merging for speaker-attributed transcripts.

Typical use-cases:
    - Doctor-patient consultation recordings
    - Multi-disciplinary medical meetings
    - Medical dictation with back-and-forth discussions
"""

import logging
import time
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------

try:
    from pyannote.audio import Pipeline
    HAS_PYANNOTE = True
except ImportError:
    HAS_PYANNOTE = False
    logger.info("pyannote.audio not installed – speaker diarization disabled")


# ---------------------------------------------------------------------------
# Pydantic data models
# ---------------------------------------------------------------------------


class SpeakerSegment(BaseModel):
    """A contiguous time span attributed to a single speaker."""

    speaker: str = Field(..., description="Speaker label (e.g. SPEAKER_00)")
    start: float = Field(..., description="Segment start time in seconds")
    end: float = Field(..., description="Segment end time in seconds")
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Diarization confidence for this segment",
    )


class DiarizationResult(BaseModel):
    """Full diarization output for an audio file."""

    file_path: str = Field(..., description="Original audio file path")
    segments: List[SpeakerSegment] = Field(
        default_factory=list,
        description="Speaker-labelled time segments",
    )
    num_speakers: int = Field(default=0, description="Number of distinct speakers")
    duration: float = Field(default=0.0, description="Audio duration in seconds")
    processing_time: float = Field(default=0.0)


class CombinedSegment(BaseModel):
    """A transcription segment merged with speaker label."""

    speaker: str = Field(..., description="Speaker label")
    start: float = Field(..., description="Segment start in seconds")
    end: float = Field(..., description="Segment end in seconds")
    text: str = Field(default="", description="Transcribed text")
    confidence: float = Field(default=1.0, description="Overall confidence")


class CombinedResult(BaseModel):
    """Result of merging diarization with transcription."""

    full_text: str = Field(default="", description="Full text with speaker markers")
    segments: List[CombinedSegment] = Field(
        default_factory=list,
        description="Speaker-attributed transcript segments",
    )
    language: str = Field(default="en")


class SpeakerRole(BaseModel):
    """Identified role for a speaker."""

    speaker: str = Field(..., description="Speaker label")
    role: str = Field(
        ...,
        description="Inferred role: doctor, patient, nurse, other",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence of role assignment",
    )
    total_speaking_time: float = Field(
        default=0.0,
        description="Total speaking time in seconds",
    )
    num_turns: int = Field(
        default=0,
        description="Number of speaking turns",
    )


class SpeakerRoles(BaseModel):
    """Role assignments for all speakers."""

    roles: List[SpeakerRole] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Keyword-based role identification
# ---------------------------------------------------------------------------

# Arabic role-indicating phrases
_DOCTOR_KEYWORDS_AR = [
    "سأقوم بفحص", "الدواء", "العلاج", "التشخيص", "وصفة",
    "التحاليل", "الأشعة", "أرى", "أنصحك", "المريض",
    "الجرعة", "أخذ العينة", "سأكتب", "الحالة",
]

_PATIENT_KEYWORDS_AR = [
    "أشعر", "ألم", "الم", "عندي", "منذ", "عندما",
    "صعوبة", "أعاني", "لا أستطيع", "مرت", "الحمد لله",
    "مشكلة", "عرض", "نصحني",
]

_NURSE_KEYWORDS_AR = [
    "التمريض", "الضغط", "الحرارة", "الوزن", "القسطرة",
    "الحقنة", "جدول", "المناوبة", "الوردي", "تحضير",
]

# English role-indicating phrases
_DOCTOR_KEYWORDS_EN = [
    "i'm going to examine", "the treatment", "the diagnosis", "prescription",
    "i recommend", "the test results", "your lab work", "i'll write",
    "the dosage", "i'm prescribing", "the condition", "let's check",
    "i'd like to", "we need to", "your symptoms indicate",
]

_PATIENT_KEYWORDS_EN = [
    "i feel", "i've been having", "it hurts", "i've been experiencing",
    "for the past", "i can't", "i'm having trouble", "my pain",
    "it started when", "i noticed", "i was diagnosed", "my symptoms",
    "i've been feeling", "i keep getting", "it's been",
]

_NURSE_KEYWORDS_EN = [
    "your vitals", "blood pressure", "temperature", "the nurse",
    "i'll take your", "the injection", "your weight", "the IV",
    "before the doctor", "after the procedure", "follow-up appointment",
]


# ---------------------------------------------------------------------------
# SpeakerDiarization
# ---------------------------------------------------------------------------


class SpeakerDiarization:
    """
    Speaker diarization for medical audio recordings.

    Uses pyannote.audio to partition an audio file into speaker-labelled
    segments.  When pyannote is not available, falls back to a simple
    energy-based segmentation heuristic.

    Role identification is performed via keyword analysis of the transcript,
    classifying speakers as doctor, patient, nurse, or other.
    """

    def __init__(
        self,
        pipeline_model: str = "pyannote/speaker-diarization-3.1",
        use_auth_token: Optional[str] = None,
    ) -> None:
        """
        Args:
            pipeline_model: Hugging Face model identifier for the
                pyannote diarization pipeline.
            use_auth_token: Hugging Face access token (required for
                pyannote models). If ``None``, reads from
                ``settings.PYANNOTE_HF_TOKEN`` or ``HF_TOKEN`` env var.
        """
        self.pipeline_model = pipeline_model
        self._auth_token = use_auth_token or getattr(settings, "PYANNOTE_HF_TOKEN", None)
        self._pipeline: Optional["Pipeline"] = None

        if not HAS_PYANNOTE:
            logger.warning(
                "pyannote.audio is not installed. "
                "Install with: pip install pyannote.audio"
            )

        logger.info("SpeakerDiarization initialised  model=%s", pipeline_model)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def diarize(
        self,
        file_path: Union[str, "Path"],
        num_speakers: Optional[int] = None,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
    ) -> DiarizationResult:
        """
        Perform speaker diarization on an audio file.

        Args:
            file_path: Path to the audio file.
            num_speakers: Exact number of speakers (hint). ``None`` = auto-detect.
            min_speakers: Minimum number of speakers.
            max_speakers: Maximum number of speakers.

        Returns:
            A :class:`DiarizationResult` with speaker-labelled segments.

        Raises:
            RuntimeError: If pyannote.audio is not installed.
            FileNotFoundError: If the audio file doesn't exist.
        """
        from pathlib import Path

        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Audio file not found: {file_path}")

        t0 = time.time()
        logger.info("Diarizing: %s  num_speakers=%s", file_path.name, num_speakers)

        if HAS_PYANNOTE:
            segments = self._diarize_pyannote(
                str(file_path),
                num_speakers=num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )
        else:
            logger.warning("pyannote not available – using fallback segmentation")
            segments = self._fallback_segmentation(str(file_path), num_speakers)

        speakers = {seg.speaker for seg in segments}
        duration = segments[-1].end if segments else 0.0

        processing_time = round(time.time() - t0, 2)
        logger.info(
            "Diarization complete: %d speakers, %d segments, %.2fs",
            len(speakers),
            len(segments),
            processing_time,
        )

        return DiarizationResult(
            file_path=str(file_path.resolve()),
            segments=segments,
            num_speakers=len(speakers),
            duration=duration,
            processing_time=processing_time,
        )

    @staticmethod
    def merge_with_transcription(
        diarization: DiarizationResult,
        transcription: "TranscriptionResult",
    ) -> CombinedResult:
        """
        Merge speaker diarization segments with a transcription.

        For each diarization segment, the overlapping transcription text is
        attached, producing speaker-attributed transcript segments.

        Args:
            diarization: Output from :meth:`diarize`.
            transcription: Output from :class:`AudioProcessor.transcribe_audio`.

        Returns:
            A :class:`CombinedResult` with speaker-attributed text.
        """
        combined_segments: List[CombinedSegment] = []
        text_parts: List[str] = []

        for d_seg in diarization.segments:
            # Find all transcription segments that overlap
            seg_texts: List[str] = []
            for t_seg in transcription.segments:
                # Check overlap: segments overlap if neither ends before the other starts
                if t_seg.start < d_seg.end and t_seg.end > d_seg.start:
                    seg_texts.append(t_seg.text.strip())

            combined_text = " ".join(seg_texts).strip()

            combined_segments.append(
                CombinedSegment(
                    speaker=d_seg.speaker,
                    start=d_seg.start,
                    end=d_seg.end,
                    text=combined_text,
                    confidence=d_seg.confidence,
                )
            )

            if combined_text:
                text_parts.append(f"[{d_seg.speaker}] {combined_text}")

        full_text = "\n".join(text_parts)

        return CombinedResult(
            full_text=full_text,
            segments=combined_segments,
            language=transcription.language,
        )

    def identify_roles(
        self,
        diarization_result: DiarizationResult,
        transcription_text: Optional[str] = None,
    ) -> SpeakerRoles:
        """
        Infer speaker roles (doctor, patient, nurse, other) using
        keyword analysis on the associated transcription.

        Args:
            diarization_result: Output from :meth:`diarize`.
            transcription_text: Optional full transcript text for
                additional context.

        Returns:
            A :class:`SpeakerRoles` with role assignments.
        """
        if not diarization_result.segments:
            return SpeakerRoles(roles=[])

        # Collect text per speaker
        speaker_texts: Dict[str, str] = {}
        speaker_durations: Dict[str, float] = {}
        speaker_turns: Dict[str, int] = {}

        for seg in diarization_result.segments:
            speaker_texts.setdefault(seg.speaker, "")
            speaker_durations.setdefault(seg.speaker, 0.0)
            speaker_turns.setdefault(seg.speaker, 0)

            speaker_durations[seg.speaker] += seg.end - seg.start
            speaker_turns[seg.speaker] += 1

        # If we have combined text, try to use the full transcript + speaker mapping
        # Otherwise fall back to keyword density heuristics
        # For the heuristic we just need to detect which speaker talks more
        # (typically the doctor) vs uses patient-like language.

        roles: List[SpeakerRole] = []

        # Sort speakers by total speaking time (descending)
        sorted_speakers = sorted(
            speaker_durations.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        # Apply keyword scoring
        speaker_scores: Dict[str, Dict[str, float]] = {}
        for speaker in speaker_texts:
            speaker_scores[speaker] = {
                "doctor": 0.0,
                "patient": 0.0,
                "nurse": 0.0,
            }

        # Score using transcription text (if combined segments are available)
        if transcription_text:
            for speaker_label in speaker_texts:
                # Collect text spoken by this speaker from the full transcript
                speaker_text = self._extract_speaker_text(
                    transcription_text, speaker_label
                )
                if speaker_text:
                    self._score_keywords(speaker_text, speaker_scores[speaker_label])

        # Assign roles based on scores
        assigned: set = set()
        for speaker, duration in sorted_speakers:
            scores = speaker_scores.get(speaker, {"doctor": 0, "patient": 0, "nurse": 0})

            # Find best unassigned role
            best_role = "other"
            best_score = 0.0

            for role in ["doctor", "patient", "nurse"]:
                if role not in assigned and scores[role] > best_score:
                    best_score = scores[role]
                    best_role = role

            # If no keywords matched, use heuristic: longest speaker = doctor
            if best_score == 0.0:
                if len(assigned) == 0:
                    best_role = "doctor"
                elif len(assigned) == 1:
                    best_role = "patient"
                else:
                    best_role = "nurse"

            assigned.add(best_role)

            roles.append(
                SpeakerRole(
                    speaker=speaker,
                    role=best_role,
                    confidence=min(best_score + 0.1, 1.0) if best_score > 0 else 0.3,
                    total_speaking_time=round(duration, 2),
                    num_turns=speaker_turns.get(speaker, 0),
                )
            )

        return SpeakerRoles(roles=roles)

    # ------------------------------------------------------------------
    # pyannote backend
    # ------------------------------------------------------------------

    def _diarize_pyannote(
        self,
        file_path: str,
        num_speakers: Optional[int] = None,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
    ) -> List[SpeakerSegment]:
        """Run diarization via pyannote.audio pipeline."""
        pipeline = self._get_pipeline()

        diarization_params: Dict = {}
        if num_speakers is not None:
            diarization_params["num_speakers"] = num_speakers
        if min_speakers is not None:
            diarization_params["min_speakers"] = min_speakers
        if max_speakers is not None:
            diarization_params["max_speakers"] = max_speakers

        diarization = pipeline(file_path, **diarization_params)

        segments: List[SpeakerSegment] = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append(
                SpeakerSegment(
                    speaker=speaker,
                    start=round(turn.start, 3),
                    end=round(turn.end, 3),
                )
            )

        return segments

    def _get_pipeline(self) -> "Pipeline":  # type: ignore[return]
        """Lazy-load pyannote pipeline."""
        if self._pipeline is None:
            if self._auth_token:
                self._pipeline = Pipeline.from_pretrained(
                    self.pipeline_model,
                    use_auth_token=self._auth_token,
                )
            else:
                self._pipeline = Pipeline.from_pretrained(self.pipeline_model)

            # Use GPU if available
            try:
                import torch
                if torch.cuda.is_available():
                    self._pipeline.to(torch.device("cuda"))
                    logger.info("pyannote pipeline moved to CUDA")
            except ImportError:
                pass

            logger.info("pyannote pipeline loaded: %s", self.pipeline_model)
        return self._pipeline

    # ------------------------------------------------------------------
    # Fallback segmentation (when pyannote is unavailable)
    # ------------------------------------------------------------------

    @staticmethod
    def _fallback_segmentation(
        file_path: str,
        num_speakers: Optional[int] = None,
    ) -> List[SpeakerSegment]:
        """
        Simple energy-based segmentation fallback.

        Divides the audio into equal-length chunks and assigns alternating
        speaker labels.  This is a very rough approximation.
        """
        try:
            import wave
        except ImportError:
            logger.error("wave module not available for fallback segmentation")
            return []

        try:
            with wave.open(file_path, "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                duration = frames / float(rate)
        except Exception as exc:
            logger.error("Cannot read WAV for fallback: %s", exc)
            return []

        chunk_duration = 5.0  # 5-second chunks
        num_chunks = max(1, int(duration / chunk_duration))
        num_s = num_speakers or min(2, num_chunks)

        segments: List[SpeakerSegment] = []
        for i in range(num_chunks):
            start = i * chunk_duration
            end = min(start + chunk_duration, duration)
            speaker = f"SPEAKER_{i % num_s:02d}"
            segments.append(
                SpeakerSegment(
                    speaker=speaker,
                    start=round(start, 3),
                    end=round(end, 3),
                    confidence=0.3,
                )
            )

        return segments

    # ------------------------------------------------------------------
    # Keyword scoring helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_speaker_text(full_text: str, speaker_label: str) -> str:
        """Extract text belonging to a specific speaker from combined transcript."""
        lines = full_text.split("\n")
        collected: List[str] = []
        for line in lines:
            if line.strip().startswith(f"[{speaker_label}]"):
                # Remove the speaker label prefix
                text_part = line.strip()
                prefix = f"[{speaker_label}]"
                if text_part.startswith(prefix):
                    text_part = text_part[len(prefix):].strip()
                collected.append(text_part)
        return " ".join(collected)

    @staticmethod
    def _score_keywords(text: str, scores: Dict[str, float]) -> None:
        """Score speaker text against role keyword lists."""
        text_lower = text.lower()

        for kw in _DOCTOR_KEYWORDS_AR + _DOCTOR_KEYWORDS_EN:
            if kw.lower() in text_lower:
                scores["doctor"] += 1.0

        for kw in _PATIENT_KEYWORDS_AR + _PATIENT_KEYWORDS_EN:
            if kw.lower() in text_lower:
                scores["patient"] += 1.0

        for kw in _NURSE_KEYWORDS_AR + _NURSE_KEYWORDS_EN:
            if kw.lower() in text_lower:
                scores["nurse"] += 1.0


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_diarization_instance: Optional[SpeakerDiarization] = None


def get_diarization(
    pipeline_model: str = "pyannote/speaker-diarization-3.1",
    use_auth_token: Optional[str] = None,
) -> SpeakerDiarization:
    """Get or create the shared :class:`SpeakerDiarization` singleton."""
    global _diarization_instance
    if _diarization_instance is None:
        _diarization_instance = SpeakerDiarization(
            pipeline_model=pipeline_model,
            use_auth_token=use_auth_token,
        )
    return _diarization_instance
