"""
Patient Profile Builder.

Aggregates structured medical data from multiple documents (visits) for a
single patient into a comprehensive :class:`PatientProfile`.  Merges medication
lists, diagnoses, vital signs, and lab results across visits while tracking
temporal changes in a patient timeline.
"""

import logging
from typing import Optional, List, Dict, Any
from uuid import uuid4
from datetime import datetime, date

from pydantic import BaseModel, Field

from app.config import settings
from app.ai.schema_extractor import (
    VitalSigns,
    Medication,
    Diagnosis,
    LabResult,
    PatientInfo,
    MedicalDataExtract,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Data Models
# =============================================================================


class MedicationEntry(BaseModel):
    """A medication entry within the patient profile."""

    name: str = Field(description="Drug / medication name")
    dosage: Optional[str] = Field(default=None)
    frequency: Optional[str] = Field(default=None)
    route: Optional[str] = Field(default=None)
    status: str = Field(default="active", description="'active', 'discontinued', 'changed'")
    start_date: Optional[str] = Field(default=None, description="When medication was first observed")
    end_date: Optional[str] = Field(default=None, description="When medication was discontinued")
    source_document_id: Optional[str] = Field(default=None)
    notes: Optional[str] = Field(default=None)


class DiagnosisEntry(BaseModel):
    """A diagnosis entry within the patient profile."""

    code: Optional[str] = Field(default=None, description="ICD code")
    description: str = Field(description="Diagnosis description")
    severity: Optional[str] = Field(default=None)
    chronic: bool = Field(default=False)
    first_seen: Optional[str] = Field(default=None, description="Earliest date this diagnosis was observed")
    last_seen: Optional[str] = Field(default=None, description="Most recent date observed")
    status: str = Field(default="active", description="'active', 'resolved', 'recurrent'")
    source_document_ids: List[str] = Field(default_factory=list)


class VitalSignSnapshot(BaseModel):
    """A single vital-sign measurement at a point in time."""

    systolic_bp: Optional[float] = None
    diastolic_bp: Optional[float] = None
    heart_rate: Optional[int] = None
    temperature: Optional[float] = None
    spo2: Optional[float] = None
    respiratory_rate: Optional[int] = None
    recorded_at: str = Field(description="ISO timestamp of measurement")
    source_document_id: Optional[str] = None


class LabResultEntry(BaseModel):
    """A lab result within the patient profile."""

    test_name: str
    value: Optional[float] = None
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    is_abnormal: bool = False
    recorded_at: str = Field(description="ISO timestamp")
    source_document_id: Optional[str] = None


class VisitRecord(BaseModel):
    """Represents a single clinical visit / document encounter."""

    visit_date: str = Field(description="Date of the visit (ISO format or free-form)")
    document_id: Optional[str] = Field(default=None)
    document_type: Optional[str] = Field(default=None, description="Type: 'prescription', 'lab_report', 'clinical_note', etc.")
    chief_complaint: Optional[str] = Field(default=None)
    summary: Optional[str] = Field(default=None, description="Brief summary of visit content")
    vitals: Optional[VitalSignSnapshot] = Field(default=None)
    medications: List[Medication] = Field(default_factory=list)
    diagnoses: List[Diagnosis] = Field(default_factory=list)
    lab_results: List[LabResult] = Field(default_factory=list)


class PatientTimeline(BaseModel):
    """Chronological timeline of clinical events for a patient."""

    events: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of {date, event_type, description, source_document_id} dicts",
    )

    def add_event(self, date: str, event_type: str, description: str, source_document_id: Optional[str] = None) -> None:
        """Append an event to the timeline, maintaining chronological order."""
        event = {
            "date": date,
            "event_type": event_type,
            "description": description,
            "source_document_id": source_document_id,
        }
        self.events.append(event)
        self.events.sort(key=lambda e: e["date"])


class PatientProfile(BaseModel):
    """
    Comprehensive profile for a single patient, aggregating data across
    all available visits / documents.
    """

    patient_id: str = Field(description="Unique patient identifier")
    patient_info: Optional[PatientInfo] = Field(default=None)
    visits: List[VisitRecord] = Field(default_factory=list)
    medications: List[MedicationEntry] = Field(default_factory=list, description="Current merged medication list")
    diagnoses: List[DiagnosisEntry] = Field(default_factory=list, description="All diagnoses across visits")
    vitals_history: List[VitalSignSnapshot] = Field(default_factory=list, description="Vital sign measurements over time")
    lab_results_history: List[LabResultEntry] = Field(default_factory=list, description="Lab results over time")
    timeline: PatientTimeline = Field(default_factory=PatientTimeline)
    summary: Optional[str] = Field(default=None, description="Auto-generated patient summary")
    allergies: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# =============================================================================
# PatientProfileBuilder
# =============================================================================


class PatientProfileBuilder:
    """
    Constructs and maintains comprehensive patient profiles by aggregating
    structured data extracted from multiple medical documents.

    Usage::

        builder = PatientProfileBuilder()
        profile = builder.build_profile("patient-001", [doc1, doc2, doc3])
    """

    def __init__(self):
        logger.info("PatientProfileBuilder initialised")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_profile(
        self,
        patient_id: str,
        documents: List[Dict[str, Any]],
    ) -> PatientProfile:
        """
        Build a complete patient profile from a list of document dicts.

        Each document dict should contain:
            * ``document_id`` (str, optional)
            * ``visit_date`` (str, optional)
            * ``document_type`` (str, optional)
            * ``text`` (str) — full OCR text
            * ``extracted_data`` (MedicalDataExtract, optional) — pre-extracted data

        If ``extracted_data`` is not provided, the builder will run
        :class:`MedicalSchemaExtractor` on the document text.

        Args:
            patient_id: Unique patient identifier.
            documents: List of document dictionaries.

        Returns:
            A fully populated :class:`PatientProfile`.
        """
        profile = PatientProfile(patient_id=patient_id)
        profile.updated_at = datetime.utcnow().isoformat()

        for doc in documents:
            try:
                profile = self.add_visit(profile, doc)
            except Exception as exc:
                logger.error(
                    "Failed to process document %s: %s",
                    doc.get("document_id", "unknown"),
                    exc,
                )

        # Generate final summary
        profile.summary = self.generate_summary(profile)

        logger.info(
            "Built profile for patient '%s': %d visits, %d medications, %d diagnoses",
            patient_id,
            len(profile.visits),
            len(profile.medications),
            len(profile.diagnoses),
        )
        return profile

    def add_visit(self, profile: PatientProfile, document: Dict[str, Any]) -> PatientProfile:
        """
        Add a single visit (document) to an existing patient profile.

        Extracts structured data from the document (if not pre-extracted),
        merges medications, appends diagnoses, records vitals and labs,
        and updates the patient timeline.

        Args:
            profile: Existing :class:`PatientProfile` to update.
            document: Document dict (see :meth:`build_profile` for schema).

        Returns:
            The updated :class:`PatientProfile`.
        """
        doc_id = document.get("document_id")
        visit_date = document.get("visit_date", datetime.utcnow().isoformat()[:10])
        doc_type = document.get("document_type", "unknown")
        text = document.get("text", "")

        # Get or extract structured data
        extracted: MedicalDataExtract = document.get("extracted_data")
        if extracted is None and text:
            try:
                from app.ai.schema_extractor import MedicalSchemaExtractor
                extractor = MedicalSchemaExtractor()
                extracted = extractor.extract_all(text)
            except Exception as exc:
                logger.error("Schema extraction failed for document %s: %s", doc_id, exc)
                extracted = MedicalDataExtract()

        if extracted is None:
            extracted = MedicalDataExtract()

        # Build visit record
        visit = VisitRecord(
            visit_date=visit_date,
            document_id=doc_id,
            document_type=doc_type,
            vitals=(
                VitalSignSnapshot(
                    systolic_bp=extracted.vital_signs.systolic_bp,
                    diastolic_bp=extracted.vital_signs.diastolic_bp,
                    heart_rate=extracted.vital_signs.heart_rate,
                    temperature=extracted.vital_signs.temperature,
                    spo2=extracted.vital_signs.spo2,
                    respiratory_rate=extracted.vital_signs.respiratory_rate,
                    recorded_at=visit_date,
                    source_document_id=doc_id,
                )
                if any([
                    extracted.vital_signs.systolic_bp,
                    extracted.vital_signs.heart_rate,
                    extracted.vital_signs.temperature,
                ])
                else None
            ),
            medications=extracted.medications,
            diagnoses=extracted.diagnoses,
            lab_results=extracted.lab_results,
            summary=self._visit_summary(extracted),
        )
        profile.visits.append(visit)

        # Merge patient info (keep most complete)
        if extracted.patient_info and (extracted.patient_info.name or extracted.patient_info.age):
            if profile.patient_info is None:
                profile.patient_info = extracted.patient_info
            else:
                self._merge_patient_info(profile.patient_info, extracted.patient_info)

        # Merge medications
        profile.medications = self.merge_medications(profile.medications, extracted.medications, doc_id, visit_date)

        # Merge diagnoses
        profile.diagnoses = self._merge_diagnoses(profile.diagnoses, extracted.diagnoses, doc_id, visit_date)

        # Append vitals
        if visit.vitals:
            profile.vitals_history.append(visit.vitals)

        # Append lab results
        for lab in extracted.lab_results:
            profile.lab_results_history.append(
                LabResultEntry(
                    test_name=lab.test_name,
                    value=lab.value,
                    unit=lab.unit,
                    reference_range=lab.reference_range,
                    is_abnormal=lab.is_abnormal,
                    recorded_at=visit_date,
                    source_document_id=doc_id,
                )
            )

        # Merge allergies
        for allergy in extracted.patient_info.allergies:
            if allergy not in profile.allergies:
                profile.allergies.append(allergy)

        # Update timeline
        self._update_timeline(profile.timeline, visit, extracted)

        profile.updated_at = datetime.utcnow().isoformat()
        return profile

    def merge_medications(
        self,
        current: List[MedicationEntry],
        new_meds: List[Medication],
        source_doc_id: Optional[str] = None,
        source_date: Optional[str] = None,
    ) -> List[MedicationEntry]:
        """
        Merge a new list of medications into the current medication list.

        Matching is done by medication name (case-insensitive, stripped of
        whitespace).  If a medication already exists, its details are updated
        (e.g. dosage change).  New medications are appended.

        Args:
            current: Existing medication entries in the profile.
            new_meds: Newly extracted medication list.
            source_doc_id: Document ID where the new meds came from.
            source_date: Date of the source document.

        Returns:
            Updated list of :class:`MedicationEntry`.
        """
        # Build lookup by normalised name
        current_by_name: Dict[str, MedicationEntry] = {}
        for entry in current:
            key = entry.name.lower().strip()
            current_by_name[key] = entry

        for med in new_meds:
            key = med.name.lower().strip()
            if key in current_by_name:
                existing = current_by_name[key]
                # Update details if they changed
                if med.dosage and med.dosage != existing.dosage:
                    existing.notes = f"Dosage changed from {existing.dosage} to {med.dosage}"
                    existing.dosage = med.dosage
                if med.frequency and med.frequency != existing.frequency:
                    existing.frequency = med.frequency
                if med.route and med.route != existing.route:
                    existing.route = med.route
                existing.end_date = None  # Still active
                existing.status = "active"
            else:
                current_by_name[key] = MedicationEntry(
                    name=med.name,
                    dosage=med.dosage,
                    frequency=med.frequency,
                    route=med.route,
                    start_date=source_date,
                    source_document_id=source_doc_id,
                    status="active",
                )

        return list(current_by_name.values())

    def generate_summary(self, profile: PatientProfile) -> str:
        """
        Generate a human-readable summary of the patient profile.

        Includes demographics, active medications, diagnoses, recent vitals,
        and notable lab results.

        Args:
            profile: The complete patient profile.

        Returns:
            A multi-line summary string.
        """
        lines: List[str] = []

        # Patient demographics
        if profile.patient_info:
            info = profile.patient_info
            name_part = info.name or "Unknown"
            age_part = info.age or "Unknown age"
            gender_part = info.gender or ""
            lines.append(f"Patient: {name_part} ({age_part}) {gender_part}".strip())
            if info.patient_id:
                lines.append(f"ID: {info.patient_id}")
            if profile.allergies:
                lines.append(f"Allergies: {', '.join(profile.allergies)}")
            lines.append("")

        # Visit overview
        lines.append(f"Total visits: {len(profile.visits)}")
        if profile.visits:
            lines.append(f"Most recent: {profile.visits[-1].visit_date}")
        lines.append("")

        # Active medications
        active_meds = [m for m in profile.medications if m.status == "active"]
        if active_meds:
            lines.append("Active Medications:")
            for med in active_meds:
                detail_parts = [med.name]
                if med.dosage:
                    detail_parts.append(med.dosage)
                if med.frequency:
                    detail_parts.append(med.frequency)
                if med.route:
                    detail_parts.append(med.route)
                lines.append(f"  • {' '.join(detail_parts)}")
            lines.append("")

        # Diagnoses
        if profile.diagnoses:
            lines.append("Diagnoses:")
            for diag in profile.diagnoses:
                prefix = "[Chronic] " if diag.chronic else ""
                code_part = f" ({diag.code})" if diag.code else ""
                status_part = f" [{diag.status}]" if diag.status != "active" else ""
                lines.append(f"  • {prefix}{diag.description}{code_part}{status_part}")
            lines.append("")

        # Recent vitals
        if profile.vitals_history:
            latest = profile.vitals_history[-1]
            vital_parts = []
            if latest.systolic_bp and latest.diastolic_bp:
                vital_parts.append(f"BP: {int(latest.systolic_bp)}/{int(latest.diastolic_bp)} mmHg")
            if latest.heart_rate:
                vital_parts.append(f"HR: {latest.heart_rate} bpm")
            if latest.temperature:
                vital_parts.append(f"Temp: {latest.temperature}°C")
            if latest.spo2:
                vital_parts.append(f"SpO2: {latest.spo2}%")
            if latest.respiratory_rate:
                vital_parts.append(f"RR: {latest.respiratory_rate}/min")
            if vital_parts:
                lines.append(f"Latest Vitals ({latest.recorded_at}):")
                for part in vital_parts:
                    lines.append(f"  • {part}")
                lines.append("")

        # Notable lab results (abnormal)
        abnormal_labs = [l for l in profile.lab_results_history if l.is_abnormal]
        if abnormal_labs:
            lines.append("Notable Lab Results (abnormal):")
            for lab in abnormal_labs[-5:]:  # Show most recent 5
                val_str = f"{lab.value} {lab.unit or ''}".strip() if lab.value is not None else "N/A"
                ref_str = f" (ref: {lab.reference_range})" if lab.reference_range else ""
                status_str = f" [{lab.status.upper()}]" if lab.status else ""
                lines.append(f"  • {lab.test_name}: {val_str}{ref_str}{status_str} ({lab.recorded_at})")
            lines.append("")

        # Timeline highlights
        if profile.timeline.events:
            lines.append(f"Timeline ({len(profile.timeline.events)} events):")
            for event in profile.timeline.events[-10:]:
                lines.append(f"  [{event['date']}] {event['event_type']}: {event['description']}")

        summary = "\n".join(lines).strip()
        return summary

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    def _merge_patient_info(self, current: PatientInfo, new_info: PatientInfo) -> None:
        """Update current patient info with new data, preferring more specific values."""
        if new_info.name and (not current.name or len(new_info.name) > len(current.name)):
            current.name = new_info.name
        if new_info.age and not current.age:
            current.age = new_info.age
        if new_info.gender and not current.gender:
            current.gender = new_info.gender
        if new_info.patient_id and not current.patient_id:
            current.patient_id = new_info.patient_id
        if new_info.phone and not current.phone:
            current.phone = new_info.phone
        if new_info.address and not current.address:
            current.address = new_info.address

    def _merge_diagnoses(
        self,
        current: List[DiagnosisEntry],
        new_diagnoses: List[Diagnosis],
        doc_id: Optional[str],
        visit_date: Optional[str],
    ) -> List[DiagnosisEntry]:
        """
        Merge new diagnoses into the current list.

        Matching by ICD code (exact) or description similarity.
        """
        # Build lookup
        current_by_code: Dict[str, DiagnosisEntry] = {
            d.code: d for d in current if d.code
        }
        current_by_desc: Dict[str, DiagnosisEntry] = {
            d.description.lower().strip(): d for d in current if d.description
        }

        for diag in new_diagnoses:
            matched = False

            # Try ICD code match
            if diag.code and diag.code in current_by_code:
                entry = current_by_code[diag.code]
                entry.last_seen = visit_date
                if doc_id not in entry.source_document_ids:
                    entry.source_document_ids.append(doc_id)
                matched = True

            # Try description match
            if not matched and diag.description:
                key = diag.description.lower().strip()[:50]
                if key in current_by_desc:
                    entry = current_by_desc[key]
                    entry.last_seen = visit_date
                    if doc_id not in entry.source_document_ids:
                        entry.source_document_ids.append(doc_id)
                    matched = True

            if not matched:
                current.append(
                    DiagnosisEntry(
                        code=diag.code,
                        description=diag.description,
                        severity=diag.severity,
                        chronic=diag.chronic,
                        first_seen=visit_date,
                        last_seen=visit_date,
                        status="active",
                        source_document_ids=[doc_id] if doc_id else [],
                    )
                )
                # Update lookups for subsequent matches
                if diag.code:
                    current_by_code[diag.code] = current[-1]
                if diag.description:
                    current_by_desc[diag.description.lower().strip()[:50]] = current[-1]

        return current

    def _update_timeline(self, timeline: PatientTimeline, visit: VisitRecord, extracted: MedicalDataExtract) -> None:
        """Add timeline events based on visit data."""
        date = visit.visit_date
        doc_id = visit.document_id

        # Medication events
        for med in extracted.medications:
            timeline.add_event(
                date=date,
                event_type="medication",
                description=f"Prescribed: {med.name} {med.dosage or ''} {med.frequency or ''}".strip(),
                source_document_id=doc_id,
            )

        # Diagnosis events
        for diag in extracted.diagnoses:
            timeline.add_event(
                date=date,
                event_type="diagnosis",
                description=diag.description,
                source_document_id=doc_id,
            )

        # Abnormal lab events
        for lab in extracted.lab_results:
            if lab.is_abnormal:
                val_str = f"{lab.value} {lab.unit or ''}".strip() if lab.value is not None else ""
                timeline.add_event(
                    date=date,
                    event_type="abnormal_lab",
                    description=f"{lab.test_name}: {val_str}",
                    source_document_id=doc_id,
                )

    @staticmethod
    def _visit_summary(extracted: MedicalDataExtract) -> str:
        """Generate a one-line summary for a visit."""
        parts: List[str] = []
        if extracted.medications:
            parts.append(f"{len(extracted.medications)} medications")
        if extracted.diagnoses:
            parts.append(f"{len(extracted.diagnoses)} diagnoses")
        if extracted.vital_signs.systolic_bp:
            parts.append("vitals recorded")
        if extracted.lab_results:
            abnormal = sum(1 for l in extracted.lab_results if l.is_abnormal)
            parts.append(f"{len(extracted.lab_results)} labs ({abnormal} abnormal)")

        return "; ".join(parts) if parts else "No structured data extracted"
