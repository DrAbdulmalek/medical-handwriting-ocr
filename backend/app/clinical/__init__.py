"""
Clinical Decision Support & Supporting Infrastructure.

This package provides production-quality modules for clinical decision support,
including guideline tracking, clinical question answering, result aggregation,
and real-time processing progress tracking.  All modules support Arabic text
processing and follow the project's established patterns.

Modules:
    guideline_tracker  - Medical guideline monitoring and version tracking.
    clinical_qa        - Evidence-based clinical question answering with
                         drug interaction checking and differential diagnosis.
    result_aggregator  - Multi-engine result merging, deduplication, and
                         conflict resolution.
    progress_tracker   - Real-time task progress tracking with WebSocket
                         support and cancellation.
"""

from app.clinical.guideline_tracker import (
    GuidelineTracker,
    Guideline,
    GuidelineUpdate,
    GuidelineSource,
    VersionDiff,
    Subscription,
    StoredGuideline,
)
from app.clinical.clinical_qa import (
    ClinicalQA,
    ClinicalAnswer,
    Evidence,
    InteractionReport,
    Contraindication,
    DifferentialDiagnosis,
    TreatmentProtocol,
    DosageValidation,
)
from app.clinical.result_aggregator import (
    ResultAggregator,
    AggregatedResult,
    MergedExtraction,
    ConflictingItem,
    ResolvedItem,
    UnifiedReport,
    ProcessingResult,
)
from app.clinical.progress_tracker import (
    ProgressTracker,
    ProgressStatus,
    ProgressStage,
    SessionInfo,
)

__all__ = [
    # -- Guideline Tracker --
    "GuidelineTracker",
    "Guideline",
    "GuidelineUpdate",
    "GuidelineSource",
    "VersionDiff",
    "Subscription",
    "StoredGuideline",
    # -- Clinical QA --
    "ClinicalQA",
    "ClinicalAnswer",
    "Evidence",
    "InteractionReport",
    "Contraindication",
    "DifferentialDiagnosis",
    "TreatmentProtocol",
    "DosageValidation",
    # -- Result Aggregator --
    "ResultAggregator",
    "AggregatedResult",
    "MergedExtraction",
    "ConflictingItem",
    "ResolvedItem",
    "UnifiedReport",
    "ProcessingResult",
    # -- Progress Tracker --
    "ProgressTracker",
    "ProgressStatus",
    "ProgressStage",
    "SessionInfo",
]
