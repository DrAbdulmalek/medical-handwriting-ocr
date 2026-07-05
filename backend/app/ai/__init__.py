"""
AI module for Medical Handwriting OCR.

Provides advanced AI capabilities including text chunking, semantic splitting,
structured data extraction, patient profiling, FHIR mapping, LLM integration,
and Retrieval-Augmented Generation (RAG) for medical documents.
"""

from app.ai.chunker import MedicalTextChunker, Chunk, DocumentChunk, ChunkMetadata, ChunkingConfig
from app.ai.semantic_splitter import SemanticSplitter, SemanticChunk, SplitPoint
from app.ai.schema_extractor import (
    MedicalSchemaExtractor,
    VitalSigns,
    Medication,
    Diagnosis,
    LabResult,
    PatientInfo,
    MedicalDataExtract,
)
from app.ai.patient_profile_builder import (
    PatientProfileBuilder,
    PatientProfile,
    VisitRecord,
    MedicationEntry,
    DiagnosisEntry,
    PatientTimeline,
)
from app.ai.fhir_mapper import FHIRMapper, ValidationResult, FHIRBundleConfig
from app.ai.llm_integration import LLMIntegration, LLMConfig, ValidationReport, EntityExtraction
from app.ai.rag_engine import (
    MedicalRAGEngine,
    RetrievalResult,
    QAAnswer,
    IndexStats,
    DocumentEmbedding,
)

__all__ = [
    # Chunker
    "MedicalTextChunker",
    "Chunk",
    "DocumentChunk",
    "ChunkMetadata",
    "ChunkingConfig",
    # Semantic Splitter
    "SemanticSplitter",
    "SemanticChunk",
    "SplitPoint",
    # Schema Extractor
    "MedicalSchemaExtractor",
    "VitalSigns",
    "Medication",
    "Diagnosis",
    "LabResult",
    "PatientInfo",
    "MedicalDataExtract",
    # Patient Profile Builder
    "PatientProfileBuilder",
    "PatientProfile",
    "VisitRecord",
    "MedicationEntry",
    "DiagnosisEntry",
    "PatientTimeline",
    # FHIR Mapper
    "FHIRMapper",
    "ValidationResult",
    "FHIRBundleConfig",
    # LLM Integration
    "LLMIntegration",
    "LLMConfig",
    "ValidationReport",
    "EntityExtraction",
    # RAG Engine
    "MedicalRAGEngine",
    "RetrievalResult",
    "QAAnswer",
    "IndexStats",
    "DocumentEmbedding",
]
