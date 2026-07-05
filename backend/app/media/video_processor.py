"""
Video processing and transcription for medical multimedia.

Provides capabilities to extract audio tracks, transcribe speech, and pull
key frames from video files (MP4, AVI, MKV, MOV, WEBM) using FFmpeg.

Typical use-cases:
    - Medical procedure recordings
    - Lecture / training video transcriptions
    - Telemedicine session archives
    - Surgical video documentation
"""

import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional heavy dependencies
# ---------------------------------------------------------------------------

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False
    logger.info("opencv-python not installed – keyframe extraction disabled")

try:
    from PIL import Image as PILImage
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    logger.info("Pillow not installed – image saving for keyframes disabled")


# ---------------------------------------------------------------------------
# Pydantic data models
# ---------------------------------------------------------------------------


class KeyFrame(BaseModel):
    """A single extracted keyframe from a video."""

    frame_number: int = Field(..., description="Frame index in the video")
    timestamp: float = Field(..., description="Timestamp in seconds")
    file_path: str = Field(..., description="Absolute path to the saved frame image")
    width: int = Field(default=0, description="Frame width in pixels")
    height: int = Field(default=0, description="Frame height in pixels")
    is_scene_change: bool = Field(
        default=False,
        description="Whether this frame was selected because of a scene change",
    )


class VideoMetadata(BaseModel):
    """Technical metadata about a video file."""

    duration: float = Field(..., description="Total duration in seconds")
    width: int = Field(default=0, description="Video width in pixels")
    height: int = Field(default=0, description="Video height in pixels")
    fps: float = Field(default=0.0, description="Frames per second")
    codec: str = Field(default="unknown", description="Video codec name")
    audio_codec: str = Field(default="none", description="Audio codec (or 'none')")
    bitrate: int = Field(default=0, description="Bitrate in bps")
    num_frames: int = Field(default=0, description="Total number of frames")
    has_audio: bool = Field(default=False)
    file_size: int = Field(default=0, description="File size in bytes")


class VideoResult(BaseModel):
    """Complete result of processing a video file."""

    file_path: str = Field(..., description="Original video file path")
    metadata: VideoMetadata = Field(..., description="Video technical metadata")
    audio_path: Optional[str] = Field(
        default=None,
        description="Path to extracted audio file (if any)",
    )
    keyframes: List[KeyFrame] = Field(
        default_factory=list,
        description="Extracted keyframe images",
    )
    transcription_text: Optional[str] = Field(
        default=None,
        description="Transcribed speech from video audio track",
    )
    transcription_language: Optional[str] = Field(default=None)
    processing_time: float = Field(
        default=0.0,
        description="Total wall-clock processing time in seconds",
    )


# ---------------------------------------------------------------------------
# FFmpeg helpers
# ---------------------------------------------------------------------------

def _ffmpeg_exists() -> bool:
    """Check whether the ``ffmpeg`` binary is available on ``PATH``."""
    return shutil.which("ffmpeg") is not None


def _run_ffmpeg(args: List[str], timeout: int = 600) -> subprocess.CompletedProcess:
    """
    Execute an FFmpeg command with error handling.

    Args:
        args: Full argument list (including ``ffmpeg`` as argv[0]).
        timeout: Maximum execution time in seconds.

    Returns:
        CompletedProcess instance.

    Raises:
        FileNotFoundError: If ffmpeg is not installed.
        subprocess.TimeoutExpired: If the process exceeds *timeout*.
        RuntimeError: If ffmpeg exits with a non-zero code.
    """
    if not _ffmpeg_exists():
        raise FileNotFoundError(
            "ffmpeg not found on PATH. Install it via your package manager "
            "(e.g. ``apt install ffmpeg`` or ``brew install ffmpeg``)."
        )
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.error("ffmpeg stderr: %s", result.stderr.strip())
            raise RuntimeError(
                f"ffmpeg failed (code {result.returncode}): {result.stderr.strip()}"
            )
        return result
    except subprocess.TimeoutExpired:
        raise


def _probe_video(file_path: str) -> Dict:
    """
    Use ``ffprobe`` to extract video metadata.

    Returns a dict with duration, width, height, fps, codec, audio_codec,
    bitrate, num_frames, has_audio, file_size.
    """
    if not shutil.which("ffprobe"):
        logger.warning("ffprobe not found – returning default metadata")
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        return {
            "duration": 0.0,
            "width": 0,
            "height": 0,
            "fps": 0.0,
            "codec": "unknown",
            "audio_codec": "unknown",
            "bitrate": 0,
            "num_frames": 0,
            "has_audio": False,
            "file_size": file_size,
        }

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        import json
        probe = json.loads(result.stdout)
    except Exception as exc:
        logger.warning("ffprobe failed: %s – returning default metadata", exc)
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        return {
            "duration": 0.0,
            "width": 0,
            "height": 0,
            "fps": 0.0,
            "codec": "unknown",
            "audio_codec": "unknown",
            "bitrate": 0,
            "num_frames": 0,
            "has_audio": False,
            "file_size": file_size,
        }

    info: Dict = {"has_audio": False, "file_size": 0}
    fmt = probe.get("format", {})
    info["duration"] = float(fmt.get("duration", 0))
    info["bitrate"] = int(fmt.get("bit_rate", 0))
    info["file_size"] = int(fmt.get("size", 0))

    for stream in probe.get("streams", []):
        codec_type = stream.get("codec_type", "")
        if codec_type == "video":
            info["width"] = int(stream.get("width", 0))
            info["height"] = int(stream.get("height", 0))
            info["codec"] = stream.get("codec_name", "unknown")
            # FPS from r_frame_rate (e.g. "30/1")
            rfr = stream.get("r_frame_rate", "0/1")
            try:
                num, den = rfr.split("/")
                info["fps"] = float(num) / float(den) if float(den) else 0.0
            except (ValueError, ZeroDivisionError):
                info["fps"] = 0.0
            nb_frames = stream.get("nb_frames")
            if nb_frames:
                info["num_frames"] = int(nb_frames)
        elif codec_type == "audio":
            info["has_audio"] = True
            info["audio_codec"] = stream.get("codec_name", "unknown")

    return info


# ---------------------------------------------------------------------------
# VideoProcessor
# ---------------------------------------------------------------------------


class VideoProcessor:
    """
    Process video files for medical multimedia workflows.

    Capabilities:
        - Extract audio tracks (WAV) for transcription
        - Extract key frames at regular intervals or via scene detection
        - Transcribe video speech via the :class:`AudioProcessor`
        - Gather full technical metadata via ``ffprobe``
    """

    def __init__(
        self,
        output_dir: Optional[str] = None,
        default_frame_interval: float = 5.0,
    ) -> None:
        """
        Args:
            output_dir: Directory for extracted audio and keyframes.
                Defaults to ``settings.UPLOAD_DIR / "video_output"``.
            default_frame_interval: Default interval in seconds between keyframes.
        """
        self.output_dir = Path(output_dir or os.path.join(settings.UPLOAD_DIR, "video_output"))
        self.default_frame_interval = default_frame_interval
        self.output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "VideoProcessor initialised  output_dir=%s  frame_interval=%.1fs",
            self.output_dir,
            self.default_frame_interval,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_video(self, file_path: Union[str, Path]) -> VideoResult:
        """
        Fully process a video file: extract audio, keyframes, and transcribe.

        Args:
            file_path: Path to the video file.

        Returns:
            A :class:`VideoResult` with all extracted data.
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")

        t0 = time.time()
        logger.info("Processing video: %s", file_path.name)

        # 1. Metadata
        meta_raw = _probe_video(str(file_path))
        metadata = VideoMetadata(**meta_raw)

        result = VideoResult(
            file_path=str(file_path.resolve()),
            metadata=metadata,
        )

        # 2. Extract audio
        try:
            audio_path = self.extract_audio(str(file_path))
            result.audio_path = audio_path
            logger.info("Audio extracted to: %s", audio_path)
        except Exception as exc:
            logger.warning("Audio extraction failed: %s", exc)

        # 3. Extract keyframes
        try:
            keyframes = self.extract_keyframes(
                str(file_path),
                interval=self.default_frame_interval,
            )
            result.keyframes = keyframes
            logger.info("Extracted %d keyframes", len(keyframes))
        except Exception as exc:
            logger.warning("Keyframe extraction failed: %s", exc)

        # 4. Transcribe (if audio was extracted)
        if result.audio_path:
            try:
                transcription = self.transcribe_video(str(file_path))
                result.transcription_text = transcription.text
                result.transcription_language = transcription.language
            except Exception as exc:
                logger.warning("Video transcription failed: %s", exc)

        result.processing_time = round(time.time() - t0, 2)
        logger.info(
            "Video processing complete in %.2fs  (keyframes=%d, transcription=%s)",
            result.processing_time,
            len(result.keyframes),
            "yes" if result.transcription_text else "no",
        )
        return result

    def extract_audio(self, file_path: Union[str, Path]) -> str:
        """
        Extract the audio track from a video file and save as WAV.

        Args:
            file_path: Path to the video file.

        Returns:
            Absolute path to the extracted WAV file.

        Raises:
            FileNotFoundError: If ffmpeg is not installed or the file doesn't exist.
            RuntimeError: If the video has no audio track.
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")

        # Quick check for audio stream
        probe = _probe_video(str(file_path))
        if not probe.get("has_audio"):
            raise RuntimeError(f"Video '{file_path.name}' has no audio track")

        out_name = f"{file_path.stem}_audio.wav"
        out_path = str(self.output_dir / out_name)

        cmd = [
            "ffmpeg",
            "-i", str(file_path),
            "-vn",                   # no video
            "-acodec", "pcm_s16le",  # WAV 16-bit PCM
            "-ar", "16000",           # 16 kHz sample rate (ideal for Whisper)
            "-ac", "1",               # mono
            "-y",                     # overwrite
            out_path,
        ]
        _run_ffmpeg(cmd)

        logger.info("Audio extracted: %s (%.1f KB)", out_path, os.path.getsize(out_path) / 1024)
        return os.path.abspath(out_path)

    def extract_keyframes(
        self,
        file_path: Union[str, Path],
        interval: float = 5.0,
        max_frames: int = 200,
        use_scene_detect: bool = False,
    ) -> List[KeyFrame]:
        """
        Extract key frames from a video file.

        Args:
            file_path: Path to the video file.
            interval: Time in seconds between frames (used when *use_scene_detect*
                is ``False``).
            max_frames: Upper limit on the number of frames to extract.
            use_scene_detect: If ``True``, use OpenCV scene-change detection instead
                of fixed-interval extraction.

        Returns:
            List of :class:`KeyFrame` objects with saved image paths.
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")

        frames_dir = self.output_dir / f"{file_path.stem}_keyframes"
        frames_dir.mkdir(parents=True, exist_ok=True)

        if use_scene_detect and HAS_CV2:
            return self._extract_by_scene_change(str(file_path), frames_dir, max_frames)
        else:
            return self._extract_by_interval(str(file_path), frames_dir, interval, max_frames)

    def transcribe_video(self, file_path: Union[str, Path]) -> "TranscriptionResult":
        """
        Transcribe the speech in a video's audio track.

        Extracts audio first (if not already cached), then delegates to
        :class:`AudioProcessor`.

        Args:
            file_path: Path to the video file.

        Returns:
            A :class:`TranscriptionResult` from the audio transcription.
        """
        from app.media.audio_processor import get_audio_processor

        file_path = Path(file_path)

        # Extract audio to a temp location or reuse cached version
        audio_path = self.output_dir / f"{file_path.stem}_audio.wav"
        if not audio_path.exists():
            audio_path = Path(self.extract_audio(str(file_path)))
        else:
            audio_path = audio_path

        processor = get_audio_processor()
        result = processor.transcribe_audio(str(audio_path))
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_by_interval(
        self,
        file_path: str,
        frames_dir: Path,
        interval: float,
        max_frames: int,
    ) -> List[KeyFrame]:
        """Extract frames at fixed time intervals using FFmpeg."""
        keyframes: List[KeyFrame] = []
        frame_idx = 0

        out_pattern = str(frames_dir / "frame_%04d.jpg")

        cmd = [
            "ffmpeg",
            "-i", file_path,
            "-vf", f"fps=1/{interval}",
            "-q:v", "2",       # high quality JPEG
            "-frames:v", str(max_frames),
            "-y",
            out_pattern,
        ]
        _run_ffmpeg(cmd)

        for img_file in sorted(frames_dir.glob("frame_*.jpg")):
            w, h = 0, 0
            if HAS_CV2:
                img = cv2.imread(str(img_file))
                if img is not None:
                    h, w = img.shape[:2]

            # Derive timestamp from frame index
            ts = frame_idx * interval

            keyframes.append(
                KeyFrame(
                    frame_number=frame_idx,
                    timestamp=round(ts, 3),
                    file_path=str(img_file.resolve()),
                    width=w,
                    height=h,
                    is_scene_change=False,
                )
            )
            frame_idx += 1

        logger.info("Interval extraction: %d frames at %.1fs interval", len(keyframes), interval)
        return keyframes

    def _extract_by_scene_change(
        self,
        file_path: str,
        frames_dir: Path,
        max_frames: int,
    ) -> List[KeyFrame]:
        """Extract frames using OpenCV scene-change detection."""
        if not HAS_CV2:
            raise RuntimeError("opencv-python is required for scene-change detection")

        cap = cv2.VideoCapture(file_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {file_path}")

        keyframes: List[KeyFrame] = []
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        prev_frame = None
        frame_number = 0
        scene_threshold = 30.0  # Mean absolute difference threshold

        try:
            while len(keyframes) < max_frames:
                ret, frame = cap.read()
                if not ret:
                    break

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray = cv2.GaussianBlur(gray, (21, 21), 0)

                is_scene = False
                if prev_frame is not None:
                    diff = cv2.absdiff(prev_frame, gray)
                    mean_diff = diff.mean()
                    is_scene = mean_diff > scene_threshold

                if is_scene:
                    ts = frame_number / fps
                    out_path = frames_dir / f"scene_{frame_number:06d}.jpg"
                    cv2.imwrite(str(out_path), frame)

                    keyframes.append(
                        KeyFrame(
                            frame_number=frame_number,
                            timestamp=round(ts, 3),
                            file_path=str(out_path.resolve()),
                            width=width,
                            height=height,
                            is_scene_change=True,
                        )
                    )

                prev_frame = gray
                frame_number += 1

        finally:
            cap.release()

        logger.info("Scene-change detection: %d keyframes from %d total frames", len(keyframes), frame_number)
        return keyframes


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_video_processor: Optional[VideoProcessor] = None


def get_video_processor(
    output_dir: Optional[str] = None,
    default_frame_interval: float = 5.0,
) -> VideoProcessor:
    """Get or create the shared :class:`VideoProcessor` singleton."""
    global _video_processor
    if _video_processor is None:
        _video_processor = VideoProcessor(
            output_dir=output_dir,
            default_frame_interval=default_frame_interval,
        )
    return _video_processor
