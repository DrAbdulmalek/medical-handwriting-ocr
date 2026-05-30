"""
Structured Medical Data Extraction (Schema Extractor).

Parses free-form medical text — whether OCR output, clinical notes, or
prescriptions — into structured Pydantic models for vital signs,
medications, diagnoses, lab results, and patient demographics.

Primary extraction uses battle-tested regex patterns tuned for both
Arabic and English medical notation.  An optional LLM-assisted extraction
pass (via :class:`LLMIntegration`) can be layered on top when available
to catch edge-cases the regex engine misses.
"""

import re
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from uuid import uuid4

from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Data Models
# =============================================================================


class VitalSigns(BaseModel):
    """Extracted vital-sign measurements."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    systolic_bp: Optional[float] = Field(default=None, description="Systolic blood pressure (mmHg)")
    diastolic_bp: Optional[float] = Field(default=None, description="Diastolic blood pressure (mmHg)")
    heart_rate: Optional[int] = Field(default=None, description="Heart rate (bpm)")
    temperature: Optional[float] = Field(default=None, description="Body temperature (°C)")
    spo2: Optional[float] = Field(default=None, description="Oxygen saturation (%)")
    respiratory_rate: Optional[int] = Field(default=None, description="Respiratory rate (breaths/min)")
    source_text: str = Field(default="", description="Original text snippet from which vitals were extracted")
    extracted_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class Medication(BaseModel):
    """A single medication entry extracted from text."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(description="Drug / medication name")
    dosage: Optional[str] = Field(default=None, description="Dosage amount (e.g. '500mg', '10ml')")
    frequency: Optional[str] = Field(default=None, description="Frequency (e.g. 'BID', 'once daily', 'كل 8 ساعات')")
    route: Optional[str] = Field(default=None, description="Administration route (e.g. 'PO', 'IV', 'topical')")
    duration: Optional[str] = Field(default=None, description="Treatment duration (e.g. '7 days', 'أسبوعين')")
    notes: Optional[str] = Field(default=None, description="Additional instructions")
    source_text: str = Field(default="", description="Original text snippet")


class Diagnosis(BaseModel):
    """A diagnosis entry extracted from text."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    code: Optional[str] = Field(default=None, description="ICD-10 / ICD-11 code if detected (e.g. 'E11.9')")
    description: str = Field(description="Diagnosis description in original language")
    severity: Optional[str] = Field(default=None, description="Severity level if noted (mild/moderate/severe)")
    laterality: Optional[str] = Field(default=None, description="Laterality (left/right/bilateral)")
    chronic: bool = Field(default=False, description="Whether marked as chronic / long-standing")
    source_text: str = Field(default="", description="Original text snippet")


class LabResult(BaseModel):
    """A single laboratory test result."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    test_name: str = Field(description="Name of the laboratory test")
    value: Optional[float] = Field(default=None, description="Numeric result value")
    unit: Optional[str] = Field(default=None, description="Unit of measurement (e.g. 'mg/dL', 'mmol/L')")
    reference_range: Optional[str] = Field(default=None, description="Reference / normal range (e.g. '70-100')")
    is_abnormal: bool = Field(default=False, description="Whether value falls outside reference range")
    status: Optional[str] = Field(default=None, description="Flag: 'high', 'low', 'critical', or None")
    source_text: str = Field(default="", description="Original text snippet")


class PatientInfo(BaseModel):
    """Extracted patient demographic information."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: Optional[str] = Field(default=None, description="Patient full name")
    age: Optional[str] = Field(default=None, description="Age (may include units, e.g. '45 years', '٦٠ سنة')")
    gender: Optional[str] = Field(default=None, description="Gender ('male', 'female', or None)")
    date_of_birth: Optional[str] = Field(default=None, description="Date of birth as free-form string")
    patient_id: Optional[str] = Field(default=None, description="Hospital / MRN identifier")
    phone: Optional[str] = Field(default=None, description="Contact phone number")
    address: Optional[str] = Field(default=None, description="Address")
    allergies: List[str] = Field(default_factory=list, description="Known allergies")
    source_text: str = Field(default="", description="Original text snippet")


class MedicalDataExtract(BaseModel):
    """Aggregate of all structured data extracted from a medical document."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    vital_signs: VitalSigns = Field(default_factory=VitalSigns)
    medications: List[Medication] = Field(default_factory=list)
    diagnoses: List[Diagnosis] = Field(default_factory=list)
    lab_results: List[LabResult] = Field(default_factory=list)
    patient_info: PatientInfo = Field(default_factory=PatientInfo)
    extraction_method: str = Field(default="regex", description="'regex', 'llm', or 'hybrid'")
    confidence_scores: Dict[str, float] = Field(default_factory=dict, description="Per-category confidence (0-1)")
    warnings: List[str] = Field(default_factory=list, description="Extraction warnings or ambiguities")
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
    # Drug name (letters including Arabic unicode range)
    r"([A-Za-z\u0600-\u06FF][\w\u0600-\u06FF\-]{1,40})"
    r"\s*"
    r"(?:"
    r"(\d+(?:\.\d+)?)\s*(?:mg|mcg|g|ml|IU|units?|وحدة|مل|ملغ)\b"  # dosage
    r")?"
    r"\s*"
    r"(?:"
    r"((?:BID|TID|QID|QD|OD|PRN|SOS|once|twice|daily|weekly|"
    r"كل\s*\d+\s*(?:ساعة|ساعات|يوم|أيام)|"
    r"صباحا|مساء|bedtime|at night|"
    r"\d+x?/?day|1-0-1|1-1-1|1-0-0|0-1-0))"  # frequency
    r")?"
    r"\s*"
    r"(?:"
    r"((?:PO|IV|IM|SC|SubQ|topical|inhaled|rectal|oral|"
    r"فم|وريد|عضل|تحت الجلد|موضعي))"  # route
    r")?"
    r"\s*"
    r"(?:"
    r"((?:for\s+\d+\s*(?:days?|weeks?|months?)|"
    r"لمدة\s*\d+\s*(?:يوم|أيام|أسبوع|أسابيع|شهر|أشهر)))"  # duration
    r")?",
    re.IGNORECASE | re.UNICODE,
)


# =============================================================================
# Regex Patterns — Diagnoses
# =============================================================================

_RE_ICD = re.compile(
    r"\b([A-Z]\d{2}(?:\.\d{1,4})?)\b",
)

_RE_DIAGNOSIS_KEYWORD = re.compile(
    r"(?:(?:dx|diagnosis|diagnoses|تشخيص|المرض|الحالة)[:\s–-]+)"
    r"(.+?)(?:\n|$)",
    re.IGNORECASE | re.UNICODE,
)

_RE_CHRONIC_MARKER = re.compile(
    r"(?i)(?:chronic|مزمن|long[- ]standing|دائم)",
)


# =============================================================================
# Regex Patterns — Lab Results
# =============================================================================

_RE_LAB_RESULT = re.compile(
    r"([A-Za-z\u0600-\u06FF][\w\u0600-\u06FF\s\-]{1,50})"  # test name
    r"\s*[:\s]\s*"
    r"([\d.]+)"  # value
    r"\s*(?:mg/dL|mmol/L|g/L|U/L|ng/mL|pg/mL|mg/L|µmol/L|%|"
    r"ملغ/ديسيلتر|ملمول/لتر)?\b"  # unit
    r"(?:\s*\(?([\d.\-~\s]+\s*(?:mg/dL|mmol/L|g/L|U/L|ng/mL|pg/mL|%))?\)?)?"  # optional reference range
    r"(?:\s*(?:high|low|↑|↓|مرتفع|منخفض|crit|حرج))?",  # flag
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
    """
    Extract structured medical data from free-form text using regex patterns
    and, optionally, LLM-assisted extraction.

    The extractor is designed to handle OCR output which may contain
    recognition errors, mixed Arabic/English scripts, and irregular formatting.
    """

    def __init__(self, use_llm_fallback: bool = False):
        """
        Args:
            use_llm_fallback: When ``True``, the extractor will attempt LLM
                extraction (via :class:`LLMIntegration`) for any category that
                regex produces no results for.  Requires an LLM to be configured.
        """
        self.use_llm_fallback = use_llm_fallback
        logger.info(
            "MedicalSchemaExtractor initialised (llm_fallback=%s)",
            self.use_llm_fallback,
        )

    # ------------------------------------------------------------------
    # Individual extractors
    # ------------------------------------------------------------------

    def extract_vital_signs(self, text: str) -> VitalSigns:
        """
        Extract vital signs (BP, HR, temperature, SpO2, RR) from *text*.

        Args:
            text: Medical text (may be Arabic, English, or mixed).

        Returns:
            A :class:`VitalSigns` instance with populated fields.
        """
        vitals = VitalSigns(source_text=text)

        # Blood pressure
        for pattern in (_RE_BP, _RE_STANDALONE_BP):
            match = pattern.search(text)
            if match:
                vitals.systolic_bp = float(match.group(1))
                vitals.diastolic_bp = float(match.group(2))
                vitals.source_text = match.group(0)
                break

        # Heart rate
        match = _RE_HR.search(text)
        if match:
            vitals.heart_rate = int(match.group(1))
            if not vitals.source_text:
                vitals.source_text = match.group(0)

        # Temperature
        match = _RE_TEMP.search(text)
        if match:
            vitals.temperature = float(match.group(1))
            if not vitals.source_text:
                vitals.source_text = match.group(0)

        # SpO2
        match = _RE_SPO2.search(text)
        if match:
            vitals.spo2 = float(match.group(1))
            if not vitals.source_text:
                vitals.source_text = match.group(0)

        # Respiratory rate
        match = _RE_RR.search(text)
        if match:
            vitals.respiratory_rate = int(match.group(1))
            if not vitals.source_text:
                vitals.source_text = match.group(0)

        logger.debug("Extracted vitals: %s", vitals.model_dump(exclude={"id"}))
        return vitals

    def extract_medications(self, text: str) -> List[Medication]:
        """
        Extract medication entries from *text*.

        Supports common formats: ``DrugName 500mg BID PO for 7 days``,
        Arabic equivalents, and various shorthand notations.

        Args:
            text: Medical text.

        Returns:
            A list of :class:`Medication` instances.
        """
        medications: List[Medication] = []
        seen_names: set = set()

        for match in _RE_MEDICATION.finditer(text):
            name = match.group(1).strip()
            if not name or name.lower() in seen_names:
                continue
            seen_names.add(name.lower())

            med = Medication(
                name=name,
                dosage=match.group(2).strip() if match.group(2) else None,
                frequency=match.group(3).strip() if match.group(3) else None,
                route=match.group(4).strip() if match.group(4) else None,
                duration=match.group(5).strip() if match.group(5) else None,
                source_text=match.group(0).strip(),
            )
            medications.append(med)

        logger.debug("Extracted %d medications", len(medications))
        return medications

    def extract_diagnoses(self, text: str) -> List[Diagnosis]:
        """
        Extract diagnosis entries from *text*.

        Looks for ICD codes (e.g. ``E11.9``) and diagnosis keywords
        (``dx:``, ``diagnosis:``, ``تشخيص:``).

        Args:
            text: Medical text.

        Returns:
            A list of :class:`Diagnosis` instances.
        """
        diagnoses: List[Diagnosis] = []

        # Try keyword-based extraction first
        for match in _RE_DIAGNOSIS_KEYWORD.finditer(text):
            desc = match.group(1).strip()
            if not desc:
                continue

            # Check for ICD code within the description
            icd_match = _RE_ICD.search(desc)
            code = icd_match.group(1) if icd_match else None

            chronic = bool(_RE_CHRONIC_MARKER.search(desc))

            diag = Diagnosis(
                code=code,
                description=desc,
                chronic=chronic,
                source_text=match.group(0).strip(),
            )
            diagnoses.append(diag)

        # Scan for standalone ICD codes not captured above
        seen_codes = {d.code for d in diagnoses if d.code}
        for match in _RE_ICD.finditer(text):
            code = match.group(1)
            if code not in seen_codes:
                seen_codes.add(code)
                # Take surrounding context as description
                start = max(0, match.start() - 60)
                end = min(len(text), match.end() + 100)
                context = text[start:end].strip()
                diagnoses.append(
                    Diagnosis(
                        code=code,
                        description=context,
                        source_text=match.group(0),
                    )
                )

        logger.debug("Extracted %d diagnoses", len(diagnoses))
        return diagnoses

    def extract_lab_results(self, text: str) -> List[LabResult]:
        """
        Extract laboratory test results from *text*.

        Parses patterns like ``HbA1c 7.2%``, ``Glucose 120 mg/dL (70-100)``,
        and Arabic equivalents.

        Args:
            text: Medical text.

        Returns:
            A list of :class:`LabResult` instances.
        """
        results: List[LabResult] = []
        seen_tests: set = set()

        for match in _RE_LAB_RESULT.finditer(text):
            test_name = match.group(1).strip()
            # Normalise test name for dedup
            test_key = test_name.lower().strip()
            if not test_name or test_key in seen_tests:
                continue
            seen_tests.add(test_key)

            value = float(match.group(2)) if match.group(2) else None
            unit = match.group(3).strip() if match.group(3) else None
            ref_range = match.group(4).strip() if match.group(4) else None

            # Determine abnormal status from text flags
            raw_after_value = text[match.end():match.end() + 30].lower()
            is_abnormal = any(
                flag in raw_after_value
                for flag in ("high", "low", "↑", "↓", "مرتفع", "منخفض", "crit", "حرج")
            )
            status = None
            if is_abnormal:
                if any(f in raw_after_value for f in ("high", "↑", "مرتفع", "crit", "حرج")):
                    status = "high"
                else:
                    status = "low"

            # Try to check value against reference range
            if value is not None and ref_range:
                is_abnormal, status = self._check_reference(value, ref_range, status)

            results.append(
                LabResult(
                    test_name=test_name,
                    value=value,
                    unit=unit,
                    reference_range=ref_range,
                    is_abnormal=is_abnormal,
                    status=status,
                    source_text=match.group(0).strip(),
                )
            )

        logger.debug("Extracted %d lab results", len(results))
        return results

    def extract_patient_info(self, text: str) -> PatientInfo:
        """
        Extract patient demographic information from *text*.

        Looks for name, age, gender, patient ID, phone, address, and allergies.

        Args:
            text: Medical text.

        Returns:
            A :class:`PatientInfo` instance.
        """
        info = PatientInfo(source_text=text)

        # Name
        match = _RE_PATIENT_NAME.search(text)
        if match:
            info.name = match.group(1).strip()
            info.source_text = match.group(0).strip()

        # Age — also try Arabic numerals conversion
        match = _RE_AGE.search(text)
        if match:
            raw_age = self._arabic_numeral_to_int(match.group(1))
            info.age = f"{raw_age}"

        # Gender
        match = _RE_GENDER.search(text)
        if match:
            raw = match.group(1).lower()
            if raw in ("male", "m", "ذكر"):
                info.gender = "male"
            elif raw in ("female", "f", "أنثى"):
                info.gender = "female"

        # Patient ID / MRN
        match = _RE_PATIENT_ID.search(text)
        if match:
            info.patient_id = match.group(1).strip()

        # Allergies
        for match in _RE_ALLERGY.finditer(text):
            allergy_text = match.group(1).strip()
            if allergy_text and allergy_text.lower() not in ("none", "nka", "لا يوجد", "لا"):
                info.allergies.append(allergy_text)

        # Phone (simple pattern)
        phone_match = re.search(r"(?:phone|tel|هاتف|جوال|موبايل)[:\s]*([\d+\-\s]{7,20})", text, re.IGNORECASE | re.UNICODE)
        if phone_match:
            info.phone = phone_match.group(1).strip()

        logger.debug("Extracted patient info: %s", info.model_dump(exclude={"id"}))
        return info

    def extract_all(self, text: str) -> MedicalDataExtract:
        """
        Run all extraction categories on *text* and aggregate results.

        If ``use_llm_fallback`` was enabled and any category yields no results
        via regex, the extractor will attempt an LLM-assisted pass.

        Args:
            text: Full medical document text.

        Returns:
            A :class:`MedicalDataExtract` aggregate.
        """
        warnings: List[str] = []

        vital_signs = self.extract_vital_signs(text)
        medications = self.extract_medications(text)
        diagnoses = self.extract_diagnoses(text)
        lab_results = self.extract_lab_results(text)
        patient_info = self.extract_patient_info(text)

        # Compute per-category confidence heuristics
        confidence_scores: Dict[str, float] = {}
        confidence_scores["vital_signs"] = self._vital_signs_confidence(vital_signs)
        confidence_scores["medications"] = min(1.0, len(medications) * 0.8) if medications else 0.0
        confidence_scores["diagnoses"] = min(1.0, len(diagnoses) * 0.7) if diagnoses else 0.0
        confidence_scores["lab_results"] = min(1.0, len(lab_results) * 0.8) if lab_results else 0.0
        confidence_scores["patient_info"] = self._patient_info_confidence(patient_info)

        # Generate warnings for empty categories
        if not medications and any(w in text.lower() for w in ("medication", "drug", "أدوية", "دواء")):
            warnings.append("Medication keywords found but no structured medications extracted")
        if not diagnoses and any(w in text.lower() for w in ("diagnosis", "dx", "تشخيص")):
            warnings.append("Diagnosis keywords found but no structured diagnoses extracted")

        extract = MedicalDataExtract(
            vital_signs=vital_signs,
            medications=medications,
            diagnoses=diagnoses,
            lab_results=lab_results,
            patient_info=patient_info,
            extraction_method="regex",
            confidence_scores=confidence_scores,
            warnings=warnings,
        )

        # Optional LLM fallback for empty categories
        if self.use_llm_fallback:
            extract = self._llm_fallback(extract, text)

        logger.info(
            "Full extraction complete: vitals=%d meds=%d dx=%d labs=%d warnings=%d",
            bool(vital_signs.systolic_bp or vital_signs.heart_rate),
            len(medications),
            len(diagnoses),
            len(lab_results),
            len(warnings),
        )
        return extract

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_reference(value: float, ref_range: str, current_status: Optional[str]) -> tuple:
        """
        Attempt to evaluate whether *value* falls within *ref_range*.

        Supports ranges like ``70-100``, ``3.5 - 5.5``, ``< 200``, ``> 0.5``.
        """
        is_abnormal = current_status is not None

        try:
            ref_clean = ref_range.strip().replace(" ", "")

            if "-" in ref_clean and not ref_clean.startswith("<") and not ref_clean.startswith(">"):
                low, high = ref_clean.split("-", 1)
                low_val = float(low)
                high_val = float(high)
                if value < low_val:
                    is_abnormal = True
                    current_status = "low"
                elif value > high_val:
                    is_abnormal = True
                    current_status = "high"
                else:
                    is_abnormal = False
                    current_status = None
            elif ref_clean.startswith("<"):
                threshold = float(ref_clean.lstrip("<").strip())
                if value >= threshold:
                    is_abnormal = True
                    current_status = "high"
            elif ref_clean.startswith(">"):
                threshold = float(ref_clean.lstrip(">").strip())
                if value <= threshold:
                    is_abnormal = True
                    current_status = "low"
        except (ValueError, IndexError):
            pass  # Could not parse reference range

        return is_abnormal, current_status

    @staticmethod
    def _vital_signs_confidence(vitals: VitalSigns) -> float:
        """Heuristic confidence for extracted vital signs."""
        fields = [
            vitals.systolic_bp, vitals.diastolic_bp, vitals.heart_rate,
            vitals.temperature, vitals.spo2, vitals.respiratory_rate,
        ]
        filled = sum(1 for f in fields if f is not None)
        return filled / len(fields)

    @staticmethod
    def _patient_info_confidence(info: PatientInfo) -> float:
        """Heuristic confidence for extracted patient info."""
        fields = [info.name, info.age, info.gender, info.patient_id]
        filled = sum(1 for f in fields if f is not None)
        return filled / len(fields)

    @staticmethod
    def _arabic_numeral_to_int(text: str) -> str:
        """
        Convert Arabic-Indic numerals (٠-٩) to Western (0-9).
        """
        arabic_digits = "٠١٢٣٤٥٦٧٨٩"
        western_digits = "0123456789"
        return text.translate(str.maketrans(arabic_digits, western_digits))

    def _llm_fallback(self, extract: MedicalDataExtract, text: str) -> MedicalDataExtract:
        """
        Attempt LLM-assisted extraction for categories where regex
        produced no results.

        This is a best-effort fallback; failures are logged but do not raise.
        """
        try:
            from app.ai.llm_integration import LLMIntegration

            llm = LLMIntegration()
            llm.initialize_llm()

            prompt = (
                "Extract structured medical data from the following text. "
                "Return JSON with keys: medications (list of {name, dosage, frequency, route}), "
                "diagnoses (list of {code, description}), "
                "lab_results (list of {test_name, value, unit, reference_range}).\n\n"
                f"Text:\n{text[:3000]}"
            )

            response = llm.extract_entities(prompt)

            if isinstance(response, dict):
                if not extract.medications and "medications" in response:
                    for m in response["medications"]:
                        extract.medications.append(
                            Medication(
                                name=m.get("name", ""),
                                dosage=m.get("dosage"),
                                frequency=m.get("frequency"),
                                route=m.get("route"),
                            )
                        )
                if not extract.diagnoses and "diagnoses" in response:
                    for d in response["diagnoses"]:
                        extract.diagnoses.append(
                            Diagnosis(
                                code=d.get("code"),
                                description=d.get("description", ""),
                            )
                        )
                if not extract.lab_results and "lab_results" in response:
                    for l in response["lab_results"]:
                        extract.lab_results.append(
                            LabResult(
                                test_name=l.get("test_name", ""),
                                value=l.get("value"),
                                unit=l.get("unit"),
                                reference_range=l.get("reference_range"),
                            )
                        )
                extract.extraction_method = "hybrid"

        except Exception as exc:
            logger.warning("LLM fallback extraction failed: %s", exc)

        return extract
