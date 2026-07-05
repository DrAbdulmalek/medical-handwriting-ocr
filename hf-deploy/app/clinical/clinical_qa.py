"""
Clinical Question Answering Engine (HF Spaces Standalone Edition).

Provides the ``ClinicalQA`` class for evidence-based medical question answering,
drug interaction checking, contraindication warnings, differential diagnosis
suggestions, treatment protocol recommendations, and dosage validation.

This version removes dependencies on ``app.config.settings`` and ``app.database``
to work standalone on HuggingFace Spaces.
"""

import logging
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic data models
# ---------------------------------------------------------------------------


class EvidenceLevel(str, Enum):
    SYSTEMATIC_REVIEW = "systematic_review"
    RANDOMISED_TRIAL = "randomised_trial"
    COHORT_STUDY = "cohort_study"
    CASE_CONTROL = "case_control"
    EXPERT_OPINION = "expert_opinion"
    CLINICAL_EXPERIENCE = "clinical_experience"


class SeverityLevel(str, Enum):
    CONTRAINDICATED = "contraindicated"
    MAJOR = "major"
    MODERATE = "moderate"
    MINOR = "minor"


class DosageStatus(str, Enum):
    WITHIN_RANGE = "within_range"
    BELOW_MINIMUM = "below_minimum"
    ABOVE_MAXIMUM = "above_maximum"
    ADJUSTMENT_NEEDED = "adjustment_needed"
    CONTRAINDICATED = "contraindicated"


class Evidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: str(uuid4()))
    source: str
    source_url: Optional[str] = None
    level: EvidenceLevel = Field(default=EvidenceLevel.EXPERT_OPINION)
    excerpt: str
    excerpt_ar: Optional[str] = None
    publication_year: Optional[int] = None
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)


class ClinicalAnswer(BaseModel):
    answer_id: str = Field(default_factory=lambda: str(uuid4()))
    question: str
    answer: str
    answer_ar: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: List[Evidence] = Field(default_factory=list)
    related_conditions: List[str] = Field(default_factory=list)
    disclaimer: str = Field(
        default="This information is for clinical decision support only and "
        "does not replace professional medical judgment.",
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class InteractionReport(BaseModel):
    report_id: str = Field(default_factory=lambda: str(uuid4()))
    drug_list: List[str]
    interactions: List["DrugInteraction"] = Field(default_factory=list)
    severity_summary: SeverityLevel = Field(default=SeverityLevel.MINOR)
    recommendation: str = ""
    recommendation_ar: Optional[str] = None
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DrugInteraction(BaseModel):
    drug_a: str
    drug_b: str
    severity: SeverityLevel = SeverityLevel.MODERATE
    mechanism: str = ""
    mechanism_ar: Optional[str] = None
    clinical_effect: str = ""
    management: str = ""
    evidence_level: EvidenceLevel = Field(default=EvidenceLevel.EXPERT_OPINION)
    source: str = ""


class Contraindication(BaseModel):
    contraindication_id: str = Field(default_factory=lambda: str(uuid4()))
    drug: str
    condition: str
    severity: SeverityLevel = SeverityLevel.MAJOR
    details: str = ""
    details_ar: Optional[str] = None
    alternative_suggestion: Optional[str] = None
    alternative_suggestion_ar: Optional[str] = None
    evidence: List[Evidence] = Field(default_factory=list)


class DifferentialDiagnosis(BaseModel):
    diagnosis_id: str = Field(default_factory=lambda: str(uuid4()))
    condition: str
    condition_ar: Optional[str] = None
    probability: float = Field(default=0.0, ge=0.0, le=1.0)
    supporting_symptoms: List[str] = Field(default_factory=list)
    supporting_symptoms_ar: Optional[List[str]] = None
    distinguishing_features: List[str] = Field(default_factory=list)
    distinguishing_features_ar: Optional[List[str]] = None
    recommended_tests: List[str] = Field(default_factory=list)
    icd10_code: Optional[str] = None


class TreatmentStep(BaseModel):
    step_number: int
    description: str
    description_ar: Optional[str] = None
    duration: Optional[str] = None
    notes: Optional[str] = None
    notes_ar: Optional[str] = None


class TreatmentProtocol(BaseModel):
    protocol_id: str = Field(default_factory=lambda: str(uuid4()))
    condition: str
    condition_ar: Optional[str] = None
    icd10_code: Optional[str] = None
    severity_grades: List[str] = Field(default_factory=list)
    steps: List[TreatmentStep] = Field(default_factory=list)
    medications: List[str] = Field(default_factory=list)
    follow_up: Optional[str] = None
    follow_up_ar: Optional[str] = None
    source: Optional[str] = None
    last_updated: Optional[datetime] = None


class DosageValidation(BaseModel):
    validation_id: str = Field(default_factory=lambda: str(uuid4()))
    drug: str
    drug_ar: Optional[str] = None
    patient_weight_kg: Optional[float] = None
    patient_age_years: Optional[float] = None
    suggested_min_mg: Optional[float] = None
    suggested_max_mg: Optional[float] = None
    calculated_dose_mg: Optional[float] = None
    status: DosageStatus = DosageStatus.WITHIN_RANGE
    notes: str = ""
    notes_ar: Optional[str] = None
    adjustment_factors: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Inline knowledge base
# ---------------------------------------------------------------------------

_DRUG_INTERACTIONS: Dict[Tuple[str, str], DrugInteraction] = {
    ("warfarin", "aspirin"): DrugInteraction(
        drug_a="Warfarin", drug_b="Aspirin",
        severity=SeverityLevel.MAJOR,
        mechanism="Antiplatelet + anticoagulant synergism increases bleeding risk.",
        clinical_effect="Increased risk of gastrointestinal and intracranial bleeding.",
        management="Avoid combination. If unavoidable, monitor INR closely.",
        evidence_level=EvidenceLevel.SYSTEMATIC_REVIEW,
        source="ACC/AHA Guideline 2023",
    ),
    ("metformin", "contrast_dye"): DrugInteraction(
        drug_a="Metformin", drug_b="Iodinated Contrast Dye",
        severity=SeverityLevel.MAJOR,
        mechanism="Contrast-induced nephropathy can precipitate lactic acidosis.",
        clinical_effect="Risk of lactic acidosis in patients with renal impairment.",
        management="Hold metformin 48 h before and after contrast study; check eGFR.",
        evidence_level=EvidenceLevel.EXPERT_OPINION,
        source="ESUR Contrast Media Guidelines 2023",
    ),
    ("ssri", "tramadol"): DrugInteraction(
        drug_a="SSRI (e.g. Sertraline)", drug_b="Tramadol",
        severity=SeverityLevel.MAJOR,
        mechanism="Serotonin syndrome risk – both increase CNS serotonin.",
        clinical_effect="Serotonin syndrome: agitation, hyperthermia, rigidity.",
        management="Avoid combination. Use alternative analgesics.",
        evidence_level=EvidenceLevel.COHORT_STUDY,
        source="FDA Drug Safety Communication",
    ),
    ("amlodipine", "simvastatin"): DrugInteraction(
        drug_a="Amlodipine", drug_b="Simvastatin",
        severity=SeverityLevel.MODERATE,
        mechanism="CYP3A4 inhibition increases simvastatin levels.",
        clinical_effect="Increased risk of myopathy and rhabdomyolysis.",
        management="Limit simvastatin to 20 mg/day when used with amlodipine.",
        evidence_level=EvidenceLevel.RANDOMISED_TRIAL,
        source="MHRA Drug Safety Update",
    ),
}

_CONTRAINDICATIONS: Dict[Tuple[str, str], Contraindication] = {
    ("nsaid", "peptic_ulcer"): Contraindication(
        drug="NSAIDs", condition="Active Peptic Ulcer Disease",
        severity=SeverityLevel.CONTRAINDICATED,
        details="NSAIDs increase gastric mucosal injury and bleeding risk.",
        details_ar="تزيد مضادات الالتهاب غير الستيرويدية من خطر إصابة الغشاء المخاطي المعدي والنزيف.",
        alternative_suggestion="Use acetaminophen or a COX-2 selective inhibitor with PPI co-therapy.",
    ),
    ("metformin", "renal_failure"): Contraindication(
        drug="Metformin", condition="Severe Renal Failure (eGFR < 30)",
        severity=SeverityLevel.CONTRAINDICATED,
        details="Risk of lactic acidosis in advanced renal impairment.",
        details_ar="خطر الحماض اللبني في حالات القصور الكلوي المتقدم.",
        alternative_suggestion="Switch to insulin or sulfonylurea.",
    ),
    ("beta_blocker", "asthma"): Contraindication(
        drug="Non-selective Beta Blockers (e.g. Propranolol)",
        condition="Asthma / Severe COPD",
        severity=SeverityLevel.MAJOR,
        details="Can cause bronchoconstriction by blocking β2 receptors.",
        details_ar="يمكن أن يسبب تضيق القصبات عن طريق حصار مستقبلات بيتا 2.",
        alternative_suggestion="Use cardioselective beta-blocker (e.g. Bisoprolol) with caution.",
    ),
}

_DOSAGE_REFERENCE: Dict[str, Dict[str, Any]] = {
    "amoxicillin": {
        "min_mg_per_kg_day": 25, "max_mg_per_kg_day": 50, "max_total_mg": 1500,
        "frequency": "8-hourly",
        "notes": "Adjust for renal impairment.",
        "notes_ar": "تعديل الجرعة في حالة ضعف الكلى.",
    },
    "paracetamol": {
        "min_mg_per_kg_day": 10, "max_mg_per_kg_day": 15, "max_total_mg": 4000,
        "frequency": "4–6-hourly",
        "notes": "Max 4 doses per 24 h.",
        "notes_ar": "الحد الأقصى 4 جرعات في 24 ساعة.",
    },
    "ibuprofen": {
        "min_mg_per_kg_day": 5, "max_mg_per_kg_day": 10, "max_total_mg": 1200,
        "frequency": "6–8-hourly",
        "notes": "Take with food. Avoid in renal impairment.",
        "notes_ar": "تناول مع الطعام. تجنب في حالات ضعف الكلى.",
    },
}


# ---------------------------------------------------------------------------
# ClinicalQA
# ---------------------------------------------------------------------------


class ClinicalQA:
    """Evidence-based clinical question answering and decision support (standalone)."""

    def __init__(self) -> None:
        self._interactions = _DRUG_INTERACTIONS
        self._contraindications = _CONTRAINDICATIONS
        self._dosage_ref = _DOSAGE_REFERENCE
        logger.info("ClinicalQA initialised with %d known drug interactions", len(self._interactions))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ask_clinical_question(
        self, question: str, patient_context: Optional[Dict[str, Any]] = None,
    ) -> ClinicalAnswer:
        logger.info("ask_clinical_question – question='%s'", question[:120])

        try:
            question_normalised = self._normalise_text(question)
            is_arabic = self._is_arabic_text(question)
            question_lower = question_normalised.lower()

            drug_kw = ["interaction", "interact", "تداخل", "تفاعل"]
            if any(kw in question_lower for kw in drug_kw):
                return await self._answer_interaction_question(question, patient_context)

            dosage_kw = ["dose", "dosage", "جرعة", "مقدار"]
            if any(kw in question_lower for kw in dosage_kw):
                return await self._answer_dosage_question(question, patient_context)

            contra_kw = ["contraindication", "contraindicated", "موانع", "مضاد"]
            if any(kw in question_lower for kw in contra_kw):
                return await self._answer_contraindication_question(question, patient_context)

            treatment_kw = ["treatment", "treat", "manage", "علاج", "إدارة"]
            if any(kw in question_lower for kw in treatment_kw):
                return await self._answer_treatment_question(question, patient_context)

            answer_text, evidence = self._general_answer(question, patient_context)
            return ClinicalAnswer(
                question=question,
                answer=answer_text,
                answer_ar=answer_text if is_arabic else None,
                confidence=0.7 if evidence else 0.3,
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

    async def check_drug_interactions(self, drug_list: List[str]) -> InteractionReport:
        normalised = [self._normalise_text(d).lower().strip() for d in drug_list]
        interactions: List[DrugInteraction] = []
        severity_order = [SeverityLevel.CONTRAINDICATED, SeverityLevel.MAJOR, SeverityLevel.MODERATE, SeverityLevel.MINOR]

        for i, da in enumerate(normalised):
            for j, db in enumerate(normalised):
                if i >= j:
                    continue
                interaction = self._interactions.get((da, db)) or self._interactions.get((db, da))
                if interaction:
                    interactions.append(interaction)

        worst = SeverityLevel.MINOR
        if interactions:
            for sev in severity_order:
                if any(i.severity == sev for i in interactions):
                    worst = sev
                    break

        rec = "No significant interactions detected."
        if worst in (SeverityLevel.CONTRAINDICATED, SeverityLevel.MAJOR):
            rec = "MAJOR interactions detected. Review medication list and consult with a pharmacist."
        elif worst == SeverityLevel.MODERATE:
            rec = "Moderate interactions detected. Monitor patient and consider dose adjustments."

        return InteractionReport(
            drug_list=drug_list, interactions=interactions,
            severity_summary=worst, recommendation=rec,
        )

    async def get_contraindications(self, drug: str, conditions: List[str]) -> List[Contraindication]:
        drug_n = self._normalise_text(drug).lower().strip()
        results: List[Contraindication] = []

        for cond in conditions:
            cond_n = self._normalise_text(cond).lower().strip()
            c = self._contraindications.get((drug_n, cond_n)) or self._contraindications.get((cond_n, drug_n))
            if c:
                results.append(c)

        if not results:
            for (dk, ck), c in self._contraindications.items():
                if (drug_n in dk or dk in drug_n) and any(cond_n in ck or ck in cond_n for ck in [ck]):
                    results.append(c)

        return results

    async def suggest_differential(self, symptoms: List[str]) -> List[DifferentialDiagnosis]:
        normalised = [self._normalise_text(s).lower().strip() for s in symptoms]

        symptom_map: Dict[str, Dict[str, float]] = {
            "headache": {"migraine": 0.35, "tension_headache": 0.30, "sinusitis": 0.15, "meningitis": 0.05},
            "fever": {"upper_respiratory_infection": 0.40, "influenza": 0.25, "urinary_tract_infection": 0.15, "meningitis": 0.05},
            "chest_pain": {"acute_coronary_syndrome": 0.20, "pulmonary_embolism": 0.10, "gastroesophageal_reflux": 0.25},
            "neck_stiffness": {"meningitis": 0.40, "cervical_spondylosis": 0.30, "torticollis": 0.15},
            "dyspnea": {"heart_failure": 0.25, "copd": 0.20, "asthma": 0.20, "pneumonia": 0.15},
            "الصداع": {"migraine": 0.35, "tension_headache": 0.30, "sinusitis": 0.15},
            "الحمى": {"upper_respiratory_infection": 0.40, "influenza": 0.25, "urinary_tract_infection": 0.15},
        }

        scores: Dict[str, float] = {}
        cond_syms: Dict[str, List[str]] = {}
        for sym in normalised:
            for cond, prob in symptom_map.get(sym, {}).items():
                scores[cond] = scores.get(cond, 0.0) + prob
                cond_syms.setdefault(cond, []).append(sym)

        max_p = len(normalised) or 1
        for c in scores:
            scores[c] = min(scores[c] / max_p, 1.0)

        return [
            DifferentialDiagnosis(condition=cond, probability=round(prob, 3), supporting_symptoms=cond_syms.get(cond, []))
            for cond, prob in sorted(scores.items(), key=lambda x: x[1], reverse=True)
            if prob >= 0.01
        ]

    async def get_treatment_protocol(self, condition: str) -> TreatmentProtocol:
        condition_n = self._normalise_text(condition).lower().strip()
        is_arabic = self._is_arabic_text(condition)
        proto = self._lookup_protocol(condition_n)

        if not proto:
            return TreatmentProtocol(condition=condition, condition_ar=condition if is_arabic else None, source="No matching protocol found.")

        steps = [TreatmentStep(step_number=i + 1, description=s["description"], description_ar=s.get("description_ar"),
                         duration=s.get("duration"), notes=s.get("notes"), notes_ar=s.get("notes_ar"))
                 for i, s in enumerate(proto.get("steps", []))]

        return TreatmentProtocol(
            condition=proto.get("condition", condition),
            condition_ar=proto.get("condition_ar") or (condition if is_arabic else None),
            icd10_code=proto.get("icd10_code"),
            severity_grades=proto.get("severity_grades", []),
            steps=steps, medications=proto.get("medications", []),
            follow_up=proto.get("follow_up"), follow_up_ar=proto.get("follow_up_ar"),
            source=proto.get("source", "Clinical Knowledge Base"),
            last_updated=datetime.now(timezone.utc),
        )

    async def validate_dosage(self, drug: str, patient_weight: Optional[float] = None, age: Optional[float] = None) -> DosageValidation:
        drug_n = self._normalise_text(drug).lower().strip()
        is_arabic = self._is_arabic_text(drug)
        ref = self._dosage_ref.get(drug_n)

        if not ref:
            return DosageValidation(drug=drug, drug_ar=drug if is_arabic else None,
                                     patient_weight_kg=patient_weight, patient_age_years=age,
                                     notes=f"No dosage reference found for '{drug}'.")

        status = DosageStatus.WITHIN_RANGE
        notes = ref.get("notes", "")
        notes_ar = ref.get("notes_ar", "")
        adj: List[str] = []
        calc = sug_min = sug_max = None

        if patient_weight and patient_weight > 0:
            calc = patient_weight * ref["max_mg_per_kg_day"]
            sug_min = patient_weight * ref["min_mg_per_kg_day"]
            sug_max = patient_weight * ref["max_mg_per_kg_day"]
            if ref.get("max_total_mg") and calc > ref["max_total_mg"]:
                calc = float(ref["max_total_mg"])
                adj.append("absolute_max_cap")

        if age is not None:
            if age < 2:
                adj.append("paediatric")
                status = DosageStatus.ADJUSTMENT_NEEDED
            elif age >= 65:
                adj.append("elderly")
                status = DosageStatus.ADJUSTMENT_NEEDED

        return DosageValidation(drug=drug, drug_ar=drug if is_arabic else None,
                                 patient_weight_kg=patient_weight, patient_age_years=age,
                                 suggested_min_mg=round(sug_min, 2) if sug_min else None,
                                 suggested_max_mg=round(sug_max, 2) if sug_max else None,
                                 calculated_dose_mg=round(calc, 2) if calc else None,
                                 status=status, notes=notes, notes_ar=notes_ar, adjustment_factors=adj)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _answer_interaction_question(self, question: str, ctx: Optional[Dict]) -> ClinicalAnswer:
        drugs = self._extract_drug_names(question)
        if len(drugs) >= 2:
            report = await self.check_drug_interactions(drugs)
            parts = [f"Interaction check for: {', '.join(drugs)}"]
            for i in report.interactions:
                parts.append(f"• {i.drug_a} + {i.drug_b}: [{i.severity.value}] {i.clinical_effect}")
            parts.append(f"Recommendation: {report.recommendation}")
            return ClinicalAnswer(question=question, answer="\n".join(parts),
                                 confidence=0.85 if report.interactions else 0.7)
        return ClinicalAnswer(question=question, answer="Please specify at least two drugs.", confidence=0.5)

    async def _answer_dosage_question(self, question: str, ctx: Optional[Dict]) -> ClinicalAnswer:
        drug = self._extract_single_drug(question)
        if drug:
            w = (ctx or {}).get("weight")
            a = (ctx or {}).get("age")
            v = await self.validate_dosage(drug, w, a)
            return ClinicalAnswer(question=question,
                                 answer=f"Drug: {v.drug}\nRange: {v.suggested_min_mg}–{v.suggested_max_mg} mg/day\nStatus: {v.status.value}\nNotes: {v.notes}",
                                 confidence=0.8)
        return ClinicalAnswer(question=question, answer="Please specify a drug name.", confidence=0.5)

    async def _answer_contraindication_question(self, question: str, ctx: Optional[Dict]) -> ClinicalAnswer:
        drug = self._extract_single_drug(question)
        conds = self._extract_conditions_from_question(question)
        if (ctx or {}).get("conditions"):
            conds.extend(ctx["conditions"])
        if drug and conds:
            cs = await self.get_contraindications(drug, conds)
            if cs:
                parts = [f"Contraindications for {drug}:"]
                for c in cs:
                    parts.append(f"• {c.condition}: [{c.severity.value}] {c.details}")
                    if c.alternative_suggestion:
                        parts.append(f"  Alternative: {c.alternative_suggestion}")
                return ClinicalAnswer(question=question, answer="\n".join(parts), confidence=0.85)
        return ClinicalQA(question=question, answer="Specify a drug and conditions.", confidence=0.5)

    async def _answer_treatment_question(self, question: str, ctx: Optional[Dict]) -> ClinicalAnswer:
        conds = self._extract_conditions_from_question(question)
        for c in conds:
            proto = await self.get_treatment_protocol(c)
            if proto.steps:
                parts = [f"Treatment Protocol for {proto.condition}:"]
                for s in proto.steps:
                    parts.append(f"  Step {s.step_number}: {s.description}")
                if proto.medications:
                    parts.append(f"Medications: {', '.join(proto.medications)}")
                return ClinicalQA(question=question, answer="\n".join(parts), confidence=0.8)
        return ClinicalQA(question=question, answer="No specific treatment protocol found.", confidence=0.5)

    def _general_answer(self, question: str, ctx: Optional[Dict]) -> tuple:
        conds = self._extract_conditions_from_question(question)
        if conds:
            a = (f"Based on the clinical question regarding '{conds[0]}', "
                 "consult the latest clinical guidelines considering patient context, "
                 "comorbidities, and current medications.")
            return a, [Evidence(source="General Clinical Knowledge", level=EvidenceLevel.EXPERT_OPINION, excerpt=a, relevance_score=0.6)]
        return "Unable to classify question. Please rephrase with medical terminology.", []

    @staticmethod
    def _lookup_protocol(condition: str) -> Optional[Dict[str, Any]]:
        protocols = {
            "hypertension": {
                "condition": "Hypertension", "condition_ar": "ارتفاع ضغط الدم", "icd10_code": "I10",
                "severity_grades": ["elevated", "stage_1", "stage_2"],
                "steps": [
                    {"description": "Lifestyle modifications (DASH diet, exercise, sodium restriction)",
                     "description_ar": "تعديل نمط الحياة (نظام داش الغذائي، الرياضة، تقليل الصوديوم)", "duration": "Ongoing"},
                    {"description": "Initiate ACE inhibitor or ARB (e.g. Lisinopril 10 mg daily)",
                     "description_ar": "بدء مثبط ACE أو ARB (مثل ليزينوبريل 10 ملغ يومياً)", "duration": "Ongoing"},
                    {"description": "Dual therapy: ACEi/ARB + CCB or thiazide diuretic",
                     "description_ar": "علاج مزدوج: مثبط ACE/ARB + حاصر الكالسيوم أو مدر ثيازيد", "duration": "Ongoing"},
                ],
                "medications": ["Lisinopril", "Amlodipine", "Hydrochlorothiazide"],
                "follow_up": "Check BP in 1 month, then every 3–6 months",
                "follow_up_ar": "فحص ضغط الدم بعد شهر، ثم كل 3-6 أشهر",
                "source": "ACC/AHA Hypertension Guideline 2017",
            },
            "diabetes": {
                "condition": "Type 2 Diabetes Mellitus", "condition_ar": "داء السكري من النوع الثاني", "icd10_code": "E11",
                "severity_grades": ["mild", "moderate", "severe"],
                "steps": [
                    {"description": "Lifestyle interventions: diet, exercise 150 min/week",
                     "description_ar": "تدخلات نمط الحياة: النظام الغذائي، الرياضة 150 دقيقة/أسبوع", "duration": "Ongoing"},
                    {"description": "Metformin 500–1000 mg BID (first-line therapy)",
                     "description_ar": "ميتفورمين 500–1000 ملغ مرتين يومياً (العلاج الخط الأول)", "duration": "Ongoing"},
                    {"description": "Add SGLT2 inhibitor or GLP-1 agonist if HbA1c above target",
                     "description_ar": "إضافة مثبط SGLT2 أو ناهض GLP-1 إذا كان HbA1c أعلى من الهدف", "duration": "Ongoing"},
                ],
                "medications": ["Metformin", "Empagliflozin", "Semaglutide"],
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
    def _extract_drug_names(question: str) -> List[str]:
        known = ["warfarin", "aspirin", "amoxicillin", "paracetamol", "ibuprofen",
                 "metformin", "amlodipine", "simvastatin", "lisinopril", "losartan",
                 "sertraline", "tramadol", "insulin", "omeprazole"]
        q = question.lower()
        return [d for d in known if d in q]

    @staticmethod
    def _extract_single_drug(question: str) -> Optional[str]:
        drugs = ClinicalQA._extract_drug_names(question)
        return drugs[0] if drugs else None

    @staticmethod
    def _extract_conditions_from_question(question: str) -> List[str]:
        known = ["hypertension", "diabetes", "heart failure", "asthma", "copd",
                 "peptic ulcer", "renal failure", "migraine", "meningitis",
                 "ارتفاع ضغط الدم", "داء السكري", "قصور القلب", "الربو",
                 "قرحة المعدة", "قصور الكلى", "الصداع النصفي", "التهاب السحايا"]
        q = question.lower()
        return [c for c in known if c in q]

    @staticmethod
    def _normalise_text(text: str) -> str:
        text = text.strip()
        tashkeel = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7-\u06E8\u06EA-\u06ED]")
        text = tashkeel.sub("", text)
        text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه")
        return text

    @staticmethod
    def _is_arabic_text(text: str) -> bool:
        arabic_chars = sum(1 for c in text if "\u0600" <= c <= "\u06FF")
        return arabic_chars > len(text) * 0.3
