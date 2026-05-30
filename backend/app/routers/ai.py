"""
AI Features Router
==================
Endpoints for text chunking, semantic splitting, medical schema extraction,
patient profile building, FHIR conversion, LLM integration, and RAG engine.
"""

import logging
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["ai"])


# ─────────────────────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────────────────────

class ChunkRequest(BaseModel):
    """Request body for text chunking."""
    text: str = Field(..., min_length=1, description="Text to chunk")
    chunk_size: int = Field(default=512, ge=100, le=4096, description="Target chunk size in characters")
    overlap: int = Field(default=50, ge=0, le=500, description="Overlap between chunks in characters")
    strategy: str = Field(default="hybrid", description="Chunking strategy: fixed, sentence, paragraph, section, hybrid")


class SchemaExtractRequest(BaseModel):
    """Request body for medical schema extraction."""
    text: str = Field(..., min_length=1, description="Text to extract medical data from")
    extract_vitals: bool = Field(default=True)
    extract_medications: bool = Field(default=True)
    extract_diagnoses: bool = Field(default=True)
    extract_lab_results: bool = Field(default=True)
    extract_patient_info: bool = Field(default=True)
    use_llm: bool = Field(default=False, description="Use LLM for enhanced extraction")


class PatientProfileRequest(BaseModel):
    """Request body for building a patient profile."""
    patient_id: str = Field(..., description="Patient identifier")
    documents: List[dict] = Field(..., description="List of document data dictionaries")


class FHIRConvertRequest(BaseModel):
    """Request body for FHIR conversion."""
    patient_info: Optional[dict] = Field(default=None)
    vital_signs: Optional[dict] = Field(default=None)
    medications: Optional[List[dict]] = Field(default=None)
    diagnoses: Optional[List[dict]] = Field(default=None)
    lab_results: Optional[List[dict]] = Field(default=None)


class RAGIndexRequest(BaseModel):
    """Request body for indexing documents in RAG."""
    documents: List[dict] = Field(..., description="Documents to index: [{id, title, content, metadata}]")


class RAGSearchRequest(BaseModel):
    """Request body for RAG search."""
    query: str = Field(..., min_length=1, description="Search query")
    top_k: int = Field(default=5, ge=1, le=50, description="Number of results")
    filter_metadata: Optional[dict] = Field(default=None)


class RAGAskRequest(BaseModel):
    """Request body for RAG question answering."""
    question: str = Field(..., min_length=1, description="Question to answer")
    top_k: int = Field(default=5, ge=1, le=50)
    include_sources: bool = Field(default=True)


# ─────────────────────────────────────────────────────────────
# Chunking Endpoints
# ─────────────────────────────────────────────────────────────

@router.post(
    "/chunk",
    summary="Chunk text for RAG",
    description="Split text into semantically meaningful chunks optimized for RAG retrieval.",
)
async def chunk_text(request: ChunkRequest):
    """Chunk text using the medical text chunker."""
    try:
        from app.ai.chunker import MedicalTextChunker, ChunkingConfig

        config = ChunkingConfig(
            chunk_size=request.chunk_size,
            overlap=request.overlap,
            strategy=request.strategy,
        )
        chunker = MedicalTextChunker(config=config)
        chunks = chunker.chunk_text(request.text)

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": f"Text chunked into {len(chunks)} segment(s)",
                "data": {
                    "total_chunks": len(chunks),
                    "strategy": request.strategy,
                    "chunk_size": request.chunk_size,
                    "overlap": request.overlap,
                    "chunks": [
                        {
                            "index": i,
                            "text": c.text[:200] + "..." if len(c.text) > 200 else c.text,
                            "char_count": len(c.text),
                            "token_estimate": c.metadata.token_estimate if c.metadata else None,
                        }
                        for i, c in enumerate(chunks)
                    ],
                },
            },
        )

    except Exception as e:
        logger.error("Chunking failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Chunking failed: {str(e)}")


# ─────────────────────────────────────────────────────────────
# Schema Extraction Endpoints
# ─────────────────────────────────────────────────────────────

@router.post(
    "/schema/extract",
    summary="Extract structured medical data",
    description=(
        "Extract structured medical data from unstructured text: vital signs, "
        "medications, diagnoses, lab results, and patient information."
    ),
)
async def extract_medical_schema(request: SchemaExtractRequest):
    """Extract structured medical entities from text."""
    try:
        from app.ai.schema_extractor import MedicalSchemaExtractor

        extractor = MedicalSchemaExtractor()
        result = extractor.extract_all(
            text=request.text,
            use_llm=request.use_llm,
        )

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": "Medical data extraction complete",
                "data": result.model_dump(),
            },
        )

    except Exception as e:
        logger.error("Schema extraction failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Schema extraction failed: {str(e)}")


# ─────────────────────────────────────────────────────────────
# Patient Profile Endpoints
# ─────────────────────────────────────────────────────────────

@router.post(
    "/patient/profile",
    summary="Build patient profile",
    description=(
        "Aggregate data from multiple documents to build a comprehensive "
        "patient profile with visit history, medication list, and timeline."
    ),
)
async def build_patient_profile(request: PatientProfileRequest):
    """Build a comprehensive patient profile from multiple document extractions."""
    try:
        from app.ai.patient_profile_builder import PatientProfileBuilder

        builder = PatientProfileBuilder()
        profile = builder.build_profile(
            patient_id=request.patient_id,
            documents=request.documents,
        )

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": "Patient profile built successfully",
                "data": profile.model_dump(),
            },
        )

    except Exception as e:
        logger.error("Patient profile building failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Profile building failed: {str(e)}")


# ─────────────────────────────────────────────────────────────
# FHIR Conversion Endpoints
# ─────────────────────────────────────────────────────────────

@router.post(
    "/fhir/convert",
    summary="Convert data to FHIR format",
    description="Convert extracted medical data to FHIR R4 compliant resources.",
)
async def convert_to_fhir(request: FHIRConvertRequest):
    """Convert medical data to FHIR R4 format."""
    try:
        from app.ai.fhir_mapper import FHIRMapper

        mapper = FHIRMapper()
        resources = []
        resource_types = []

        if request.patient_info:
            resources.append(mapper.to_fhir_patient(request.patient_info))
            resource_types.append("Patient")

        if request.vital_signs:
            obs = mapper.to_fhir_observation(request.vital_signs)
            resources.extend(obs)
            resource_types.append(f"Observation({len(obs)})")

        if request.medications:
            meds = mapper.to_fhir_medication(request.medications)
            resources.extend(meds)
            resource_types.append(f"MedicationRequest({len(meds)})")

        if request.diagnoses:
            conditions = mapper.to_fhir_condition(request.diagnoses)
            resources.extend(conditions)
            resource_types.append(f"Condition({len(conditions)})")

        if not resources:
            raise HTTPException(status_code=400, detail="No data provided for conversion")

        bundle = mapper.create_fhir_bundle(resources)
        validation = mapper.validate_fhir(bundle)

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": f"FHIR bundle created with {len(resources)} resource(s)",
                "data": {
                    "resource_types": resource_types,
                    "bundle_type": bundle.get("type"),
                    "total_resources": len(resources),
                    "validation": validation.model_dump(),
                    "bundle": bundle,
                },
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("FHIR conversion failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"FHIR conversion failed: {str(e)}")


# ─────────────────────────────────────────────────────────────
# RAG Engine Endpoints
# ─────────────────────────────────────────────────────────────

@router.post(
    "/rag/index",
    summary="Index documents for RAG",
    description="Add documents to the RAG vector store for semantic search and question answering.",
)
async def index_documents(request: RAGIndexRequest):
    """Index documents into the RAG engine."""
    try:
        from app.ai.rag_engine import MedicalRAGEngine

        engine = MedicalRAGEngine()
        stats = engine.build_index(request.documents)

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": f"Indexed {stats.documents_indexed} document(s)",
                "data": stats.model_dump(),
            },
        )

    except Exception as e:
        logger.error("RAG indexing failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Indexing failed: {str(e)}")


@router.post(
    "/rag/search",
    summary="Search RAG index",
    description="Search the RAG vector store for relevant medical documents.",
)
async def search_rag(request: RAGSearchRequest):
    """Search the RAG index for relevant documents."""
    try:
        from app.ai.rag_engine import MedicalRAGEngine

        engine = MedicalRAGEngine()
        results = engine.search(
            query=request.query,
            top_k=request.top_k,
            filter_metadata=request.filter_metadata,
        )

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": f"Found {len(results)} result(s)",
                "data": {
                    "query": request.query,
                    "results": [
                        {
                            "doc_id": r.doc_id,
                            "title": r.title,
                            "content": r.content[:200] + "..." if len(r.content) > 200 else r.content,
                            "score": r.score,
                            "source": r.source,
                        }
                        for r in results
                    ],
                },
            },
        )

    except Exception as e:
        logger.error("RAG search failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@router.post(
    "/rag/ask",
    summary="Ask question via RAG",
    description="Ask a medical question and get an AI-generated answer with source citations.",
)
async def ask_rag_question(request: RAGAskRequest):
    """Ask a question and get an answer with RAG-backed citations."""
    try:
        from app.ai.rag_engine import MedicalRAGEngine

        engine = MedicalRAGEngine()
        answer = engine.answer_question(
            question=request.question,
            top_k=request.top_k,
        )

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": "Answer generated successfully",
                "data": {
                    "question": request.question,
                    "answer": answer.answer,
                    "sources": answer.sources if request.include_sources else None,
                    "confidence": answer.confidence,
                    "model": answer.model_used,
                },
            },
        )

    except Exception as e:
        logger.error("RAG question answering failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"QA failed: {str(e)}")
