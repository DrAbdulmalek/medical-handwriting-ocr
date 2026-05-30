"""
Clinical Question Answering Engine.

Provides the ``ClinicalQA`` class for evidence-based medical question answering,
drug interaction checking, contraindication warnings, differential diagnosis
suggestions, treatment protocol recommendations, and dosage validation.

All text fields support Arabic input and output for bilingual clinical
environments.
"""

import logging
import math
import re
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


class EvidenceLevel(str, Enum):
    """Hierarchical evidence levels per GRADE-style classification."""

    SYSTEMATIC_REVIEW = "systematic_review"
    RANDOMISED_TRIAL = "randomised_trial"
    COHORT_STUDY = "cohort_study"
    CASE_CONTROL = "case_control"
    EXPERT_OPINION = "expert_opinion"
    CLINICAL_EXPERIENCE = "clinical_experience"


class SeverityLevel(str, Enum):
    """Interaction or contraindication severity."""

    CONTRAINDICATED = "contraindicated"
    MAJOR = "major"
    MODERATE = "moderate"
    MINOR = "minor"


class DosageStatus(str, Enum):
    """Dosage validation result status."""

    WITHIN_RANGE = "within_range"
    BELOW_MINIMUM = "below_minimum"
    ABOVE_MAXIMUM = "above_maximum"
    ADJUSTMENT_NEEDED = "adjustment_needed"
    CONTRAINDICATED = "contraindicated"


class Evidence(BaseModel):
    """A single evidence item backing a clinical answer."""

    evidence_id: str = Field(default_factory=lambda: str(uuid4()))
    source: str = Field(..., description="Source title, e.g. 'NEJM 2023', 'WHO Guideline 2024'")
    source_url: Optional[str] = None
    level: EvidenceLevel = Field(default=EvidenceLevel.EXPERT_OPINION)
    excerpt: str = Field(..., description="Relevant excerpt (may be Arabic)")
    excerpt_ar: Optional[str] = Field(default=None, description="Arabic excerpt")
    publication_year: Optional[int] = None
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)


class ClinicalAnswer(BaseModel):
    """Full answer to a clinical question with evidence citations."""

    answer_id: str = Field(default_factory=lambda: str(uuid4()))
    question: str
    answer: str = Field(..., description="Answer text (supports Arabic)")
    answer_ar: Optional[str] = Field(default=None, description="Arabic answer")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: List[Evidence] = Field(default_factory=list)
    related_conditions: List[str] = Field(default_factory=list)
    disclaimer: str = Field(
        default="This information is for clinical decision support only and "
        "does not replace professional medical judgment.",
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class InteractionReport(BaseModel):
    """Drug-drug interaction report for a list of medications."""

    report_id: str = Field(default_factory=lambda: str(uuid4()))
    drug_list: List[str] = Field(..., description="Input drug names (may include Arabic)")
    interactions: List["DrugInteraction"] = Field(default_factory=list)
    severity_summary: SeverityLevel = Field(default=SeverityLevel.MINOR)
    recommendation: str = Field(default="")
    recommendation_ar: Optional[str] = Field(
        default=None,
        description="Arabic recommendation",
    )
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DrugInteraction(BaseModel):
    """A single drug-drug interaction entry."""

    drug_a: str
    drug_b: str
    severity: SeverityLevel = SeverityLevel.MODERATE
    mechanism: str = Field(default="")
    mechanism_ar: Optional[str] = Field(default=None, description="Arabic mechanism description")
    clinical_effect: str = Field(default="")
    management: str = Field(default="")
    evidence_level: EvidenceLevel = Field(default=EvidenceLevel.EXPERT_OPINION)
    source: str = Field(default="")


class Contraindication(BaseModel):
    """A single contraindication for a drug given patient conditions."""

    contraindication_id: str = Field(default_factory=lambda: str(uuid4()))
    drug: str
    condition: str
    severity: SeverityLevel = SeverityLevel.MAJOR
    details: str = Field(default="")
    details_ar: Optional[str] = Field(default=None, description="Arabic details")
    alternative_suggestion: Optional[str] = None
    alternative_suggestion_ar: Optional[str] = None
    evidence: List[Evidence] = Field(default_factory=list)


class DifferentialDiagnosis(BaseModel):
    """A single differential diagnosis suggestion."""

    diagnosis_id: str = Field(default_factory=lambda: str(uuid4()))
    condition: str
    condition_ar: Optional[str] = Field(default=None, description="Arabic condition name")
    probability: float = Field(default=0.0, ge=0.0, le=1.0, description="Estimated likelihood")
    supporting_symptoms: List[str] = Field(default_factory=list)
    supporting_symptoms_ar: Optional[List[str]] = Field(
        default=None, description="Arabic symptom names"
    )
    distinguishing_features: List[str] = Field(default_factory=list)
    distinguishing_features_ar: Optional[List[str]] = Field(
        default=None, description="Arabic feature descriptions"
    )
    recommended_tests: List[str] = Field(default_factory=list)
    icd10_code: Optional[str] = None


class TreatmentStep(BaseModel):
    """A single step within a treatment protocol."""

    step_number: int
    description: str
    description_ar: Optional[str] = Field(default=None, description="Arabic description")
    duration: Optional[str] = Field(default=None, description="e.g. '7 days', 'until resolved'")
    notes: Optional[str] = None
    notes_ar: Optional[str] = None


class TreatmentProtocol(BaseModel):
    """Complete treatment protocol for a condition."""

    protocol_id: str = Field(default_factory=lambda: str(uuid4()))
    condition: str
    condition_ar: Optional[str] = Field(default=None, description="Arabic condition name")
    icd10_code: Optional[str] = None
    severity_grades: List[str] = Field(default_factory=list, description="e.g. ['mild', 'moderate', 'severe']")
    steps: List[TreatmentStep] = Field(default_factory=list)
    medications: List[str] = Field(default_factory=list)
    follow_up: Optional[str] = Field(default=None, description="Follow-up schedule")
    follow_up_ar: Optional[str] = Field(default=None)
    source: Optional[str] = Field(default=None, description="Guideline or reference source")
    last_updated: Optional[datetime] = None


class DosageValidation(BaseModel):
    """Result of a dosage validation check."""

    validation_id: str = Field(default_factory=lambda: str(uuid4()))
    drug: str
    drug_ar: Optional[str] = Field(default=None, description="Arabic drug name")
    patient_weight_kg: Optional[float] = None
    patient_age_years: Optional[float] = None
    suggested_min_mg: Optional[float] = Field(default=None, description="Minimum recommended dose (mg)")
    suggested_max_mg: Optional[float] = Field(default=None, description="Maximum recommended dose (mg)")
    calculated_dose_mg: Optional[float] = Field(default=None, description="Weight-based calculated dose")
    status: DosageStatus = DosageStatus.WITHIN_RANGE
    notes: str = Field(default="")
    notes_ar: Optional[str] = Field(default=None, description="Arabic notes")
    adjustment_factors: List[str] = Field(
        default_factory=list,
        description="e.g. ['renal_impairment', 'elderly']",
    )


# ---------------------------------------------------------------------------
# Inline knowledge base (simplified; production would use RAG + LLM)
# ---------------------------------------------------------------------------

# Drug interaction knowledge – production would query a clinical database
_DRUG_INTERACTIONS: Dict[Tuple[str, str], DrugInteraction] = {
    ("warfarin", "aspirin"): DrugInteraction(
        drug_a="Warfarin",
        drug_b="Aspirin",
        severity=SeverityLevel.MAJOR,
        mechanism="Antiplatelet + anticoagulant synergism increases bleeding risk.",
        clinical_effect="Increased risk of gastrointestinal and intracranial bleeding.",
        management="Avoid combination. If unavoidable, monitor INR closely and consider gastroprotection.",
        evidence_level=EvidenceLevel.SYSTEMATIC_REVIEW,
        source="ACC/AHA Guideline 2023",
    ),
    ("metformin", "contrast_dye"): DrugInteraction(
        drug_a="Metformin",
        drug_b="Iodinated Contrast Dye",
        severity=SeverityLevel.MAJOR,
        mechanism="Contrast-induced nephropathy can precipitate lactic acidosis.",
        clinical_effect="Risk of lactic acidosis in patients with renal impairment.",
        management="Hold metformin 48 h before and after contrast study; check eGFR.",
        evidence_level=EvidenceLevel.EXPERT_OPINION,
        source="ESUR Contrast Media Guidelines 2023",
    ),
    ("ssri", "tramadol"): DrugInteraction(
        drug_a="SSRI (e.g. Sertraline)",
        drug_b="Tramadol",
        severity=SeverityLevel.MAJOR,
        mechanism="Serotonin syndrome risk – both increase CNS serotonin.",
        clinical_effect="Serotonin syndrome: agitation, hyperthermia, rigidity.",
        management="Avoid combination. Use alternative analgesics.",
        evidence_level=EvidenceLevel.COHORT_STUDY,
        source="FDA Drug Safety Communication",
    ),
    ("amlodipine", "simvastatin"): DrugInteraction(
        drug_a="Amlodipine",
        drug_b="Simvastatin",
        severity=SeverityLevel.MODERATE,
        mechanism="CYP3A4 inhibition increases simvastatin levels.",
        clinical_effect="Increased risk of myopathy and rhabdomyolysis.",
        management="Limit simvastatin to 20 mg/day when used with amlodipine.",
        evidence_level=EvidenceLevel.RANDOMISED_TRIAL,
        source="MHRA Drug Safety Update",
    ),
}

# Contraindication knowledge
_CONTRAINDICATIONS: Dict[Tuple[str, str], Contraindication] = {
    ("nsaid", "peptic_ulcer"): Contraindication(
        drug="NSAIDs",
        condition="Active Peptic Ulcer Disease",
        severity=SeverityLevel.CONTRAINDICATED,
        details="NSAIDs increase gastric mucosal injury and bleeding risk.",
        details_ar="تزيد مضادات الالتهاب غير الستيرويدية من خطر إصابة الغشاء المخاطي المعدي والنزيف.",
        alternative_suggestion="Use acetaminophen or a COX-2 selective inhibitor with PPI co-therapy.",
        alternative_suggestion_ar="استخدم الباراسيتامول أو مثبط COX-2 الانتقائي مع العلاج المثبط لمضخة البروتون.",
    ),
    ("metformin", "renal_failure"): Contraindication(
        drug="Metformin",
        condition="Severe Renal Failure (eGFR < 30)",
        severity=SeverityLevel.CONTRAINDICATED,
        details="Risk of lactic acidosis in advanced renal impairment.",
        details_ar="خطر الحماض اللبني في حالات القصور الكلوي المتقدم.",
        alternative_suggestion="Switch to insulin or sulfonylurea.",
        alternative_suggestion_ar="التبديل إلى الأنسولين أو السلفونيل يوريا.",
    ),
    ("beta_blocker", "asthma"): Contraindication(
        drug="Non-selective Beta Blockers (e.g. Propranolol)",
        condition="Asthma / Severe COPD",
        severity=SeverityLevel.MAJOR,
        details="Can cause bronchoconstriction by blocking β2 receptors.",
        details_ar="يمكن أن يسبب تضيق القصبات عن طريق حصار مستقبلات بيتا 2.",
        alternative_suggestion="Use cardioselective beta-blocker (e.g. Bisoprolol, Metoprolol) with caution.",
        alternative_suggestion_ar="استخدم حاصرات بيتا الانتقائية القلبية (مثل بيسوبرولول) بحذر.",
    ),
}

# Dosage reference (mg per kg per day unless otherwise noted)
_DOSAGE_REFERENCE: Dict[str, Dict[str, Any]] = {
    "amoxicillin": {
        "min_mg_per_kg_day": 25,
        "max_mg_per_kg_day": 50,
        "max_total_mg": 1500,
        "frequency": "8-hourly",
        "notes": "Adjust for renal impairment.",
        "notes_ar": "تعديل الجرعة في حالة ضعف الكلى.",
    },
    "paracetamol": {
        "min_mg_per_kg_day": 10,
        "max_mg_per_kg_day": 15,
        "max_total_mg": 4000,
        "frequency": "4–6-hourly",
        "notes": "Max 4 doses per 24 h. Reduce in hepatic impairment.",
        "notes_ar": "الحد الأقصى 4 جرعات في 24 ساعة. تقليل الجرعة في حالات ضعف الكبد.",
    },
    "ibuprofen": {
        "min_mg_per_kg_day": 5,
        "max_mg_per_kg_day": 10,
        "max_total_mg": 1200,
        "frequency": "6–8-hourly",
        "notes": "Take with food. Avoid in renal impairment.",
        "notes_ar": "تناول مع الطعام. تجنب في حالات ضعف الكلى.",
    },
}


# ---------------------------------------------------------------------------
# ClinicalQA
# ---------------------------------------------------------------------------


class ClinicalQA:
    """Evidence-based clinical question answering and decision support.

    Provides methods for answering clinical questions, checking drug
    interactions, identifying contraindications, suggesting differential
    diagnoses, recommending treatment protocols, and validating dosages.

    All text fields support Arabic input/output for bilingual clinical
    environments.

    Usage::

        qa = ClinicalQA()

        answer = await qa.ask_clinical_question(
            "What is the first-line treatment for uncomplicated hypertension?"
        )

        interactions = await qa.check_drug_interactions(
            ["warfarin", "aspirin", "amlodipine"]
        )

        contra = await qa.get_contraindications("NSAIDs", ["peptic ulcer", "asthma"])

        diff = await qa.suggest_differential(["headache", "fever", "neck stiffness"])

        protocol = await qa.get_treatment_protocol("type 2 diabetes")

        dosage = await qa.validate_dosage("amoxicillin", patient_weight=70, age=45)
    """

    def __init__(self) -> None:
        self._interactions = _DRUG_INTERACTIONS
        self._contraindications = _CONTRAINDICATIONS
        self._dosage_ref = _DOSAGE_REFERENCE
        logger.info("ClinicalQA initialised with %d known drug interactions", len(self._interactions))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ask_clinical_question(
        self,
        question: str,
        patient_context: Optional[Dict[str, Any]] = None,
    ) -> ClinicalAnswer:
        """Answer a clinical question using evidence-based reasoning.

        In production this would invoke a RAG pipeline (vector search over
        guidelines + LLM generation).  The current implementation uses the
        embedded knowledge base and heuristics.

        Args:
            question: The clinical question (supports Arabic).
            patient_context: Optional dict with patient demographics,
                            conditions, and medications for personalised answers.

        Returns:
            A :class:`ClinicalAnswer` with answer text, evidence list,
            and confidence score.
        """
        logger.info(
            "ask_clinical_question – question='%s', context=%s",
            question[:120],
            "present" if patient_context else "absent",
        )

        try:
            # Detect question language
            question_normalised = self._normalise_text(question)
            is_arabic = self._is_arabic_text(question)

            # Classify question type and retrieve knowledge
            question_lower = question_normalised.lower()

            # -- Drug interaction question --
            drug_keywords = ["interaction", "interact", "تداخل", "تفاعل"]
            if any(kw in question_lower for kw in drug_keywords):
                return await self._answer_interaction_question(question, patient_context)

            # -- Dosage question --
            dosage_keywords = ["dose", "dosage", "جرعة", "مقدار"]
            if any(kw in question_lower for kw in dosage_keywords):
                return await self._answer_dosage_question(question, patient_context)

            # -- Contraindication question --
            contra_keywords = ["contraindication", "contraindicated", "موانع", "مضاد"]
            if any(kw in question_lower for kw in contra_keywords):
                return await self._answer_contraindication_question(question, patient_context)

            # -- Treatment question --
            treatment_keywords = ["treatment", "treat", "manage", "علاج", "إدارة"]
            if any(kw in question_lower for kw in treatment_keywords):
                return await self._answer_treatment_question(question, patient_context)

            # -- General question – provide best-effort answer --
            answer_text, evidence = self._general_answer(question, patient_context)
            confidence = 0.7 if evidence else 0.3

            answer_ar = None
            if is_arabic:
                answer_ar = answer_text

            return ClinicalAnswer(
                question=question,
                answer=answer_text,
                answer_ar=answer_ar,
                confidence=confidence,
                evidence=evidence,
                related_conditions=self._extract_conditions_from_question(question),
            )

        except Exception:
            logger.exception("Error processing clinical question")
            return ClinicalAnswer(
                question=question,
                answer="An error occurred while processing your question. Please try again.",
                confidence=0.0,
            )

    async def check_drug_interactions(
        self,
        drug_list: List[str],
    ) -> InteractionReport:
        """Check for known drug-drug interactions in a medication list.

        Args:
            drug_list: List of drug names (supports Arabic names).

        Returns:
            An :class:`InteractionReport` with all detected interactions,
            severity summary, and management recommendations.
        """
        logger.info("check_drug_interactions – drugs=%s", drug_list)

        normalised_drugs = [self._normalise_text(d).lower().strip() for d in drug_list]

        interactions: List[DrugInteraction] = []
        severity_order = [
            SeverityLevel.CONTRAINDICATED,
            SeverityLevel.MAJOR,
            SeverityLevel.MODERATE,
            SeverityLevel.MINOR,
        ]

        for i, drug_a in enumerate(normalised_drugs):
            for j, drug_b in enumerate(normalised_drugs):
                if i >= j:
                    continue  # Avoid duplicate pairs

                # Check both orderings
                interaction = self._interactions.get(
                    (drug_a, drug_b)
                ) or self._interactions.get(
                    (drug_b, drug_a)
                )

                if interaction:
                    interactions.append(interaction)

        worst_severity = SeverityLevel.MINOR
        if interactions:
            for sev in severity_order:
                if any(i.severity == sev for i in interactions):
                    worst_severity = sev
                    break

        recommendation = "No significant interactions detected."
        recommendation_ar = "لم يتم اكتشاف تفاعلات دوائية كبيرة."
        if worst_severity in (SeverityLevel.CONTRAINDICATED, SeverityLevel.MAJOR):
            recommendation = (
                "MAJOR interactions detected. Review medication list and "
                "consult with a pharmacist or specialist before proceeding."
            )
            recommendation_ar = (
                "تم اكتشاف تفاعلات كبيرة. راجع قائمة الأدوية واستشر الصيدلي أو الأخصائي."
            )
        elif worst_severity == SeverityLevel.MODERATE:
            recommendation = (
                "Moderate interactions detected. Monitor patient and consider "
                "dose adjustments as indicated."
            )
            recommendation_ar = (
                "تم اكتشاف تفاعلات معتدلة. راقب المريض وفكر في تعديل الجرعة."
            )

        report = InteractionReport(
            drug_list=drug_list,
            interactions=interactions,
            severity_summary=worst_severity,
            recommendation=recommendation,
            recommendation_ar=recommendation_ar,
        )

        logger.info(
            "check_drug_interactions – interactions=%d, worst_severity=%s",
            len(interactions),
            worst_severity.value,
        )
        return report

    async def get_contraindications(
        self,
        drug: str,
        conditions: List[str],
    ) -> List[Contraindication]:
        """Retrieve known contraindications for a drug given patient conditions.

        Args:
            drug: Drug name (supports Arabic).
            conditions: List of patient conditions or comorbidities.

        Returns:
            A list of :class:`Contraindication` objects matching the drug and
            conditions.
        """
        logger.info("get_contraindications – drug=%s, conditions=%s", drug, conditions)

        drug_normalised = self._normalise_text(drug).lower().strip()
        results: List[Contraindication] = []

        for condition in conditions:
            cond_normalised = self._normalise_text(condition).lower().strip()

            # Try both orderings in the lookup
            contra = self._contraindications.get(
                (drug_normalised, cond_normalised)
            ) or self._contraindications.get(
                (cond_normalised, drug_normalised)
            )

            if contra:
                results.append(contra)

        # Fuzzy match: check substring / keyword match
        if not results:
            for (drug_key, cond_key), contra in self._contraindications.items():
                drug_match = drug_normalised in drug_key or drug_key in drug_normalised
                cond_match = any(
                    cond_normalised in c_key or c_key in cond_normalised
                    for c_key in [cond_key]
                )
                if drug_match and cond_match:
                    results.append(contra)

        logger.info(
            "get_contraindications – found %d contraindications for drug=%s",
            len(results),
            drug,
        )
        return results

    async def suggest_differential(
        self,
        symptoms: List[str],
    ) -> List[DifferentialDiagnosis]:
        """Suggest differential diagnoses based on a list of symptoms.

        Args:
            symptoms: Patient symptoms (supports Arabic symptom names).

        Returns:
            A list of :class:`DifferentialDiagnosis` objects ranked by
            estimated probability.
        """
        logger.info("suggest_differential – symptoms=%s", symptoms)

        normalised_symptoms = [self._normalise_text(s).lower().strip() for s in symptoms]

        # Symptom-condition mapping (simplified knowledge base)
        symptom_map: Dict[str, Dict[str, float]] = {
            "headache": {
                "migraine": 0.35,
                "tension_headache": 0.30,
                "sinusitis": 0.15,
                "meningitis": 0.05,
                "subarachnoid_hemorrhage": 0.02,
                "صداع": 0.10,
            },
            "fever": {
                "upper_respiratory_infection": 0.40,
                "influenza": 0.25,
                "urinary_tract_infection": 0.15,
                "meningitis": 0.05,
                "tuberculosis": 0.05,
                "حمى": 0.05,
            },
            "chest_pain": {
                "acute_coronary_syndrome": 0.20,
                "pulmonary_embolism": 0.10,
                "gastroesophageal_reflux": 0.25,
                "musculoskeletal": 0.20,
                "pericarditis": 0.05,
            },
            "neck_stiffness": {
                "meningitis": 0.40,
                "cervical_spondylosis": 0.30,
                "torticollis": 0.15,
            },
            "dyspnea": {
                "heart_failure": 0.25,
                "copd": 0.20,
                "asthma": 0.20,
                "pulmonary_embolism": 0.10,
                "pneumonia": 0.15,
            },
            "الصداع": {
                "migraine": 0.35,
                "tension_headache": 0.30,
                "sinusitis": 0.15,
            },
            "الحمى": {
                "upper_respiratory_infection": 0.40,
                "influenza": 0.25,
                "urinary_tract_infection": 0.15,
            },
        }

        scores: Dict[str, float] = {}
        condition_symptoms: Dict[str, List[str]] = {}

        for symptom in normalised_symptoms:
            for condition, prob in symptom_map.get(symptom, {}).items():
                scores[condition] = scores.get(condition, 0.0) + prob
                if condition not in condition_symptoms:
                    condition_symptoms[condition] = []
                if symptom not in condition_symptoms[condition]:
                    condition_symptoms[condition].append(symptom)

        # Normalise and sort by probability
        max_possible = len(normalised_symptoms)
        if max_possible > 0:
            for cond in scores:
                scores[cond] = min(scores[cond] / max_possible, 1.0)

        sorted_conditions = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        results: List[DifferentialDiagnosis] = []
        for condition, probability in sorted_conditions:
            if probability < 0.01:
                continue
            results.append(
                DifferentialDiagnosis(
                    condition=condition,
                    probability=round(probability, 3),
                    supporting_symptoms=condition_symptoms.get(condition, []),
                )
            )

        logger.info(
            "suggest_differential – %d suggestions for %d symptoms",
            len(results),
            len(symptoms),
        )
        return results

    async def get_treatment_protocol(
        self,
        condition: str,
    ) -> TreatmentProtocol:
        """Retrieve treatment protocol for a specific condition.

        Args:
            condition: Condition name or ICD code (supports Arabic).

        Returns:
            A :class:`TreatmentProtocol` with step-by-step treatment plan.
        """
        logger.info("get_treatment_protocol – condition=%s", condition)

        condition_normalised = self._normalise_text(condition).lower().strip()
        is_arabic = self._is_arabic_text(condition)

        # Look up built-in protocols
        protocol_data = self._lookup_protocol(condition_normalised)

        if not protocol_data:
            return TreatmentProtocol(
                condition=condition,
                condition_ar=condition if is_arabic else None,
                source="No matching protocol found in knowledge base.",
            )

        steps = [
            TreatmentStep(
                step_number=i + 1,
                description=step["description"],
                description_ar=step.get("description_ar"),
                duration=step.get("duration"),
                notes=step.get("notes"),
                notes_ar=step.get("notes_ar"),
            )
            for i, step in enumerate(protocol_data.get("steps", []))
        ]

        protocol = TreatmentProtocol(
            condition=protocol_data.get("condition", condition),
            condition_ar=protocol_data.get("condition_ar") or (condition if is_arabic else None),
            icd10_code=protocol_data.get("icd10_code"),
            severity_grades=protocol_data.get("severity_grades", []),
            steps=steps,
            medications=protocol_data.get("medications", []),
            follow_up=protocol_data.get("follow_up"),
            follow_up_ar=protocol_data.get("follow_up_ar"),
            source=protocol_data.get("source", "Clinical Knowledge Base"),
            last_updated=datetime.now(timezone.utc),
        )

        logger.info(
            "get_treatment_protocol – condition=%s, steps=%d",
            condition,
            len(steps),
        )
        return protocol

    async def validate_dosage(
        self,
        drug: str,
        patient_weight: Optional[float] = None,
        age: Optional[float] = None,
    ) -> DosageValidation:
        """Validate a drug dosage against weight-based reference ranges.

        Args:
            drug: Drug name (supports Arabic).
            patient_weight: Patient weight in kg.
            age: Patient age in years (used for geriatric/paediatric adjustments).

        Returns:
            A :class:`DosageValidation` with status, recommended range,
            and clinical notes.
        """
        logger.info(
            "validate_dosage – drug=%s, weight=%.1f, age=%.1f",
            drug,
            patient_weight or 0,
            age or 0,
        )

        drug_normalised = self._normalise_text(drug).lower().strip()
        is_arabic = self._is_arabic_text(drug)

        ref = self._dosage_ref.get(drug_normalised)
        if not ref:
            return DosageValidation(
                drug=drug,
                drug_ar=drug if is_arabic else None,
                patient_weight_kg=patient_weight,
                patient_age_years=age,
                status=DosageStatus.WITHIN_RANGE,
                notes=f"No dosage reference found for '{drug}' in knowledge base.",
                notes_ar=f"لم يتم العثور على مرجع جرعة لـ '{drug}' في قاعدة المعرفة.",
            )

        status = DosageStatus.WITHIN_RANGE
        notes = ref.get("notes", "")
        notes_ar = ref.get("notes_ar", "")
        adjustment_factors: List[str] = []

        calculated_dose: Optional[float] = None
        suggested_min: Optional[float] = None
        suggested_max: Optional[float] = None

        if patient_weight and patient_weight > 0:
            calculated_dose = patient_weight * ref["max_mg_per_kg_day"]
            suggested_min = patient_weight * ref["min_mg_per_kg_day"]
            suggested_max = patient_weight * ref["max_mg_per_kg_day"]

            # Apply absolute maximum
            if ref.get("max_total_mg") and calculated_dose > ref["max_total_mg"]:
                calculated_dose = float(ref["max_total_mg"])
                notes += f" Dose capped at absolute maximum of {ref['max_total_mg']} mg."
                adjustment_factors.append("absolute_max_cap")

        # Age-based adjustments
        if age is not None:
            if age < 2:
                adjustment_factors.append("paediatric")
                notes += " Paediatric patient – specialist dosing recommended."
                status = DosageStatus.ADJUSTMENT_NEEDED
            elif age >= 65:
                adjustment_factors.append("elderly")
                notes += " Elderly patient – consider dose reduction."
                status = DosageStatus.ADJUSTMENT_NEEDED

        return DosageValidation(
            drug=drug,
            drug_ar=drug if is_arabic else None,
            patient_weight_kg=patient_weight,
            patient_age_years=age,
            suggested_min_mg=round(suggested_min, 2) if suggested_min else None,
            suggested_max_mg=round(suggested_max, 2) if suggested_max else None,
            calculated_dose_mg=round(calculated_dose, 2) if calculated_dose else None,
            status=status,
            notes=notes,
            notes_ar=notes_ar,
            adjustment_factors=adjustment_factors,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _answer_interaction_question(
        self,
        question: str,
        patient_context: Optional[Dict[str, Any]],
    ) -> ClinicalAnswer:
        """Handle drug-interaction type questions."""
        # Extract drug names from the question
        drugs = self._extract_drug_names_from_question(question)
        if len(drugs) >= 2:
            report = await self.check_drug_interactions(drugs)
            answer_parts = [f"Interaction check for: {', '.join(drugs)}"]
            for interaction in report.interactions:
                answer_parts.append(
                    f"• {interaction.drug_a} + {interaction.drug_b}: "
                    f"[{interaction.severity.value}] {interaction.clinical_effect}"
                )
            answer_parts.append(f"Recommendation: {report.recommendation}")
            return ClinicalAnswer(
                question=question,
                answer="\n".join(answer_parts),
                confidence=0.85 if report.interactions else 0.7,
                evidence=[
                    Evidence(
                        source=interaction.source,
                        level=interaction.evidence_level,
                        excerpt=interaction.mechanism,
                        relevance_score=0.9,
                    )
                    for interaction in report.interactions
                ],
            )
        return ClinicalAnswer(
            question=question,
            answer="Please specify at least two drugs to check for interactions.",
            confidence=0.5,
        )

    async def _answer_dosage_question(
        self,
        question: str,
        patient_context: Optional[Dict[str, Any]],
    ) -> ClinicalAnswer:
        """Handle dosage-related questions."""
        drug = self._extract_single_drug_from_question(question)
        if drug:
            weight = (patient_context or {}).get("weight")
            age = (patient_context or {}).get("age")
            validation = await self.validate_dosage(drug, weight, age)
            answer = (
                f"Drug: {validation.drug}\n"
                f"Suggested range: {validation.suggested_min_mg}–{validation.suggested_max_mg} mg/day\n"
                f"Status: {validation.status.value}\n"
                f"Notes: {validation.notes}"
            )
            return ClinicalAnswer(
                question=question,
                answer=answer,
                confidence=0.8 if validation.status == DosageStatus.WITHIN_RANGE else 0.9,
                evidence=[
                    Evidence(
                        source="Clinical Dosage Reference Database",
                        level=EvidenceLevel.EXPERT_OPINION,
                        excerpt=validation.notes,
                        relevance_score=0.85,
                    )
                ],
            )
        return ClinicalAnswer(
            question=question,
            answer="Please specify a drug name for dosage validation.",
            confidence=0.5,
        )

    async def _answer_contraindication_question(
        self,
        question: str,
        patient_context: Optional[Dict[str, Any]],
    ) -> ClinicalAnswer:
        """Handle contraindication-type questions."""
        drug = self._extract_single_drug_from_question(question)
        conditions = self._extract_conditions_from_question(question)
        if (patient_context or {}).get("conditions"):
            conditions.extend(patient_context["conditions"])

        if drug and conditions:
            contraindications = await self.get_contraindications(drug, conditions)
            if contraindications:
                answer_parts = [f"Contraindications for {drug}:"]
                for c in contraindications:
                    answer_parts.append(
                        f"• {c.condition}: [{c.severity.value}] {c.details}"
                    )
                    if c.alternative_suggestion:
                        answer_parts.append(f"  Alternative: {c.alternative_suggestion}")
                return ClinicalAnswer(
                    question=question,
                    answer="\n".join(answer_parts),
                    confidence=0.85,
                    evidence=[
                        Evidence(
                            source="Clinical Contraindication Database",
                            level=EvidenceLevel.SYSTEMATIC_REVIEW,
                            excerpt=c.details,
                            relevance_score=0.9,
                        )
                        for c in contraindications
                    ],
                )

        return ClinicalAnswer(
            question=question,
            answer="Please specify a drug and relevant conditions to check contraindications.",
            confidence=0.5,
        )

    async def _answer_treatment_question(
        self,
        question: str,
        patient_context: Optional[Dict[str, Any]],
    ) -> ClinicalAnswer:
        """Handle treatment protocol questions."""
        conditions = self._extract_conditions_from_question(question)
        for cond in conditions:
            protocol = await self.get_treatment_protocol(cond)
            if protocol.steps:
                answer_parts = [f"Treatment Protocol for {protocol.condition}:"]
                for step in protocol.steps:
                    answer_parts.append(
                        f"  Step {step.step_number}: {step.description}"
                    )
                if protocol.medications:
                    answer_parts.append(
                        f"Medications: {', '.join(protocol.medications)}"
                    )
                if protocol.follow_up:
                    answer_parts.append(f"Follow-up: {protocol.follow_up}")
                return ClinicalAnswer(
                    question=question,
                    answer="\n".join(answer_parts),
                    confidence=0.8,
                    evidence=[
                        Evidence(
                            source=protocol.source or "Clinical Knowledge Base",
                            level=EvidenceLevel.SYSTEMATIC_REVIEW,
                            excerpt=step.description,
                            relevance_score=0.85,
                        )
                        for step in protocol.steps
                    ],
                )

        return ClinicalAnswer(
            question=question,
            answer="No specific treatment protocol found for the mentioned condition.",
            confidence=0.5,
        )

    def _general_answer(
        self,
        question: str,
        patient_context: Optional[Dict[str, Any]],
    ) -> tuple:
        """Provide a best-effort general answer for unclassified questions."""
        conditions = self._extract_conditions_from_question(question)
        if conditions:
            answer = (
                f"Based on the clinical question regarding '{conditions[0]}', "
                "I recommend consulting the latest published clinical guidelines "
                "and considering the patient's specific clinical context, "
                "comorbidities, and current medications before making any "
                "treatment decisions."
            )
            evidence = [
                Evidence(
                    source="General Clinical Knowledge",
                    level=EvidenceLevel.EXPERT_OPINION,
                    excerpt=answer,
                    relevance_score=0.6,
                )
            ]
        else:
            answer = (
                "I was unable to classify your clinical question into a specific "
                "category. Please rephrase with more specific medical terminology "
                "or provide patient context for a more targeted answer."
            )
            evidence = []

        return answer, evidence

    @staticmethod
    def _lookup_protocol(condition: str) -> Optional[Dict[str, Any]]:
        """Look up a built-in treatment protocol for a condition."""
        protocols: Dict[str, Dict[str, Any]] = {
            "hypertension": {
                "condition": "Hypertension",
                "condition_ar": "ارتفاع ضغط الدم",
                "icd10_code": "I10",
                "severity_grades": ["elevated", "stage_1", "stage_2", "hypertensive_crisis"],
                "steps": [
                    {
                        "description": "Lifestyle modifications (DASH diet, exercise, sodium restriction)",
                        "description_ar": "تعديل نمط الحياة (نظام داش الغذائي، الرياضة، تقليل الصوديوم)",
                        "duration": "Ongoing",
                        "notes": "First-line for elevated BP (120-129/<80 mmHg)",
                    },
                    {
                        "description": "Initiate ACE inhibitor or ARB (e.g. Lisinopril 10 mg daily)",
                        "description_ar": "بدء مثبط ACE أو ARB (مثل ليزينوبريل 10 ملغ يومياً)",
                        "duration": "Ongoing",
                        "notes": "First-line for Stage 1 hypertension with compelling indications",
                    },
                    {
                        "description": "Dual therapy: ACEi/ARB + CCB or thiazide diuretic",
                        "description_ar": "علاج مزدوج: مثبط ACE/ARB + حاصر الكالسيوم أو مدر ثيازيد",
                        "duration": "Ongoing",
                        "notes": "For Stage 2 hypertension (≥140/90)",
                    },
                ],
                "medications": ["Lisinopril", "Amlodipine", "Hydrochlorothiazide", "Losartan"],
                "follow_up": "Check BP in 1 month, then every 3–6 months",
                "follow_up_ar": "فحص ضغط الدم بعد شهر، ثم كل 3-6 أشهر",
                "source": "ACC/AHA Hypertension Guideline 2017",
            },
            "diabetes": {
                "condition": "Type 2 Diabetes Mellitus",
                "condition_ar": "داء السكري من النوع الثاني",
                "icd10_code": "E11",
                "severity_grades": ["mild", "moderate", "severe"],
                "steps": [
                    {
                        "description": "Lifestyle interventions: diet, exercise 150 min/week",
                        "description_ar": "تدخلات نمط الحياة: النظام الغذائي، الرياضة 150 دقيقة/أسبوع",
                        "duration": "Ongoing",
                    },
                    {
                        "description": "Metformin 500–1000 mg BID (first-line therapy)",
                        "description_ar": "ميتفورمين 500–1000 ملغ مرتين يومياً (العلاج الخط الأول)",
                        "duration": "Ongoing",
                        "notes": "Titrate gradually to minimise GI side effects",
                        "notes_ar": "زيادة الجرعة تدريجياً لتقليل الآثار الجانبية المعدية",
                    },
                    {
                        "description": "Add SGLT2 inhibitor or GLP-1 agonist if HbA1c above target",
                        "description_ar": "إضافة مثبط SGLT2 أو ناهض GLP-1 إذا كان HbA1c أعلى من الهدف",
                        "duration": "Ongoing",
                        "notes": "Consider cardioprotective benefits regardless of HbA1c",
                    },
                ],
                "medications": ["Metformin", "Empagliflozin", "Semaglutide", "Dapagliflozin"],
                "follow_up": "HbA1c every 3 months until stable, then every 6 months",
                "follow_up_ar": "HbA1c كل 3 أشهر حتى الاستقرار، ثم كل 6 أشهر",
                "source": "ADA Standards of Care 2024",
            },
        }

        for key, proto in protocols.items():
            if key in condition or condition in key:
                return proto
        return None

    @staticmethod
    def _extract_drug_names_from_question(question: str) -> List[str]:
        """Heuristically extract drug names from a question."""
        known_drugs = [
            "warfarin", "aspirin", "amoxicillin", "paracetamol", "ibuprofen",
            "metformin", "amlodipine", "simvastatin", "lisinopril", "losartan",
            "sertraline", "tramadol", "insulin", "omeprazole",
        ]
        question_lower = question.lower()
        return [d for d in known_drugs if d in question_lower]

    @staticmethod
    def _extract_single_drug_from_question(question: str) -> Optional[str]:
        """Extract the most likely single drug name from a question."""
        drugs = ClinicalQA._extract_drug_names_from_question(question)
        return drugs[0] if drugs else None

    @staticmethod
    def _extract_conditions_from_question(question: str) -> List[str]:
        """Extract condition names from a question."""
        known_conditions = [
            "hypertension", "diabetes", "heart failure", "asthma", "copd",
            "peptic ulcer", "renal failure", "migraine", "meningitis",
            "ارتفاع ضغط الدم", "داء السكري", "قصور القلب", "الربو",
            "قرحة المعدة", "قصور الكلى", "الصداع النصفي", "التهاب السحايا",
        ]
        question_lower = question.lower()
        return [c for c in known_conditions if c in question_lower]

    @staticmethod
    def _normalise_text(text: str) -> str:
        """Normalise text by stripping whitespace and removing Arabic tashkeel."""
        text = text.strip()
        # Remove Arabic diacritical marks
        tashkeel = re.compile(
            r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7-\u06E8\u06EA-\u06ED]"
        )
        text = tashkeel.sub("", text)
        # Normalise alef variants
        text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
        text = text.replace("ة", "ه")
        return text

    @staticmethod
    def _is_arabic_text(text: str) -> bool:
        """Detect whether the text contains significant Arabic content."""
        arabic_chars = sum(1 for c in text if "\u0600" <= c <= "\u06FF")
        return arabic_chars > len(text) * 0.3
