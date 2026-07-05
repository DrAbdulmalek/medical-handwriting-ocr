"""
Structured Medical Data Extraction (Schema Extractor).

Parses free-form medical text — whether OCR output, clinical notes, or
prescriptions — into structured Pydantic models for vital signs,
medications, diagnoses, lab results, and patient demographics.

This is the HF Spaces standalone version — it removes the dependency
on ``app.config.settings`` and ``app.ai.llm_integration`` (no LLM fallback).
"""

import re
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Data Models
# =============================================================================


class VitalSigns(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    systolic_bp: Optional[float] = None
    diastolic_bp: Optional[float] = None
    heart_rate: Optional[int] = None
    temperature: Optional[float] = None
    spo2: Optional[float] = None
    respiratory_rate: Optional[int] = None
    source_text: str = ""
    extracted_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class Medication(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    route: Optional[str] = None
    duration: Optional[str] = None
    notes: Optional[str] = None
    source_text: str = ""


class Diagnosis(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    code: Optional[str] = None
    description: str
    severity: Optional[str] = None
    laterality: Optional[str] = None
    chronic: bool = False
    source_text: str = ""


class LabResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    test_name: str
    value: Optional[float] = None
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    is_abnormal: bool = False
    status: Optional[str] = None
    source_text: str = ""


class PatientInfo(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: Optional[str] = None
    age: Optional[str] = None
    gender: Optional[str] = None
    date_of_birth: Optional[str] = None
    patient_id: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    allergies: List[str] = Field(default_factory=list)
    source_text: str = ""


class MedicalDataExtract(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    vital_signs: VitalSigns = Field(default_factory=VitalSigns)
    medications: List[Medication] = Field(default_factory=list)
    diagnoses: List[Diagnosis] = Field(default_factory=list)
    lab_results: List[LabResult] = Field(default_factory=list)
    patient_info: PatientInfo = Field(default_factory=PatientInfo)
    extraction_method: str = "regex"
    confidence_scores: Dict[str, float] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    extracted_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# =============================================================================
# Regex Patterns — Vital Signs
# =============================================================================

_RE_BP = re.compile(
    r"(?:(?:BP|blood pressure|ضغط الدم|الضغط)[:\s]*)"
    r"\s*(\d{2,3})\s*/\s*(\d{2,3})\s*(?:mmHg|mm\s*Hg)?",
    re.IGNORECASE | re.UNICODE,
)

_RE_STANDALONE_BP = re.compile(
    r"(\d{2,3})\s*/\s*(\d{2,3})\s*(?:mmHg|mm\s*Hg)?",
    re.IGNORECASE,
)

_RE_HR = re.compile(
    r"(?:(?:HR|heart rate|pulse|النبض|معدل القلب|السرعة)[:\s]*)"
    r"\s*(\d{2,3})\s*(?:bpm|beats?/?min)?",
    re.IGNORECASE | re.UNICODE,
)

_RE_TEMP = re.compile(
    r"(?:(?:temp(?:erature)?|الحرارة|درجة الحرارة)[:\s]*)"
    r"\s*([\d.]+)\s*°?\s*(?:C|c|F|f)?",
    re.IGNORECASE | re.UNICODE,
)

_RE_SPO2 = re.compile(
    r"(?:(?:SpO2|oxygen sat|الاشباع|تشبع الأكسجين)[:\s]*)"
    r"\s*([\d.]+)\s*%?",
    re.IGNORECASE | re.UNICODE,
)

_RE_RR = re.compile(
    r"(?:(?:RR|respiratory rate|التنفس|معدل التنفس)[:\s]*)"
    r"\s*(\d{1,2})\s*(?:br?/?min|breaths?)?",
    re.IGNORECASE | re.UNICODE,
)


# =============================================================================
# Regex Patterns — Medications
# =============================================================================

_RE_MEDICATION = re.compile(
    r"([A-Za-z\u0600-\u06FF][\w\u0600-\u06FF\-]{1,40})"
    r"\s*"
    r"(?:"
    r"(\d+(?:\.\d+)?)\s*(?:mg|mcg|g|ml|IU|units?|وحدة|مل|ملغ)\b"
    r")?"
    r"\s*"
    r"(?:"
    r"((?:BID|TID|QID|QD|OD|PRN|SOS|once|twice|daily|weekly|"
    r"كل\s*\d+\s*(?:ساعة|ساعات|يوم|أيام)|"
    r"صباحا|مساء|bedtime|at night|"
    r"\d+x?/?day|1-0-1|1-1-1|1-0-0|0-1-0))"
    r")?"
    r"\s*"
    r"(?:"
    r"((?:PO|IV|IM|SC|SubQ|topical|inhaled|rectal|oral|"
    r"فم|وريد|عضل|تحت الجلد|موضعي))"
    r")?"
    r"\s*"
    r"(?:"
    r"((?:for\s+\d+\s*(?:days?|weeks?|months?)|"
    r"لمدة\s*\d+\s*(?:يوم|أيام|أسبوع|أسابيع|شهر|أشهر)))"
    r")?",
    re.IGNORECASE | re.UNICODE,
)


# =============================================================================
# Regex Patterns — Diagnoses
# =============================================================================

_RE_ICD = re.compile(r"\b([A-Z]\d{2}(?:\.\d{1,4})?)\b")

_RE_DIAGNOSIS_KEYWORD = re.compile(
    r"(?:(?:dx|diagnosis|diagnoses|تشخيص|المرض|الحالة)[:\s–-]+)"
    r"(.+?)(?:\n|$)",
    re.IGNORECASE | re.UNICODE,
)

_RE_CHRONIC_MARKER = re.compile(r"(?i)(?:chronic|مزمن|long[- ]standing|دائم)")


# =============================================================================
# Regex Patterns — Lab Results
# =============================================================================

_RE_LAB_RESULT = re.compile(
    r"([A-Za-z\u0600-\u06FF][\w\u0600-\u06FF\s\-]{1,50})"
    r"\s*[:\s]\s*"
    r"([\d.]+)"
    r"\s*(?:mg/dL|mmol/L|g/L|U/L|ng/mL|pg/mL|mg/L|µmol/L|%|"
    r"ملغ/ديسيلتر|ملمول/لتر)?\b"
    r"(?:\s*\(?([\d.\-~\s]+\s*(?:mg/dL|mmol/L|g/L|U/L|ng/mL|pg/mL|%))?\)?)?"
    r"(?:\s*(?:high|low|↑|↓|مرتفع|منخفض|crit|حرج))?",
    re.IGNORECASE | re.UNICODE,
)


# =============================================================================
# Regex Patterns — Patient Info
# =============================================================================

_RE_PATIENT_NAME = re.compile(
    r"(?:patient[:\s]|المريض[:\s]|الاسم[:\s]|name[:\s]+)"
    r"([A-Za-z\u0600-\u06FF][\w\u0600-\u06FF\s\-']{1,80})",
    re.IGNORECASE | re.UNICODE,
)

_RE_AGE = re.compile(
    r"(?:(?:age|العمر)[:\s]*)"
    r"([\d\u0660-\u0669]+)\s*(?:years?|year|months?|month|days?|day|"
    r"سنة|سنين|شهر|أشهر|يوم|أيام)?",
    re.IGNORECASE | re.UNICODE,
)

_RE_GENDER = re.compile(
    r"(?:gender|sex|الجنس|النوع)[:\s]*"
    r"(male|female|ذكر|أنثى|M|F)",
    re.IGNORECASE | re.UNICODE,
)

_RE_PATIENT_ID = re.compile(
    r"(?:patient\s*id|MRN|file\s*no|رقم\s*الملف|رقم\s*المريض|ملف)[:\s#]*"
    r"([\w\u0600-\u06FF\-]{1,30})",
    re.IGNORECASE | re.UNICODE,
)

_RE_ALLERGY = re.compile(
    r"(?:allergy|allergies|الحساسية|حساسية)[:\s]*"
    r"([^\n]+)",
    re.IGNORECASE | re.UNICODE,
)


# =============================================================================
# MedicalSchemaExtractor
# =============================================================================


class MedicalSchemaExtractor:
    """Extract structured medical data from free-form text using regex patterns."""

    def __init__(self, use_llm_fallback: bool = False):
        self.use_llm_fallback = use_llm_fallback  # Always False in HF Spaces
        logger.info("MedicalSchemaExtractor initialised (llm_fallback=False)")

    def extract_vital_signs(self, text: str) -> VitalSigns:
        vitals = VitalSigns(source_text=text)

        for pattern in (_RE_BP, _RE_STANDALONE_BP):
            match = pattern.search(text)
            if match:
                vitals.systolic_bp = float(match.group(1))
                vitals.diastolic_bp = float(match.group(2))
                vitals.source_text = match.group(0)
                break

        match = _RE_HR.search(text)
        if match:
            vitals.heart_rate = int(match.group(1))
            if not vitals.source_text:
                vitals.source_text = match.group(0)

        match = _RE_TEMP.search(text)
        if match:
            vitals.temperature = float(match.group(1))
            if not vitals.source_text:
                vitals.source_text = match.group(0)

        match = _RE_SPO2.search(text)
        if match:
            vitals.spo2 = float(match.group(1))
            if not vitals.source_text:
                vitals.source_text = match.group(0)

        match = _RE_RR.search(text)
        if match:
            vitals.respiratory_rate = int(match.group(1))
            if not vitals.source_text:
                vitals.source_text = match.group(0)

        return vitals

    def extract_medications(self, text: str) -> List[Medication]:
        medications: List[Medication] = []
        seen_names: set = set()

        for match in _RE_MEDICATION.finditer(text):
            name = match.group(1).strip()
            if not name or name.lower() in seen_names:
                continue
            seen_names.add(name.lower())

            medications.append(Medication(
                name=name,
                dosage=match.group(2).strip() if match.group(2) else None,
                frequency=match.group(3).strip() if match.group(3) else None,
                route=match.group(4).strip() if match.group(4) else None,
                duration=match.group(5).strip() if match.group(5) else None,
                source_text=match.group(0).strip(),
            ))

        return medications

    def extract_diagnoses(self, text: str) -> List[Diagnosis]:
        diagnoses: List[Diagnosis] = []

        for match in _RE_DIAGNOSIS_KEYWORD.finditer(text):
            desc = match.group(1).strip()
            if not desc:
                continue
            icd_match = _RE_ICD.search(desc)
            code = icd_match.group(1) if icd_match else None
            chronic = bool(_RE_CHRONIC_MARKER.search(desc))

            diagnoses.append(Diagnosis(
                code=code,
                description=desc,
                chronic=chronic,
                source_text=match.group(0).strip(),
            ))

        seen_codes = {d.code for d in diagnoses if d.code}
        for match in _RE_ICD.finditer(text):
            code = match.group(1)
            if code not in seen_codes:
                seen_codes.add(code)
                start = max(0, match.start() - 60)
                end = min(len(text), match.end() + 100)
                context = text[start:end].strip()
                diagnoses.append(Diagnosis(
                    code=code,
                    description=context,
                    source_text=match.group(0),
                ))

        return diagnoses

    def extract_lab_results(self, text: str) -> List[LabResult]:
        results: List[LabResult] = []
        seen_tests: set = set()

        for match in _RE_LAB_RESULT.finditer(text):
            test_name = match.group(1).strip()
            test_key = test_name.lower().strip()
            if not test_name or test_key in seen_tests:
                continue
            seen_tests.add(test_key)

            value = float(match.group(2)) if match.group(2) else None
            unit = match.group(3).strip() if match.group(3) else None
            ref_range = match.group(4).strip() if match.group(4) else None

            raw_after = text[match.end():match.end() + 30].lower()
            is_abnormal = any(
                flag in raw_after
                for flag in ("high", "low", "↑", "↓", "مرتفع", "منخفض", "crit", "حرج")
            )
            status = None
            if is_abnormal:
                status = "high" if any(f in raw_after for f in ("high", "↑", "مرتفع", "crit", "حرج")) else "low"

            if value is not None and ref_range:
                is_abnormal, status = self._check_reference(value, ref_range, status)

            results.append(LabResult(
                test_name=test_name,
                value=value,
                unit=unit,
                reference_range=ref_range,
                is_abnormal=is_abnormal,
                status=status,
                source_text=match.group(0).strip(),
            ))

        return results

    def extract_patient_info(self, text: str) -> PatientInfo:
        info = PatientInfo(source_text=text)

        match = _RE_PATIENT_NAME.search(text)
        if match:
            info.name = match.group(1).strip()
            info.source_text = match.group(0).strip()

        match = _RE_AGE.search(text)
        if match:
            info.age = self._arabic_numeral_to_int(match.group(1))

        match = _RE_GENDER.search(text)
        if match:
            raw = match.group(1).lower()
            if raw in ("male", "m", "ذكر"):
                info.gender = "male"
            elif raw in ("female", "f", "أنثى"):
                info.gender = "female"

        match = _RE_PATIENT_ID.search(text)
        if match:
            info.patient_id = match.group(1).strip()

        for match in _RE_ALLERGY.finditer(text):
            allergy_text = match.group(1).strip()
            if allergy_text and allergy_text.lower() not in ("none", "nka", "لا يوجد", "لا"):
                info.allergies.append(allergy_text)

        phone_match = re.search(
            r"(?:phone|tel|هاتف|جوال|موبايل)[:\s]*([\d+\-\s]{7,20})",
            text, re.IGNORECASE | re.UNICODE,
        )
        if phone_match:
            info.phone = phone_match.group(1).strip()

        return info

    def extract_all(self, text: str) -> MedicalDataExtract:
        warnings: List[str] = []

        vital_signs = self.extract_vital_signs(text)
        medications = self.extract_medications(text)
        diagnoses = self.extract_diagnoses(text)
        lab_results = self.extract_lab_results(text)
        patient_info = self.extract_patient_info(text)

        confidence_scores: Dict[str, float] = {}
        confidence_scores["vital_signs"] = self._vital_signs_confidence(vital_signs)
        confidence_scores["medications"] = min(1.0, len(medications) * 0.8) if medications else 0.0
        confidence_scores["diagnoses"] = min(1.0, len(diagnoses) * 0.7) if diagnoses else 0.0
        confidence_scores["lab_results"] = min(1.0, len(lab_results) * 0.8) if lab_results else 0.0
        confidence_scores["patient_info"] = self._patient_info_confidence(patient_info)

        if not medications and any(w in text.lower() for w in ("medication", "drug", "أدوية", "دواء")):
            warnings.append("Medication keywords found but no structured medications extracted")
        if not diagnoses and any(w in text.lower() for w in ("diagnosis", "dx", "تشخيص")):
            warnings.append("Diagnosis keywords found but no structured diagnoses extracted")

        return MedicalDataExtract(
            vital_signs=vital_signs,
            medications=medications,
            diagnoses=diagnoses,
            lab_results=lab_results,
            patient_info=patient_info,
            extraction_method="regex",
            confidence_scores=confidence_scores,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_reference(value: float, ref_range: str, current_status: Optional[str]) -> tuple:
        is_abnormal = current_status is not None
        try:
            ref_clean = ref_range.strip().replace(" ", "")
            if "-" in ref_clean and not ref_clean.startswith("<") and not ref_clean.startswith(">"):
                low, high = ref_clean.split("-", 1)
                if value < float(low):
                    is_abnormal, current_status = True, "low"
                elif value > float(high):
                    is_abnormal, current_status = True, "high"
                else:
                    is_abnormal, current_status = False, None
            elif ref_clean.startswith("<"):
                if value >= float(ref_clean.lstrip("<").strip()):
                    is_abnormal, current_status = True, "high"
            elif ref_clean.startswith(">"):
                if value <= float(ref_clean.lstrip(">").strip()):
                    is_abnormal, current_status = True, "low"
        except (ValueError, IndexError):
            pass
        return is_abnormal, current_status

    @staticmethod
    def _vital_signs_confidence(vitals: VitalSigns) -> float:
        fields = [vitals.systolic_bp, vitals.diastolic_bp, vitals.heart_rate,
                   vitals.temperature, vitals.spo2, vitals.respiratory_rate]
        return sum(1 for f in fields if f is not None) / len(fields)

    @staticmethod
    def _patient_info_confidence(info: PatientInfo) -> float:
        fields = [info.name, info.age, info.gender, info.patient_id]
        return sum(1 for f in fields if f is not None) / len(fields)

    @staticmethod
    def _arabic_numeral_to_int(text: str) -> str:
        arabic_digits = "٠١٢٣٤٥٦٧٨٩"
        western_digits = "0123456789"
        return text.translate(str.maketrans(arabic_digits, western_digits))
