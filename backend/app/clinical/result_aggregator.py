"""
Result Aggregation and Merging Engine.

Provides the ``ResultAggregator`` class that merges processing results from
multiple engines (OCR, NLP, vision), deduplicates overlapping extractions,
resolves conflicts using confidence-weighted selection, and produces unified
patient data reports.

Supports Arabic text deduplication with diacritic-insensitive matching.
"""

import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from pydantic import BaseModel, Field

from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic data models
# ---------------------------------------------------------------------------


class ProcessingEngine(str, Enum):
    """Identifies the source processing engine."""

    OCR_PADDLE = "ocr_paddle"
    OCR_TROCR = "ocr_trocr"
    NLP_NER = "nlp_ner"
    NLP_RELATION = "nlp_relation"
    VISION_CLASSIFICATION = "vision_classification"
    VISION_LAYOUT = "vision_layout"
    MANUAL_CORRECTION = "manual_correction"


class ConflictResolutionStrategy(str, Enum):
    """Strategy for resolving conflicting extractions."""

    HIGHEST_CONFIDENCE = "highest_confidence"
    WEIGHTED_AVERAGE = "weighted_average"
    LATEST_TIMESTAMP = "latest_timestamp"
    MAJORITY_VOTE = "majority_vote"
    MANUAL_REVIEW = "manual_review"


class ProcessingResult(BaseModel):
    """A single result produced by a processing engine."""

    result_id: str = Field(default_factory=lambda: str(uuid4()))
    engine: ProcessingEngine
    source_id: Optional[str] = Field(default=None, description="Document or region ID")
    text: str = Field(..., description="Extracted text (supports Arabic)")
    text_ar: Optional[str] = Field(default=None, description="Normalised Arabic text")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    bounding_box: Optional[Dict[str, int]] = Field(
        default=None,
        description="{'x1':..,'y1':..,'x2':..,'y2':..}",
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    category: Optional[str] = Field(
        default=None,
        description="e.g. 'drug', 'dosage', 'diagnosis', 'patient_name'",
    )


class ConflictingItem(BaseModel):
    """Records an unresolved conflict between multiple extractions."""

    conflict_id: str = Field(default_factory=lambda: str(uuid4()))
    items: List[ProcessingResult] = Field(
        ...,
        description="The conflicting extraction results",
        min_length=2,
    )
    field: str = Field(
        ...,
        description="The field that conflicts, e.g. 'text', 'category'",
    )
    description: str = Field(default="")
    resolution_strategy: ConflictResolutionStrategy = (
        ConflictResolutionStrategy.HIGHEST_CONFIDENCE
    )


class ResolvedItem(BaseModel):
    """The result of resolving a conflict."""

    conflict_id: str
    selected_result: ProcessingResult
    rejected_results: List[ProcessingResult] = Field(default_factory=list)
    resolution_strategy: ConflictResolutionStrategy
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str = Field(default="")


class MergedExtraction(BaseModel):
    """An extraction that has been merged from one or more source results."""

    extraction_id: str = Field(default_factory=lambda: str(uuid4()))
    text: str = Field(..., description="Merged text (supports Arabic)")
    text_ar: Optional[str] = Field(default=None, description="Normalised Arabic text")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_results: List[str] = Field(
        default_factory=list,
        description="IDs of contributing ProcessingResult objects",
    )
    source_engines: List[ProcessingEngine] = Field(default_factory=list)
    bounding_box: Optional[Dict[str, int]] = None
    category: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    is_merged: bool = Field(default=False, description="True if >1 source contributed")
    is_conflict_resolved: bool = Field(default=False)


class AggregatedResult(BaseModel):
    """Complete aggregated result from multiple processing engines."""

    aggregation_id: str = Field(default_factory=lambda: str(uuid4()))
    source_id: Optional[str] = Field(default=None, description="Document or page ID")
    input_results: List[ProcessingResult] = Field(default_factory=list)
    merged_extractions: List[MergedExtraction] = Field(default_factory=list)
    deduplicated_count: int = Field(default=0, description="Items removed by deduplication")
    conflict_count: int = Field(default=0, description="Conflicts detected")
    resolved_count: int = Field(default=0, description="Conflicts resolved")
    engine_coverage: Dict[str, int] = Field(
        default_factory=dict,
        description="Count of results per engine",
    )
    average_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = Field(default=False, description="Requires human review")
    aggregated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UnifiedReport(BaseModel):
    """Unified patient data report generated from aggregated results."""

    report_id: str = Field(default_factory=lambda: str(uuid4()))
    source_document_id: Optional[str] = None
    patient_data: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Keyed patient data fields, e.g. {'medications': [...], 'diagnoses': [...]}",
    )
    medications: List[str] = Field(default_factory=list)
    diagnoses: List[str] = Field(default_factory=list)
    procedures: List[str] = Field(default_factory=list)
    vitals: Dict[str, str] = Field(default_factory=dict)
    patient_name: Optional[str] = None
    patient_name_ar: Optional[str] = None
    physician_name: Optional[str] = None
    physician_name_ar: Optional[str] = None
    date_of_birth: Optional[str] = None
    encounter_date: Optional[str] = None
    raw_extractions: List[MergedExtraction] = Field(default_factory=list)
    confidence_summary: Dict[str, float] = Field(
        default_factory=dict,
        description="Average confidence per category",
    )
    quality_flags: List[str] = Field(
        default_factory=list,
        description="Flags like 'low_confidence', 'conflict_detected'",
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# ResultAggregator
# ---------------------------------------------------------------------------


class ResultAggregator:
    """Merge, deduplicate, and resolve results from multiple processing engines.

    When the OCR pipeline, NLP extractors, and vision models all produce
    results for the same document, this class combines them into a single
    coherent dataset.  It handles:

    * Deduplication of overlapping extractions (including Arabic diacritic-
      insensitive matching).
    * Confidence-weighted conflict resolution.
    * Unified report generation for downstream clinical use.

    Usage::

        aggregator = ResultAggregator()

        # From multiple engines
        results = [ocr_result, nlp_result, vision_result]
        aggregated = aggregator.aggregate_results(results)

        # Merge two specific extractions
        merged = aggregator.merge_extractions(extraction1, extraction2)

        # Resolve conflicts
        resolved = aggregator.resolve_conflicts(conflicting_items)

        # Generate a unified patient report
        report = aggregator.generate_unified_report(aggregated)
    """

    def __init__(
        self,
        dedup_threshold: float = 0.85,
        conflict_threshold: float = 0.5,
    ) -> None:
        """Initialise the aggregator.

        Args:
            dedup_threshold: Minimum text similarity to consider two
                             extractions as duplicates (0.0–1.0).
            conflict_threshold: Maximum confidence difference below which
                                extractions are considered to conflict.
        """
        self._dedup_threshold = dedup_threshold
        self._conflict_threshold = conflict_threshold
        logger.info(
            "ResultAggregator initialised – dedup_threshold=%.2f, conflict_threshold=%.2f",
            dedup_threshold,
            conflict_threshold,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def aggregate_results(
        self,
        results_list: List[ProcessingResult],
    ) -> AggregatedResult:
        """Aggregate results from multiple processing engines.

        The pipeline:
        1. Normalise all texts (Arabic diacritic removal).
        2. Deduplicate overlapping extractions.
        3. Detect conflicts within same category/same region.
        4. Resolve conflicts using confidence-weighted selection.
        5. Produce final merged extractions with computed confidence.

        Args:
            results_list: List of :class:`ProcessingResult` from one or more
                          engines.

        Returns:
            An :class:`AggregatedResult` containing merged extractions,
            conflict summaries, and quality metrics.
        """
        logger.info(
            "aggregate_results – %d input results from engines: %s",
            len(results_list),
            self._summarise_engines(results_list),
        )

        if not results_list:
            return AggregatedResult()

        # Step 1: Normalise Arabic text
        normalised_results = self._normalise_results(results_list)

        # Step 2: Deduplicate
        unique_results, dedup_count = self.deduplicate(normalised_results)

        # Step 3: Detect conflicts
        conflicts = self._detect_conflicts(unique_results)

        # Step 4: Resolve conflicts
        resolved_items = self.resolve_conflicts(conflicts)

        # Step 5: Build merged extractions
        merged_extractions = self._build_merged_extractions(
            unique_results, resolved_items
        )

        # Compute metrics
        engine_coverage: Dict[str, int] = defaultdict(int)
        for r in results_list:
            engine_coverage[r.engine.value] += 1

        avg_confidence = (
            sum(e.confidence for e in merged_extractions) / len(merged_extractions)
            if merged_extractions
            else 0.0
        )

        needs_review = any(e.confidence < 0.5 for e in merged_extractions)

        aggregated = AggregatedResult(
            input_results=results_list,
            merged_extractions=merged_extractions,
            deduplicated_count=dedup_count,
            conflict_count=len(conflicts),
            resolved_count=len(resolved_items),
            engine_coverage=dict(engine_coverage),
            average_confidence=round(avg_confidence, 4),
            needs_review=needs_review,
        )

        logger.info(
            "aggregate_results complete – merged=%d, dedup=%d, conflicts=%d, resolved=%d",
            len(merged_extractions),
            dedup_count,
            len(conflicts),
            len(resolved_items),
        )
        return aggregated

    def merge_extractions(
        self,
        extraction1: ProcessingResult,
        extraction2: ProcessingResult,
    ) -> MergedExtraction:
        """Merge two processing results into a single extraction.

        If the texts differ significantly, the higher-confidence result is
        preferred.  If they are similar, a confidence-weighted merge is
        performed.

        Args:
            extraction1: First extraction result.
            extraction2: Second extraction result.

        Returns:
            A :class:`MergedExtraction` combining both sources.
        """
        logger.debug(
            "merge_extractions – %s (%.2f) + %s (%.2f)",
            extraction1.engine.value,
            extraction1.confidence,
            extraction2.engine.value,
            extraction2.confidence,
        )

        similarity = self._text_similarity(extraction1.text, extraction2.text)

        # Select the best text
        if similarity > 0.95:
            # Nearly identical – pick the higher-confidence one
            primary = extraction1 if extraction1.confidence >= extraction2.confidence else extraction2
            merged_text = primary.text
        elif similarity > 0.7:
            # Similar but not identical – keep higher confidence text
            primary = extraction1 if extraction1.confidence >= extraction2.confidence else extraction2
            merged_text = primary.text
        else:
            # Significantly different – keep higher confidence text
            primary = extraction1 if extraction1.confidence >= extraction2.confidence else extraction2
            merged_text = primary.text

        # Confidence-weighted combination
        total_conf = extraction1.confidence + extraction2.confidence
        if total_conf > 0:
            merged_confidence = (
                extraction1.confidence * extraction1.confidence
                + extraction2.confidence * extraction2.confidence
            ) / total_conf
        else:
            merged_confidence = max(extraction1.confidence, extraction2.confidence)

        # Combine bounding boxes (intersection/average)
        merged_bbox = self._merge_bounding_boxes(
            extraction1.bounding_box,
            extraction2.bounding_box,
        )

        # Combine metadata
        merged_metadata = {}
        merged_metadata.update(extraction1.metadata)
        merged_metadata.update(extraction2.metadata)
        merged_metadata["merge_sources"] = [extraction1.engine.value, extraction2.engine.value]

        result = MergedExtraction(
            text=merged_text,
            text_ar=self._normalise_arabic_text(merged_text),
            confidence=round(merged_confidence, 4),
            source_results=[extraction1.result_id, extraction2.result_id],
            source_engines=[extraction1.engine, extraction2.engine],
            bounding_box=merged_bbox,
            category=extraction1.category or extraction2.category,
            metadata=merged_metadata,
            is_merged=True,
        )

        logger.debug("merge_extractions – merged_text='%s', confidence=%.4f", merged_text, merged_confidence)
        return result

    def deduplicate(
        self,
        items: List[ProcessingResult],
    ) -> Tuple[List[ProcessingResult], int]:
        """Remove duplicate extractions from a list of results.

        Uses text similarity with Arabic diacritic-insensitive matching.
        Items with similarity above the threshold are considered duplicates,
        and the higher-confidence result is kept.

        Args:
            items: List of :class:`ProcessingResult` to deduplicate.

        Returns:
            A tuple of (deduplicated list, count of removed duplicates).
        """
        logger.info("deduplicate – %d input items", len(items))

        if not items:
            return items, 0

        # Sort by confidence descending so the best result is kept
        sorted_items = sorted(items, key=lambda r: r.confidence, reverse=True)

        kept: List[ProcessingResult] = []
        removed_count = 0

        for item in sorted_items:
            is_duplicate = False
            normalised_text = item.text_ar or self._normalise_arabic_text(item.text)

            for existing in kept:
                existing_text = existing.text_ar or self._normalise_arabic_text(existing.text)
                similarity = self._text_similarity(normalised_text, existing_text)

                if similarity >= self._dedup_threshold:
                    # Same category check – only dedup within same category
                    if item.category == existing.category or item.category is None or existing.category is None:
                        # Same source region check
                        if item.source_id == existing.source_id or item.source_id is None or existing.source_id is None:
                            is_duplicate = True
                            removed_count += 1
                            logger.debug(
                                "Dedup: removed '%s' (conf=%.2f) – duplicate of '%s' (conf=%.2f, sim=%.2f)",
                                item.text[:50],
                                item.confidence,
                                existing.text[:50],
                                existing.confidence,
                                similarity,
                            )
                            break

            if not is_duplicate:
                kept.append(item)

        logger.info("deduplicate – kept=%d, removed=%d", len(kept), removed_count)
        return kept, removed_count

    def resolve_conflicts(
        self,
        conflicting_items: List[ConflictingItem],
    ) -> List[ResolvedItem]:
        """Resolve a list of conflicting extraction items.

        Resolution strategy defaults to *highest_confidence* but can be
        overridden per conflict.

        Args:
            conflicting_items: List of :class:`ConflictingItem` objects.

        Returns:
            A list of :class:`ResolvedItem` objects with selected results.
        """
        logger.info("resolve_conflicts – %d conflicts to resolve", len(conflicting_items))

        resolved: List[ResolvedItem] = []

        for conflict in conflicting_items:
            strategy = conflict.resolution_strategy

            try:
                if strategy == ConflictResolutionStrategy.HIGHEST_CONFIDENCE:
                    selected = self._resolve_by_confidence(conflict)
                elif strategy == ConflictResolutionStrategy.LATEST_TIMESTAMP:
                    selected = self._resolve_by_timestamp(conflict)
                elif strategy == ConflictResolutionStrategy.MAJORITY_VOTE:
                    selected = self._resolve_by_majority(conflict)
                else:
                    # Default fallback
                    selected = self._resolve_by_confidence(conflict)

                resolved.append(selected)
                logger.debug(
                    "Resolved conflict %s using %s – selected='%s'",
                    conflict.conflict_id,
                    strategy.value,
                    selected.selected_result.text[:50],
                )
            except Exception:
                logger.exception(
                    "Failed to resolve conflict %s", conflict.conflict_id
                )
                # Fallback: pick first item
                selected = ResolvedItem(
                    conflict_id=conflict.conflict_id,
                    selected_result=conflict.items[0],
                    rejected_results=conflict.items[1:],
                    resolution_strategy=strategy,
                    confidence=conflict.items[0].confidence,
                    notes="Resolved by fallback (error in primary strategy).",
                )
                resolved.append(selected)

        logger.info("resolve_conflicts – resolved=%d", len(resolved))
        return resolved

    def generate_unified_report(
        self,
        aggregated: AggregatedResult,
    ) -> UnifiedReport:
        """Generate a unified patient data report from aggregated results.

        Extracts and categorises patient information (medications, diagnoses,
        procedures, vitals) from the aggregated extractions.

        Args:
            aggregated: An :class:`AggregatedResult` from :meth:`aggregate_results`.

        Returns:
            A :class:`UnifiedReport` with structured patient data.
        """
        logger.info(
            "generate_unified_report – %d extractions",
            len(aggregated.merged_extractions),
        )

        report = UnifiedReport(
            source_document_id=aggregated.source_id,
            raw_extractions=aggregated.merged_extractions,
        )

        # Classify extractions into patient data categories
        patient_data: Dict[str, List[str]] = defaultdict(list)
        confidence_by_category: Dict[str, List[float]] = defaultdict(list)
        quality_flags: List[str] = []

        for extraction in aggregated.merged_extractions:
            text = extraction.text.strip()
            if not text:
                continue

            category = self._classify_extraction(text, extraction.category)

            # Check for Arabic medical terms
            if self._is_arabic_text(text):
                category = self._classify_arabic_extraction(text) or category

            if category:
                patient_data[category].append(text)
                confidence_by_category[category].append(extraction.confidence)

            # Quality flags
            if extraction.confidence < 0.5:
                quality_flags.append("low_confidence")
            if extraction.is_conflict_resolved:
                quality_flags.append("conflict_resolved")

        # Populate structured fields
        report.medications = patient_data.get("medication", [])
        report.diagnoses = patient_data.get("diagnosis", [])
        report.procedures = patient_data.get("procedure", [])

        # Extract patient name
        names = patient_data.get("patient_name", [])
        if names:
            for name in names:
                if self._is_arabic_text(name):
                    report.patient_name_ar = name
                else:
                    report.patient_name = name

        # Extract physician name
        physicians = patient_data.get("physician_name", [])
        if physicians:
            for name in physicians:
                if self._is_arabic_text(name):
                    report.physician_name_ar = name
                else:
                    report.physician_name = name

        # Extract dates
        dates = patient_data.get("date", [])
        for date_str in dates:
            if not report.date_of_birth and self._looks_like_dob(date_str):
                report.date_of_birth = date_str
            elif not report.encounter_date:
                report.encounter_date = date_str

        # Vitals
        vitals_data = patient_data.get("vitals", [])
        for v in vitals_data:
            vitals = self._parse_vitals(v)
            report.vitals.update(vitals)

        report.patient_data = dict(patient_data)

        # Confidence summary
        report.confidence_summary = {
            cat: round(sum(confs) / len(confs), 4)
            for cat, confs in confidence_by_category.items()
            if confs
        }

        # Deduplicate quality flags
        report.quality_flags = list(set(quality_flags))

        logger.info(
            "generate_unified_report – medications=%d, diagnoses=%d, flags=%s",
            len(report.medications),
            len(report.diagnoses),
            report.quality_flags,
        )
        return report

    # ------------------------------------------------------------------
    # Internal helpers – text processing
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_arabic_text(text: str) -> str:
        """Normalise Arabic text by removing tashkeel and standardising forms."""
        tashkeel = re.compile(
            r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7-\u06E8\u06EA-\u06ED]"
        )
        normalised = tashkeel.sub("", text)
        normalised = normalised.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
        normalised = normalised.replace("ة", "ه")
        return normalised.strip().lower()

    @staticmethod
    def _text_similarity(text1: str, text2: str) -> float:
        """Compute similarity between two texts using a simple n-gram approach.

        Returns a value between 0.0 (completely different) and 1.0 (identical).
        """
        if not text1 or not text2:
            return 0.0

        # Normalise both texts
        t1 = text1.lower().strip()
        t2 = text2.lower().strip()

        if t1 == t2:
            return 1.0

        # Quick check: if one is contained in the other
        if t1 in t2 or t2 in t1:
            return min(len(t1), len(t2)) / max(len(t1), len(t2))

        # Character trigram similarity (Jaccard)
        def trigrams(s: str) -> set:
            return {s[i:i + 3] for i in range(len(s) - 2)}

        tri1 = trigrams(t1)
        tri2 = trigrams(t2)

        if not tri1 or not tri2:
            return 0.0

        intersection = len(tri1 & tri2)
        union = len(tri1 | tri2)

        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _is_arabic_text(text: str) -> bool:
        """Detect whether the text contains significant Arabic content."""
        arabic_chars = sum(1 for c in text if "\u0600" <= c <= "\u06FF")
        return arabic_chars > len(text) * 0.3

    def _normalise_results(
        self,
        results: List[ProcessingResult],
    ) -> List[ProcessingResult]:
        """Add normalised Arabic text to all results."""
        normalised = []
        for r in results:
            nr = r.model_copy()
            nr.text_ar = self._normalise_arabic_text(r.text)
            normalised.append(nr)
        return normalised

    # ------------------------------------------------------------------
    # Internal helpers – conflict detection
    # ------------------------------------------------------------------

    def _detect_conflicts(
        self,
        results: List[ProcessingResult],
    ) -> List[ConflictingItem]:
        """Detect conflicts between results of the same category and source."""
        conflicts: List[ConflictingItem] = []
        by_source_category: Dict[Tuple[Optional[str], Optional[str]], List[ProcessingResult]] = defaultdict(list)

        for r in results:
            key = (r.source_id, r.category)
            by_source_category[key].append(r)

        for key, items in by_source_category.items():
            if len(items) < 2:
                continue

            # Check pairwise for conflicts
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    similarity = self._text_similarity(items[i].text, items[j].text)
                    if similarity < 0.5 and similarity > 0.1:
                        # Different texts but same category/source → potential conflict
                        conf_diff = abs(items[i].confidence - items[j].confidence)
                        if conf_diff < self._conflict_threshold:
                            conflicts.append(
                                ConflictingItem(
                                    items=[items[i], items[j]],
                                    field="text",
                                    description=(
                                        f"Conflicting text: '{items[i].text[:50]}' "
                                        f"vs '{items[j].text[:50]}'"
                                    ),
                                )
                            )

        logger.debug("_detect_conflicts – %d conflicts detected", len(conflicts))
        return conflicts

    # ------------------------------------------------------------------
    # Internal helpers – conflict resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_by_confidence(conflict: ConflictingItem) -> ResolvedItem:
        """Select the highest-confidence result."""
        sorted_items = sorted(conflict.items, key=lambda r: r.confidence, reverse=True)
        selected = sorted_items[0]
        rejected = sorted_items[1:]
        return ResolvedItem(
            conflict_id=conflict.conflict_id,
            selected_result=selected,
            rejected_results=rejected,
            resolution_strategy=ConflictResolutionStrategy.HIGHEST_CONFIDENCE,
            confidence=selected.confidence,
            notes=f"Selected by highest confidence ({selected.confidence:.4f}).",
        )

    @staticmethod
    def _resolve_by_timestamp(conflict: ConflictingItem) -> ResolvedItem:
        """Select the most recently extracted result."""
        sorted_items = sorted(
            conflict.items, key=lambda r: r.extracted_at, reverse=True
        )
        selected = sorted_items[0]
        rejected = sorted_items[1:]
        return ResolvedItem(
            conflict_id=conflict.conflict_id,
            selected_result=selected,
            rejected_results=rejected,
            resolution_strategy=ConflictResolutionStrategy.LATEST_TIMESTAMP,
            confidence=selected.confidence,
            notes=f"Selected by latest timestamp ({selected.extracted_at.isoformat()}).",
        )

    @staticmethod
    def _resolve_by_majority(conflict: ConflictingItem) -> ResolvedItem:
        """Select the text that appears most frequently (exact match)."""
        text_counts: Dict[str, List[ProcessingResult]] = defaultdict(list)
        for item in conflict.items:
            text_counts[item.text.lower()].append(item)

        majority_text = max(text_counts, key=lambda k: len(text_counts[k]))
        majority_items = text_counts[majority_text]
        selected = max(majority_items, key=lambda r: r.confidence)
        rejected = [item for item in conflict.items if item.result_id != selected.result_id]
        return ResolvedItem(
            conflict_id=conflict.conflict_id,
            selected_result=selected,
            rejected_results=rejected,
            resolution_strategy=ConflictResolutionStrategy.MAJORITY_VOTE,
            confidence=selected.confidence,
            notes=f"Selected by majority vote ({len(majority_items)} agreements).",
        )

    # ------------------------------------------------------------------
    # Internal helpers – merging
    # ------------------------------------------------------------------

    def _build_merged_extractions(
        self,
        unique_results: List[ProcessingResult],
        resolved_items: List[ResolvedItem],
    ) -> List[MergedExtraction]:
        """Convert unique results and resolved conflicts into final merged extractions."""
        # Track which result_ids were involved in conflicts (and thus resolved)
        resolved_ids: Dict[str, str] = {}  # result_id -> conflict_id

        for resolved in resolved_items:
            for r in resolved.rejected_results:
                resolved_ids[r.result_id] = resolved.conflict_id

        merged: List[MergedExtraction] = []
        for result in unique_results:
            if result.result_id in resolved_ids:
                # This was rejected in a conflict resolution; skip it
                continue

            me = MergedExtraction(
                text=result.text,
                text_ar=result.text_ar,
                confidence=result.confidence,
                source_results=[result.result_id],
                source_engines=[result.engine],
                bounding_box=result.bounding_box,
                category=result.category,
                metadata=result.metadata,
                is_merged=False,
                is_conflict_resolved=False,
            )
            merged.append(me)

        # Add resolved selections
        for resolved in resolved_items:
            selected = resolved.selected_result
            me = MergedExtraction(
                text=selected.text,
                text_ar=selected.text_ar,
                confidence=resolved.confidence,
                source_results=[selected.result_id],
                source_engines=[selected.engine],
                bounding_box=selected.bounding_box,
                category=selected.category,
                metadata=selected.metadata,
                is_merged=True,
                is_conflict_resolved=True,
            )
            merged.append(me)

        # Sort by confidence descending
        merged.sort(key=lambda m: m.confidence, reverse=True)

        return merged

    @staticmethod
    def _merge_bounding_boxes(
        bbox1: Optional[Dict[str, int]],
        bbox2: Optional[Dict[str, int]],
    ) -> Optional[Dict[str, int]]:
        """Merge two bounding boxes using intersection."""
        if not bbox1 and not bbox2:
            return None
        if not bbox1:
            return bbox2
        if not bbox2:
            return bbox1

        return {
            "x1": max(bbox1.get("x1", 0), bbox2.get("x1", 0)),
            "y1": max(bbox1.get("y1", 0), bbox2.get("y1", 0)),
            "x2": min(bbox1.get("x2", 9999), bbox2.get("x2", 9999)),
            "y2": min(bbox1.get("y2", 9999), bbox2.get("y2", 9999)),
        }

    # ------------------------------------------------------------------
    # Internal helpers – classification & extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_extraction(
        text: str,
        hint_category: Optional[str],
    ) -> Optional[str]:
        """Classify extraction text into a patient data category."""
        if hint_category:
            return hint_category

        text_lower = text.lower()

        # Medication patterns
        med_patterns = [
            r"\d+\s*mg", r"\d+\s*mcg", r"\d+\s*ml",
            "tablet", "capsule", "injection", "syrup", "cream",
            "قرص", "كبسولة", "حقن", "شراب",
            "metformin", "amoxicillin", "ibuprofen", "paracetamol", "aspirin",
            "lisinopril", "amlodipine", "losartan", "omeprazole",
            "ميتفورمين", "أموكسيسيلين", "إيبوبروفين", "باراسيتامول",
        ]
        for pattern in med_patterns:
            if re.search(pattern, text_lower):
                return "medication"

        # Diagnosis patterns
        diag_patterns = [
            r"diagnos", "diabetes", "hypertension", "asthma", "copd",
            r"heart failure", "pneumonia", "migraine",
            "تشخيص", "سكري", "ضغط الدم", "ربو", "قصور القلب",
        ]
        for pattern in diag_patterns:
            if re.search(pattern, text_lower):
                return "diagnosis"

        # Procedure patterns
        proc_patterns = [
            r"surgery", r"biopsy", r"ct scan", r"mri", r"x-ray",
            r"ecg", r"echo", r"labour",
            "جراحة", "خزعة", "تصوير", "موجات قلب",
        ]
        for pattern in proc_patterns:
            if re.search(pattern, text_lower):
                return "procedure"

        # Date patterns
        if re.search(r"\d{1,4}[/-]\d{1,2}[/-]\d{1,4}", text_lower):
            return "date"

        # Name patterns (simple heuristic)
        if re.match(r"^[A-Z][a-z]+\s+[A-Z][a-z]+$", text.strip()):
            return "patient_name"

        return None

    @staticmethod
    def _classify_arabic_extraction(text: str) -> Optional[str]:
        """Classify Arabic text into a patient data category."""
        text_lower = text.lower()

        if any(kw in text_lower for kw in ["قرص", "كبسولة", "حقن", "شراب", "ملغ"]):
            return "medication"
        if any(kw in text_lower for kw in ["تشخيص", "سكري", "ضغط", "ربو"]):
            return "diagnosis"
        if any(kw in text_lower for kw in ["جراحة", "خزعة", "عملية"]):
            return "procedure"
        if any(kw in text_lower for kw in ["دكتور", "طبيب", "بروفيسور"]):
            return "physician_name"

        return None

    @staticmethod
    def _looks_like_dob(text: str) -> bool:
        """Heuristic to check if a date string looks like a date of birth."""
        # DOBs typically have years in the 1920–2020 range
        years = re.findall(r"\b(19[2-9]\d|20[0-1]\d)\b", text)
        return len(years) == 1

    @staticmethod
    def _parse_vitals(text: str) -> Dict[str, str]:
        """Parse vitals from text like 'BP 120/80', 'HR 72 bpm'."""
        vitals: Dict[str, str] = {}

        bp_match = re.search(r"[Bb][Pp]\s*(\d{2,3})\s*/\s*(\d{2,3})", text)
        if bp_match:
            vitals["blood_pressure"] = f"{bp_match.group(1)}/{bp_match.group(2)}"

        hr_match = re.search(r"[Hh][Rr]\s*(\d{2,3})", text)
        if hr_match:
            vitals["heart_rate"] = hr_match.group(1)

        temp_match = re.search(r"(\d{2,3}\.?\d*)\s*[°]?[Cc]", text)
        if temp_match:
            vitals["temperature"] = temp_match.group(1)

        return vitals

    @staticmethod
    def _summarise_engines(results: List[ProcessingResult]) -> str:
        """Summarise engine distribution for logging."""
        counts: Dict[str, int] = defaultdict(int)
        for r in results:
            counts[r.engine.value] += 1
        return ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
