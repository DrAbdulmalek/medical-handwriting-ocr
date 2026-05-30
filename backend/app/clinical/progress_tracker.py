"""
Real-Time Processing Progress Tracker.

Provides the ``ProgressTracker`` class for tracking long-running processing
tasks such as batch OCR, RAG indexing, and document processing pipelines.
Uses Redis pub/sub for real-time progress distribution and supports
WebSocket-compatible progress streaming, stage-level tracking with sub-steps,
estimated time remaining, and cancellation.

Supports Arabic status messages for bilingual user interfaces.
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncGenerator, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic data models
# ---------------------------------------------------------------------------


class TaskType(str, Enum):
    """Well-known long-running task types."""

    BATCH_OCR = "batch_ocr"
    RAG_INDEXING = "rag_indexing"
    DOCUMENT_PROCESSING = "document_processing"
    MODEL_TRAINING = "model_training"
    BATCH_CORRECTION = "batch_correction"
    DICOM_IMPORT = "dicom_import"
    DATA_EXPORT = "data_export"
    RETENTION_CLEANUP = "retention_cleanup"


class SessionStatus(str, Enum):
    """Current status of a progress-tracking session."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProgressStage(BaseModel):
    """A processing stage with sub-step granularity."""

    stage_number: int = Field(..., description="1-based stage index")
    stage_name: str
    stage_name_ar: Optional[str] = Field(default=None, description="Arabic stage name")
    total_substeps: int = Field(default=1, ge=1)
    completed_substeps: int = Field(default=0, ge=0)
    message: Optional[str] = None
    message_ar: Optional[str] = Field(default=None, description="Arabic message")
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @property
    def is_complete(self) -> bool:
        """Return ``True`` when all sub-steps are done."""
        return self.completed_substeps >= self.total_substeps

    @property
    def stage_progress(self) -> float:
        """Return progress within this stage as a 0.0–1.0 fraction."""
        if self.total_substeps == 0:
            return 1.0
        return min(self.completed_substeps / self.total_substeps, 1.0)


class SessionInfo(BaseModel):
    """Metadata about a progress-tracking session."""

    session_id: str
    task_type: TaskType
    total_items: int = Field(ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: Optional[str] = Field(default=None, description="User or system initiating the session")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ProgressStatus(BaseModel):
    """Complete snapshot of a session's current progress."""

    session_id: str
    task_type: TaskType
    status: SessionStatus = SessionStatus.PENDING
    current_item: int = Field(default=0, ge=0, description="Number of items processed so far")
    total_items: int = Field(default=0, ge=0)
    progress: float = Field(default=0.0, ge=0.0, le=1.0, description="Overall progress 0.0–1.0")
    percentage: float = Field(default=0.0, ge=0.0, le=100.0, description="Overall progress as percentage")
    message: Optional[str] = None
    message_ar: Optional[str] = Field(default=None, description="Arabic message")
    current_stage: Optional[ProgressStage] = None
    completed_stages: List[ProgressStage] = Field(default_factory=list)
    total_stages: int = Field(default=1, ge=1)
    estimated_remaining_seconds: Optional[float] = Field(
        default=None,
        description="Estimated seconds until completion (None if cannot estimate)",
    )
    items_per_second: Optional[float] = Field(default=None, description="Processing throughput")
    error: Optional[str] = None
    error_ar: Optional[str] = Field(default=None, description="Arabic error message")
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# ProgressTracker
# ---------------------------------------------------------------------------


class ProgressTracker:
    """Real-time progress tracking for long-running processing tasks.

    Uses an in-memory store with optional Redis pub/sub for distributed
    progress broadcasting.  Designed to work with WebSocket endpoints for
    real-time progress updates in the frontend.

    Features:

    * Session-based tracking with unique IDs.
    * Stage-level granularity with sub-steps.
    * Estimated time remaining based on processing throughput.
    * Cancellation support.
    * WebSocket-compatible async generator for streaming updates.
    * Arabic-localised status messages.

    Usage::

        tracker = ProgressTracker()

        # Create a session
        session_id = await tracker.create_session(
            task_type=TaskType.BATCH_OCR,
            total_items=100,
        )

        # Update progress (called from worker / Celery task)
        for i in range(100):
            await tracker.update_progress(
                session_id,
                current=i + 1,
                message=f"Processing document {i+1}/100",
                message_ar=f"معالجة المستند {i+1}/100",
            )

        # Complete the session
        status = await tracker.complete_session(session_id)

        # Stream progress via WebSocket
        async for progress in tracker.subscribe_progress(session_id):
            await websocket.send_json(progress.model_dump())

        # Cancel
        await tracker.cancel_session(session_id)
    """

    def __init__(self, use_redis: bool = False) -> None:
        """Initialise the progress tracker.

        Args:
            use_redis: If ``True``, use Redis pub/sub for progress
                       broadcasting across workers.  Falls back to
                       in-memory broadcasting if Redis is unavailable.
        """
        self._sessions: Dict[str, ProgressStatus] = {}
        self._subscribers: Dict[str, List[asyncio.Queue]] = defaultdict(list)
        self._use_redis = use_redis
        self._redis_client = None
        self._redis_pubsub = None
        self._lock = asyncio.Lock()

        if use_redis:
            self._init_redis()

        logger.info(
            "ProgressTracker initialised – redis=%s", "enabled" if use_redis else "disabled"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_session(
        self,
        task_type: TaskType,
        total_items: int,
        total_stages: int = 1,
        metadata: Optional[Dict[str, Any]] = None,
        created_by: Optional[str] = None,
    ) -> str:
        """Create a new progress-tracking session.

        Args:
            task_type: The type of long-running task.
            total_items: Expected total number of items to process.
            total_stages: Number of processing stages (default 1).
            metadata: Optional arbitrary metadata attached to the session.
            created_by: Identifier of the user or system creating the session.

        Returns:
            The unique session ID string.
        """
        session_id = str(uuid4())

        now = datetime.now(timezone.utc)
        session = ProgressStatus(
            session_id=session_id,
            task_type=task_type,
            status=SessionStatus.RUNNING,
            current_item=0,
            total_items=max(total_items, 0),
            total_stages=max(total_stages, 1),
            progress=0.0,
            percentage=0.0,
            message="Processing started.",
            message_ar="بدأت المعالجة.",
            started_at=now,
            metadata=metadata or {},
        )

        async with self._lock:
            self._sessions[session_id] = session

        logger.info(
            "Session created – id=%s, type=%s, items=%d, stages=%d",
            session_id,
            task_type.value,
            total_items,
            total_stages,
        )

        await self._broadcast(session_id)
        return session_id

    async def update_progress(
        self,
        session_id: str,
        current: int,
        message: Optional[str] = None,
        message_ar: Optional[str] = None,
    ) -> None:
        """Update progress for an active session.

        Args:
            session_id: The session to update.
            current: Current number of items processed.
            message: Optional English status message.
            message_ar: Optional Arabic status message.
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                logger.warning("update_progress – session %s not found", session_id)
                return

            if session.status != SessionStatus.RUNNING:
                logger.warning(
                    "update_progress – session %s is not running (status=%s)",
                    session_id,
                    session.status.value,
                )
                return

            # Check for cancellation
            if session.status == SessionStatus.CANCELLED:
                return

            session.current_item = min(current, session.total_items)

            # Compute progress
            if session.total_items > 0:
                session.progress = session.current_item / session.total_items
                session.percentage = session.progress * 100.0

            # Update stage progress
            if session.current_stage:
                session.current_stage.completed_substeps = session.current_item
                session.current_stage.message = message

            # Update messages
            if message is not None:
                session.message = message
            if message_ar is not None:
                session.message_ar = message_ar

            # Compute throughput and ETA
            self._compute_eta(session)

        logger.debug(
            "update_progress – session=%s, current=%d/%d (%.1f%%)",
            session_id,
            session.current_item,
            session.total_items,
            session.percentage,
        )

        await self._broadcast(session_id)

    async def set_stage(
        self,
        session_id: str,
        stage_number: int,
        stage_name: str,
        stage_name_ar: Optional[str] = None,
        total_substeps: int = 1,
    ) -> None:
        """Set or advance the current processing stage.

        Automatically marks previously active stage as complete.

        Args:
            session_id: The session to update.
            stage_number: 1-based stage index.
            stage_name: English stage name.
            stage_name_ar: Arabic stage name.
            total_substeps: Expected sub-steps in this stage.
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                logger.warning("set_stage – session %s not found", session_id)
                return

            # Archive previous stage if any
            if session.current_stage and not session.current_stage.is_complete:
                session.current_stage.completed_at = datetime.now(timezone.utc)
                session.completed_stages.append(session.current_stage)

            # Create new stage
            new_stage = ProgressStage(
                stage_number=stage_number,
                stage_name=stage_name,
                stage_name_ar=stage_name_ar,
                total_substeps=total_substeps,
                completed_substeps=0,
                started_at=datetime.now(timezone.utc),
            )
            session.current_stage = new_stage

        logger.info(
            "set_stage – session=%s, stage=%d/%d: %s",
            session_id,
            stage_number,
            session.total_stages,
            stage_name,
        )

        await self._broadcast(session_id)

    async def get_progress(self, session_id: str) -> Optional[ProgressStatus]:
        """Get the current progress status for a session.

        Args:
            session_id: The session to query.

        Returns:
            A :class:`ProgressStatus` snapshot, or ``None`` if the session
            does not exist.
        """
        async with self._lock:
            session = self._sessions.get(session_id)

        if session is None:
            logger.debug("get_progress – session %s not found", session_id)
            return None

        # Return a copy to prevent external mutation
        return session.model_copy(deep=True)

    async def complete_session(self, session_id: str) -> ProgressStatus:
        """Mark a session as successfully completed.

        Args:
            session_id: The session to complete.

        Returns:
            The final :class:`ProgressStatus`.

        Raises:
            ValueError: If the session does not exist.
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise ValueError(f"Session {session_id} not found")

            session.status = SessionStatus.COMPLETED
            session.progress = 1.0
            session.percentage = 100.0
            session.current_item = session.total_items
            session.completed_at = datetime.now(timezone.utc)
            session.message = "Processing completed successfully."
            session.message_ar = "تمت المعالجة بنجاح."

            # Complete current stage
            if session.current_stage and not session.current_stage.is_complete:
                session.current_stage.completed_substeps = session.current_stage.total_substeps
                session.current_stage.completed_at = session.completed_at
                session.completed_stages.append(session.current_stage)
                session.current_stage = None

            # Final ETA update
            self._compute_eta(session)
            session.estimated_remaining_seconds = 0.0

        logger.info(
            "Session completed – id=%s, items=%d, duration=%.1fs",
            session_id,
            session.current_item,
            (session.completed_at - session.started_at).total_seconds() if session.started_at else 0,
        )

        await self._broadcast(session_id)
        return session.model_copy(deep=True)

    async def fail_session(self, session_id: str, error: str) -> ProgressStatus:
        """Mark a session as failed.

        Args:
            session_id: The session to fail.
            error: Error message describing the failure.

        Returns:
            The final :class:`ProgressStatus`.

        Raises:
            ValueError: If the session does not exist.
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise ValueError(f"Session {session_id} not found")

            session.status = SessionStatus.FAILED
            session.error = error
            session.message = f"Processing failed: {error}"
            session.message_ar = f"فشلت المعالجة: {error}"
            session.completed_at = datetime.now(timezone.utc)

        logger.error(
            "Session failed – id=%s, error=%s", session_id, error
        )

        await self._broadcast(session_id)
        return session.model_copy(deep=True)

    async def cancel_session(self, session_id: str) -> bool:
        """Cancel an active session.

        The session will be marked as cancelled and any future
        ``update_progress`` calls will be no-ops.

        Args:
            session_id: The session to cancel.

        Returns:
            ``True`` if the session was cancelled, ``False`` if it was
            not found or already terminal.
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                logger.warning("cancel_session – session %s not found", session_id)
                return False

            if session.status not in (SessionStatus.RUNNING, SessionStatus.PENDING):
                logger.warning(
                    "cancel_session – session %s already in state %s",
                    session_id,
                    session.status.value,
                )
                return False

            session.status = SessionStatus.CANCELLED
            session.cancelled_at = datetime.now(timezone.utc)
            session.message = "Processing cancelled by user."
            session.message_ar = "تم إلغاء المعالجة بواسطة المستخدم."

        logger.info("Session cancelled – id=%s", session_id)

        await self._broadcast(session_id)
        return True

    async def subscribe_progress(
        self,
        session_id: str,
        poll_interval: float = 0.5,
    ) -> AsyncGenerator[ProgressStatus, None]:
        """Subscribe to progress updates as an async generator.

        This is designed to be used directly in a WebSocket endpoint::

            @router.websocket("/progress/{session_id}")
            async def progress_ws(websocket: WebSocket, session_id: str):
                await websocket.accept()
                async for status in tracker.subscribe_progress(session_id):
                    await websocket.send_json(status.model_dump(mode="json"))
                    if status.status in (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED):
                        break

        Args:
            session_id: The session to subscribe to.
            poll_interval: Seconds between status checks when not using
                           pub/sub events.

        Yields:
            :class:`ProgressStatus` snapshots.
        """
        logger.info("subscribe_progress – session=%s, new subscriber", session_id)

        # Send initial status
        initial = await self.get_progress(session_id)
        if initial is None:
            logger.warning("subscribe_progress – session %s not found, yielding nothing", session_id)
            return
        yield initial

        # Check if session is already terminal
        if initial.status in (
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.CANCELLED,
        ):
            return

        # Create a queue for this subscriber
        queue: asyncio.Queue[ProgressStatus] = asyncio.Queue()
        async with self._lock:
            self._subscribers[session_id].append(queue)

        try:
            while True:
                try:
                    # Wait for update with timeout
                    status = await asyncio.wait_for(queue.get(), timeout=poll_interval)
                    yield status

                    # Stop on terminal status
                    if status.status in (
                        SessionStatus.COMPLETED,
                        SessionStatus.FAILED,
                        SessionStatus.CANCELLED,
                    ):
                        break

                except asyncio.TimeoutError:
                    # Timeout – send a heartbeat / current status
                    current = await self.get_progress(session_id)
                    if current is None:
                        break
                    yield current

                    if current.status in (
                        SessionStatus.COMPLETED,
                        SessionStatus.FAILED,
                        SessionStatus.CANCELLED,
                    ):
                        break

        finally:
            # Clean up subscriber
            async with self._lock:
                queues = self._subscribers.get(session_id, [])
                if queue in queues:
                    queues.remove(queue)
            logger.info("subscribe_progress – session=%s, subscriber removed", session_id)

    async def get_all_active_sessions(self) -> List[ProgressStatus]:
        """Get all currently active (running) sessions.

        Returns:
            A list of :class:`ProgressStatus` for sessions that are not
            in a terminal state.
        """
        async with self._lock:
            active = [
                s.model_copy(deep=True)
                for s in self._sessions.values()
                if s.status in (SessionStatus.RUNNING, SessionStatus.PENDING)
            ]
        logger.info("get_all_active_sessions – %d active sessions", len(active))
        return active

    async def remove_session(self, session_id: str) -> bool:
        """Remove a completed or failed session from the tracker.

        Terminal sessions should be removed periodically to prevent
        memory leaks.

        Args:
            session_id: The session to remove.

        Returns:
            ``True`` if the session was removed.
        """
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                # Notify subscribers to stop
                for queue in self._subscribers.pop(session_id, []):
                    try:
                        queue.put_nowait(session.model_copy(deep=True))
                    except asyncio.QueueFull:
                        pass
                return True
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _broadcast(self, session_id: str) -> None:
        """Broadcast current progress to all subscribers of a session."""
        async with self._lock:
            session = self._sessions.get(session_id)
            queues = list(self._subscribers.get(session_id, []))

        if not session:
            return

        status_copy = session.model_copy(deep=True)

        for queue in queues:
            try:
                queue.put_nowait(status_copy)
            except asyncio.QueueFull:
                # Drop the update if the subscriber is slow
                logger.warning(
                    "_broadcast – queue full for session %s subscriber (dropped)",
                    session_id,
                )

        # Redis broadcast (if enabled)
        if self._redis_client and self._use_redis:
            try:
                channel = f"progress:{session_id}"
                payload = status_copy.model_dump_json()
                await self._redis_client.publish(channel, payload)
            except Exception:
                logger.exception("_broadcast – failed to publish to Redis")

    @staticmethod
    def _compute_eta(session: ProgressStatus) -> None:
        """Compute estimated time remaining and items-per-second."""
        if not session.started_at or session.current_item == 0:
            session.estimated_remaining_seconds = None
            session.items_per_second = None
            return

        now = datetime.now(timezone.utc)
        elapsed = (now - session.started_at).total_seconds()

        if elapsed <= 0:
            session.estimated_remaining_seconds = None
            session.items_per_second = None
            return

        # Items per second
        session.items_per_second = session.current_item / elapsed

        # ETA
        remaining_items = session.total_items - session.current_item
        if remaining_items > 0 and session.items_per_second > 0:
            session.estimated_remaining_seconds = remaining_items / session.items_per_second
        else:
            session.estimated_remaining_seconds = 0.0

    def _init_redis(self) -> None:
        """Attempt to initialise the Redis client for pub/sub."""
        try:
            import redis.asyncio as aioredis
            self._redis_client = aioredis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
            )
            logger.info("ProgressTracker Redis client initialised (pub/sub enabled)")
        except ImportError:
            logger.warning(
                "redis package not installed – falling back to in-memory broadcasting"
            )
            self._use_redis = False
        except Exception:
            logger.exception("Failed to initialise Redis client – using in-memory broadcasting")
            self._use_redis = False

    async def close(self) -> None:
        """Clean up resources (Redis connections, etc.)."""
        if self._redis_client:
            try:
                await self._redis_client.close()
            except Exception:
                logger.exception("Error closing Redis client")
            logger.info("ProgressTracker Redis client closed")
