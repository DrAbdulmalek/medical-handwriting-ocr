"""
Parsers package for Medical Handwriting OCR.

Provides document parsing, table extraction, equation recognition,
medical image processing, medical-specific object detection, and
batch processing capabilities.
"""

from app.parsers.document_parser import (
    DocumentParser,
    DocumentParseResult,
    PageContent,
    TableContent,
    ImageContent,
)
from app.parsers.table_extractor import (
    TableExtractor,
    TableData,
)
from app.parsers.equation_parser import (
    EquationParser,
    EquationRegion,
)
from app.parsers.image_processor import (
    MedicalImageProcessor,
    MedicalImageResult,
    DetectedObject,
    RegionClassification,
)
from app.parsers.medical_detector import (
    MedicalObjectDetector,
    MedicalElements,
    PrescriptionBlock,
)
from app.parsers.batch_processor import (
    BatchProcessor,
    BatchJob,
    BatchStatus,
    BatchResult,
    PatientBatchResult,
)

__all__ = [
    # Document Parser
    "DocumentParser",
    "DocumentParseResult",
    "PageContent",
    "TableContent",
    "ImageContent",
    # Table Extractor
    "TableExtractor",
    "TableData",
    # Equation Parser
    "EquationParser",
    "EquationRegion",
    # Image Processor
    "MedicalImageProcessor",
    "MedicalImageResult",
    "DetectedObject",
    "RegionClassification",
    # Medical Detector
    "MedicalObjectDetector",
    "MedicalElements",
    "PrescriptionBlock",
    # Batch Processor
    "BatchProcessor",
    "BatchJob",
    "BatchStatus",
    "BatchResult",
    "PatientBatchResult",
]
