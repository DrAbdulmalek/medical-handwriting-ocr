"""
FHIR (Fast Healthcare Interoperability Resources) Mapper.

Converts structured medical data extracted by :class:`MedicalSchemaExtractor`
into FHIR R4-compliant JSON resources.  Supports Patient, Observation,
MedicationRequest, Condition, and DiagnosticReport resources, and assembles
them into a FHIR Bundle for easy exchange.
"""

import logging
import uuid
from typing import Optional, List, Dict, Any
from datetime import datetime

from pydantic import BaseModel, Field

from app.config import settings
from app.ai.schema_extractor import (
    VitalSigns,
    Medication,
    Diagnosis,
    LabResult,
    PatientInfo,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Data Models
# =============================================================================


class FHIRBundleConfig(BaseModel):
    """Configuration for FHIR Bundle generation."""

    base_url: str = Field(default="https://hapi.fhir.org/baseR4", description="FHIR server base URL")
    include_patient: bool = Field(default=True, description="Whether to include Patient resource")
    include_vitals: bool = Field(default=True, description="Whether to include vital-sign Observations")
    include_medications: bool = Field(default=True, description="Whether to include MedicationRequests")
    include_diagnoses: bool = Field(default=True, description="Whether to include Conditions")
    include_lab_results: bool = Field(default=True, description="Whether to include DiagnosticReports")
    generate_ids: bool = Field(default=True, description="Auto-generate UUIDs for resources")
    validate_before_emit: bool = Field(default=True, description="Validate each resource before including")


class ValidationResult(BaseModel):
    """Result of validating a FHIR resource."""

    is_valid: bool = Field(description="Whether the resource passed validation")
    resource_type: str = Field(description="FHIR resource type (e.g. 'Patient', 'Observation')")
    errors: List[str] = Field(default_factory=list, description="List of validation error messages")
    warnings: List[str] = Field(default_factory=list, description="Non-fatal warnings")


# =============================================================================
# FHIR R4 Resource Type Codes
# =============================================================================

# LOINC codes for vital signs
_VITAL_SIGN_CODES = {
    "systolic_bp": ("8480-6", "Systolic blood pressure", "mmHg"),
    "diastolic_bp": ("8462-4", "Diastolic blood pressure", "mmHg"),
    "heart_rate": ("8867-4", "Heart rate", "bpm"),
    "temperature": ("8310-5", "Body temperature", "°C"),
    "spo2": ("2708-6", "Oxygen saturation", "%"),
    "respiratory_rate": ("9279-1", "Respiratory rate", "breaths/min"),
}

# SNOMED CT / LOINC for common lab tests (simplified)
_LAB_CODES: Dict[str, tuple] = {
    "glucose": ("2345-7", "Glucose", "LOINC"),
    "hba1c": ("4548-4", "Hemoglobin A1c", "LOINC"),
    "creatinine": ("2160-0", "Creatinine", "LOINC"),
    "bun": ("3094-0", "Blood urea nitrogen", "LOINC"),
    "wbc": ("6690-2", "Leukocytes", "LOINC"),
    "rbc": ("718-7", "Erythrocytes", "LOINC"),
    "hemoglobin": ("718-7", "Hemoglobin", "LOINC"),
    "platelets": ("777-3", "Platelets", "LOINC"),
    "alt": ("1742-6", "Alanine aminotransferase", "LOINC"),
    "ast": ("1920-8", "Aspartate aminotransferase", "LOINC"),
    "potassium": ("2823-3", "Potassium", "LOINC"),
    "sodium": ("2951-2", "Sodium", "LOINC"),
    "cholesterol": ("2093-3", "Total cholesterol", "LOINC"),
    "ldl": ("18262-6", "LDL cholesterol", "LOINC"),
    "hdl": ("2085-9", "HDL cholesterol", "LOINC"),
    "triglycerides": ("2571-8", "Triglycerides", "LOINC"),
    "tsh": ("3016-3", "TSH", "LOINC"),
}


# =============================================================================
# FHIRMapper
# =============================================================================


class FHIRMapper:
    """
    Converts extracted medical data into FHIR R4 JSON resources and bundles.

    Supported resource types:
        - **Patient** — from :class:`PatientInfo`
        - **Observation** — from :class:`VitalSigns`
        - **MedicationRequest** — from :class:`Medication`
        - **Condition** — from :class:`Diagnosis`
        - **DiagnosticReport** — from :class:`LabResult` (grouped)

    The mapper is stateless — each method takes raw data models and returns
    plain dicts that conform to the FHIR R4 JSON specification.
    """

    def __init__(self, config: Optional[FHIRBundleConfig] = None):
        """
        Args:
            config: Bundle generation configuration.  Uses defaults when *None*.
        """
        self.config = config or FHIRBundleConfig()
        logger.info("FHIRMapper initialised (base_url=%s)", self.config.base_url)

    # ------------------------------------------------------------------
    # Resource Generators
    # ------------------------------------------------------------------

    def to_fhir_patient(self, patient_info: PatientInfo) -> dict:
        """
        Convert :class:`PatientInfo` to a FHIR R4 Patient resource.

        Args:
            patient_info: Extracted patient demographic data.

        Returns:
            A dict representing a FHIR Patient resource.
        """
        patient_id = str(uuid.uuid4()) if self.config.generate_ids else (patient_info.id or str(uuid.uuid4()))

        resource: Dict[str, Any] = {
            "resourceType": "Patient",
            "id": patient_id,
            "meta": {
                "profile": ["http://hl7.org/fhir/StructureDefinition/Patient"],
            },
        }

        # Name
        if patient_info.name:
            name_parts = patient_info.name.split()
            family = name_parts[-1] if len(name_parts) > 1 else None
            given = name_parts[:-1] if len(name_parts) > 1 else name_parts
            name_obj: Dict[str, Any] = {}
            if family:
                name_obj["family"] = family
            if given:
                name_obj["given"] = given
            if not name_obj:
                name_obj["text"] = patient_info.name
            resource["name"] = [name_obj]

        # Gender
        if patient_info.gender:
            gender_map = {"male": "male", "female": "female", "m": "male", "f": "female", "ذكر": "male", "أنثى": "female"}
            resource["gender"] = gender_map.get(patient_info.gender.lower(), "unknown")

        # Age (as extension, since FHIR Patient doesn't have a native age field)
        if patient_info.age:
            resource["extension"] = [
                {
                    "url": "http://hl7.org/fhir/StructureDefinition/patient-age",
                    "valueQuantity": {
                        "value": self._parse_age_value(patient_info.age),
                        "unit": "years",
                        "system": "http://unitsofmeasure.org",
                        "code": "a",
                    },
                }
            ]

        # Patient identifier
        if patient_info.patient_id:
            resource["identifier"] = [
                {
                    "system": f"{self.config.base_url}/identifier/patient-mrn",
                    "value": patient_info.patient_id,
                }
            ]

        # Telecom (phone)
        if patient_info.phone:
            resource["telecom"] = [{"system": "phone", "value": patient_info.phone}]

        # Address
        if patient_info.address:
            resource["address"] = [{"text": patient_info.address}]

        logger.debug("Generated FHIR Patient resource: id=%s", patient_id)
        return resource

    def to_fhir_observation(self, vitals: VitalSigns) -> List[dict]:
        """
        Convert :class:`VitalSigns` to FHIR R4 Observation resources.

        Each non-null vital sign becomes its own Observation resource.

        Args:
            vitals: Extracted vital signs.

        Returns:
            A list of FHIR Observation dicts.
        """
        now = datetime.utcnow().isoformat() + "Z"
        observations: List[dict] = []

        field_map = {
            "systolic_bp": vitals.systolic_bp,
            "diastolic_bp": vitals.diastolic_bp,
            "heart_rate": vitals.heart_rate,
            "temperature": vitals.temperature,
            "spo2": vitals.spo2,
            "respiratory_rate": vitals.respiratory_rate,
        }

        for field_name, value in field_map.items():
            if value is None:
                continue

            code_info = _VITAL_SIGN_CODES.get(field_name)
            if not code_info:
                continue

            loinc_code, display, unit = code_info
            obs_id = str(uuid.uuid4()) if self.config.generate_ids else field_name

            observation: Dict[str, Any] = {
                "resourceType": "Observation",
                "id": obs_id,
                "meta": {
                    "profile": ["http://hl7.org/fhir/StructureDefinition/vitalsigns"],
                },
                "status": "final",
                "category": [
                    {
                        "coding": [
                            {
                                "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                "code": "vital-signs",
                                "display": "Vital Signs",
                            }
                        ],
                    }
                ],
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": loinc_code,
                            "display": display,
                        }
                    ],
                    "text": display,
                },
                "subject": {"reference": f"Patient/{obs_id}"},
                "effectiveDateTime": now,
                "valueQuantity": {
                    "value": value,
                    "unit": unit,
                    "system": "http://unitsofmeasure.org",
                },
            }

            observations.append(observation)

        logger.debug("Generated %d FHIR Observation resources", len(observations))
        return observations

    def to_fhir_medication(self, meds: List[Medication]) -> List[dict]:
        """
        Convert a list of :class:`Medication` to FHIR R4 MedicationRequest resources.

        Args:
            meds: Extracted medication entries.

        Returns:
            A list of FHIR MedicationRequest dicts.
        """
        now = datetime.utcnow().isoformat() + "Z"
        requests: List[dict] = []

        for med in meds:
            med_id = str(uuid.uuid4()) if self.config.generate_ids else med.id

            medication_request: Dict[str, Any] = {
                "resourceType": "MedicationRequest",
                "id": med_id,
                "meta": {
                    "profile": ["http://hl7.org/fhir/StructureDefinition/MedicationRequest"],
                },
                "status": "active",
                "intent": "order",
                "medicationCodeableConcept": {
                    "text": med.name,
                },
                "subject": {"reference": f"Patient/{med_id}"},
                "authoredOn": now,
            }

            # Dosage instruction
            dosage_instruction: Dict[str, Any] = {}
            if med.dosage:
                dosage_instruction["text"] = med.dosage
            if med.frequency:
                dosage_instruction["timing"] = {
                    "code": {
                        "text": med.frequency,
                    }
                }
            if med.route:
                dosage_instruction["route"] = {
                    "text": med.route,
                }
            if dosage_instruction:
                medication_request["dosageInstruction"] = [dosage_instruction]

            # Notes
            if med.notes:
                medication_request["note"] = [{"text": med.notes}]

            requests.append(medication_request)

        logger.debug("Generated %d FHIR MedicationRequest resources", len(requests))
        return requests

    def to_fhir_condition(self, diagnoses: List[Diagnosis]) -> List[dict]:
        """
        Convert a list of :class:`Diagnosis` to FHIR R4 Condition resources.

        Args:
            diagnoses: Extracted diagnosis entries.

        Returns:
            A list of FHIR Condition dicts.
        """
        now = datetime.utcnow().isoformat() + "Z"
        conditions: List[dict] = []

        for diag in diagnoses:
            cond_id = str(uuid.uuid4()) if self.config.generate_ids else diag.id

            condition: Dict[str, Any] = {
                "resourceType": "Condition",
                "id": cond_id,
                "meta": {
                    "profile": ["http://hl7.org/fhir/StructureDefinition/Condition"],
                },
                "clinicalStatus": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                            "code": "active" if not diag.chronic else "active",
                            "display": "Active",
                        }
                    ]
                },
                "code": {
                    "text": diag.description,
                },
                "subject": {"reference": f"Patient/{cond_id}"},
                "recordedDate": now,
            }

            # ICD code
            if diag.code:
                condition["code"]["coding"] = [
                    {
                        "system": "http://hl7.org/fhir/sid/icd-10",
                        "code": diag.code,
                        "display": diag.description,
                    }
                ]

            # Severity
            if diag.severity:
                severity_map = {"mild": "mild", "moderate": "moderate", "severe": "severe"}
                condition["severity"] = {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/condition-severity",
                            "code": severity_map.get(diag.severity.lower(), "mild"),
                        }
                    ]
                }

            # Chronic marker
            if diag.chronic:
                condition["extension"] = [
                    {
                        "url": "http://hl7.org/fhir/StructureDefinition/condition-assertedDate",
                        "valueDateTime": now,
                    }
                ]

            conditions.append(condition)

        logger.debug("Generated %d FHIR Condition resources", len(conditions))
        return conditions

    def to_fhir_diagnostic_report(self, lab_results: List[LabResult]) -> List[dict]:
        """
        Convert :class:`LabResult` entries to FHIR R4 DiagnosticReport resources,
        each with embedded Observation references.

        Args:
            lab_results: Extracted lab result entries.

        Returns:
            A list of FHIR DiagnosticReport dicts.
        """
        now = datetime.utcnow().isoformat() + "Z"
        reports: List[dict] = []

        for lab in lab_results:
            report_id = str(uuid.uuid4()) if self.config.generate_ids else lab.id

            # Try to find a known LOINC code
            test_key = lab.test_name.lower().strip().split()[0]  # First word
            code_info = _LAB_CODES.get(test_key)

            report: Dict[str, Any] = {
                "resourceType": "DiagnosticReport",
                "id": report_id,
                "meta": {
                    "profile": ["http://hl7.org/fhir/StructureDefinition/DiagnosticReport"],
                },
                "status": "final",
                "category": [
                    {
                        "coding": [
                            {
                                "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
                                "code": "LAB",
                                "display": "Laboratory",
                            }
                        ],
                    }
                ],
                "code": {
                    "coding": (
                        [
                            {
                                "system": code_info[2],
                                "code": code_info[0],
                                "display": code_info[1],
                            }
                        ]
                        if code_info
                        else []
                    ),
                    "text": lab.test_name,
                },
                "subject": {"reference": f"Patient/{report_id}"},
                "effectiveDateTime": now,
            }

            # Result value as embedded observation reference
            if lab.value is not None:
                obs_id = f"{report_id}-obs"
                report["result"] = [{"reference": f"Observation/{obs_id}"}]

            # Status flag for abnormal results
            if lab.is_abnormal:
                report["conclusion"] = "Abnormal result detected"

            reports.append(report)

        logger.debug("Generated %d FHIR DiagnosticReport resources", len(reports))
        return reports

    # ------------------------------------------------------------------
    # Bundle Creation
    # ------------------------------------------------------------------

    def create_fhir_bundle(
        self,
        patient_info: Optional[PatientInfo] = None,
        vitals: Optional[VitalSigns] = None,
        medications: Optional[List[Medication]] = None,
        diagnoses: Optional[List[Diagnosis]] = None,
        lab_results: Optional[List[LabResult]] = None,
        extra_resources: Optional[List[dict]] = None,
    ) -> dict:
        """
        Assemble a FHIR R4 Bundle from one or more resource types.

        Args:
            patient_info: Patient demographic data.
            vitals: Vital sign measurements.
            medications: Medication list.
            diagnoses: Diagnosis list.
            lab_results: Lab results list.
            extra_resources: Additional pre-built FHIR resources to include.

        Returns:
            A FHIR R4 Bundle dict with type ``collection`` or ``transaction``.
        """
        entries: List[Dict[str, Any]] = []

        # Patient
        if patient_info and self.config.include_patient:
            patient_resource = self.to_fhir_patient(patient_info)
            if self.config.validate_before_emit:
                validation = self.validate_fhir(patient_resource)
                if validation.is_valid:
                    entries.append({"resource": patient_resource})
                else:
                    logger.warning("Patient resource validation failed: %s", validation.errors)
            else:
                entries.append({"resource": patient_resource})

        # Vitals
        if vitals and self.config.include_vitals:
            for obs in self.to_fhir_observation(vitals):
                if self.config.validate_before_emit:
                    validation = self.validate_fhir(obs)
                    if not validation.is_valid:
                        logger.warning("Observation validation failed: %s", validation.errors)
                        continue
                entries.append({"resource": obs})

        # Medications
        if medications and self.config.include_medications:
            for med_req in self.to_fhir_medication(medications):
                if self.config.validate_before_emit:
                    validation = self.validate_fhir(med_req)
                    if not validation.is_valid:
                        logger.warning("MedicationRequest validation failed: %s", validation.errors)
                        continue
                entries.append({"resource": med_req})

        # Diagnoses
        if diagnoses and self.config.include_diagnoses:
            for condition in self.to_fhir_condition(diagnoses):
                if self.config.validate_before_emit:
                    validation = self.validate_fhir(condition)
                    if not validation.is_valid:
                        logger.warning("Condition validation failed: %s", validation.errors)
                        continue
                entries.append({"resource": condition})

        # Lab Results
        if lab_results and self.config.include_lab_results:
            for report in self.to_fhir_diagnostic_report(lab_results):
                if self.config.validate_before_emit:
                    validation = self.validate_fhir(report)
                    if not validation.is_valid:
                        logger.warning("DiagnosticReport validation failed: %s", validation.errors)
                        continue
                entries.append({"resource": report})

        # Extra resources
        if extra_resources:
            for res in extra_resources:
                entries.append({"resource": res})

        bundle: Dict[str, Any] = {
            "resourceType": "Bundle",
            "id": str(uuid.uuid4()),
            "type": "collection",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "entry": entries,
        }

        logger.info("Created FHIR Bundle with %d entries", len(entries))
        return bundle

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_fhir(self, resource: dict) -> ValidationResult:
        """
        Perform lightweight structural validation on a FHIR resource.

        Checks:
            - ``resourceType`` is present and is a known FHIR R4 resource.
            - ``id`` is present.
            - ``status`` is present for applicable resource types.
            - ``code`` is present for applicable resource types.
            - Required fields exist based on resource type.

        This does NOT perform full FHIR schema validation (which would
        require fhir.resources or a FHIR server).  It catches the most
        common structural errors.

        Args:
            resource: A dict representing a FHIR resource.

        Returns:
            A :class:`ValidationResult` indicating pass/fail with details.
        """
        errors: List[str] = []
        warnings: List[str] = []

        _KNOWN_TYPES = {
            "Patient", "Observation", "MedicationRequest", "Condition",
            "DiagnosticReport", "Bundle", "Encounter", "Practitioner",
            "Organization", "Location", "Specimen", "ServiceRequest",
        }

        # Check resourceType
        resource_type = resource.get("resourceType")
        if not resource_type:
            errors.append("Missing required field: resourceType")
            return ValidationResult(is_valid=False, resource_type="Unknown", errors=errors, warnings=warnings)

        if resource_type not in _KNOWN_TYPES:
            warnings.append(f"resourceType '{resource_type}' is not a standard FHIR R4 resource")

        # Check id
        if not resource.get("id"):
            errors.append("Missing required field: id")

        # Type-specific checks
        if resource_type == "Observation":
            if not resource.get("status"):
                errors.append("Observation missing required field: status")
            if not resource.get("code"):
                errors.append("Observation missing required field: code")

        elif resource_type == "MedicationRequest":
            if not resource.get("status"):
                errors.append("MedicationRequest missing required field: status")
            if not resource.get("intent"):
                errors.append("MedicationRequest missing required field: intent")
            if not resource.get("medicationCodeableConcept") and not resource.get("medicationReference"):
                errors.append("MedicationRequest missing required field: medicationCodeableConcept or medicationReference")

        elif resource_type == "Condition":
            if not resource.get("code"):
                errors.append("Condition missing required field: code")
            if not resource.get("clinicalStatus") and not resource.get("verificationStatus"):
                warnings.append("Condition should have clinicalStatus or verificationStatus")

        elif resource_type == "DiagnosticReport":
            if not resource.get("status"):
                errors.append("DiagnosticReport missing required field: status")
            if not resource.get("code"):
                errors.append("DiagnosticReport missing required field: code")

        elif resource_type == "Bundle":
            if not resource.get("type"):
                errors.append("Bundle missing required field: type")

        is_valid = len(errors) == 0
        result = ValidationResult(
            is_valid=is_valid,
            resource_type=resource_type,
            errors=errors,
            warnings=warnings,
        )

        if not is_valid:
            logger.warning("FHIR validation failed for %s: %s", resource_type, errors)

        return result

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_age_value(age_str: str) -> int:
        """
        Parse a free-form age string into an integer value.

        Handles: ``"45"``, ``"45 years"``, ``"٦٠ سنة"``, ``"3 months"``, etc.
        """
        import re

        # Arabic numeral conversion
        arabic_digits = "٠١٢٣٤٥٦٧٨٩"
        western_digits = "0123456789"
        age_str = age_str.translate(str.maketrans(arabic_digits, western_digits))

        match = re.search(r"(\d+)", age_str)
        if match:
            return int(match.group(1))
        return 0
