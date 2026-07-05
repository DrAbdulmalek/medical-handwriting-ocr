"""
LLM Integration Module for Medical AI Tasks.

Provides a unified interface to multiple LLM providers (OpenAI, local models,
or any LangChain-compatible backend) with pre-built prompt templates for
medical-domain tasks such as clinical note summarisation, entity extraction,
medical Q&A, and extraction validation.
"""

import json
import logging
import re
from typing import Optional, List, Dict, Any
from datetime import datetime

from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Data Models
# =============================================================================


class LLMConfig(BaseModel):
    """Configuration for the LLM integration."""

    provider: str = Field(default="openai", description="LLM provider: 'openai', 'local', or 'langchain'")
    model: Optional[str] = Field(default=None, description="Model name (provider-specific).  None = provider default.")
    api_key: Optional[str] = Field(default=None, description="API key for cloud providers")
    base_url: Optional[str] = Field(default=None, description="Custom API base URL (for local / self-hosted models)")
    temperature: float = Field(default=0.1, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: int = Field(default=2048, ge=1, le=16384, description="Maximum tokens in the completion")
    timeout: int = Field(default=60, ge=5, description="Request timeout in seconds")
    max_retries: int = Field(default=3, ge=0, le=10, description="Maximum retries on transient failures")


class ValidationReport(BaseModel):
    """Report from validating extracted medical data against the source text."""

    is_valid: bool = Field(description="Overall validation pass/fail")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Overall confidence score")
    field_results: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-field validation: {field_name: {valid, confidence, issues}}",
    )
    corrected_values: Dict[str, Any] = Field(default_factory=dict, description="LLM-suggested corrections")
    summary: str = Field(default="", description="Human-readable validation summary")
    validated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class EntityExtraction(BaseModel):
    """Structured entity extraction result from an LLM."""

    entities: Dict[str, List[Dict[str, str]]] = Field(
        default_factory=dict,
        description="Extracted entities grouped by type, e.g. {'medications': [{name, dosage}], ...}",
    )
    raw_response: str = Field(default="", description="Raw LLM response text")
    extraction_model: str = Field(default="", description="LLM model used")
    token_usage: Dict[str, int] = Field(default_factory=dict, description="Token usage stats")
    extracted_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# =============================================================================
# Prompt Templates
# =============================================================================

_PROMPT_SUMMARIZE_CLINICAL_NOTES = """You are a medical documentation assistant. Summarize the following clinical notes concisely in the same language they are written (Arabic or English). Include:
1. Chief complaint
2. Key findings
3. Diagnoses
4. Medications prescribed
5. Follow-up instructions

Clinical Notes:
{text}

Summary:"""

_PROMPT_MEDICAL_QA = """You are a medical knowledge assistant. Answer the following medical question based on the provided context. If the answer is not in the context, say "Information not available in the provided context."

Context:
{context}

Question:
{question}

Answer:"""

_PROMPT_EXTRACT_ENTITIES = """Extract structured medical entities from the following text. Return a valid JSON object with these keys:
- "medications": list of {{"name": str, "dosage": str, "frequency": str, "route": str}}
- "diagnoses": list of {{"code": str, "description": str, "severity": str}}
- "lab_results": list of {{"test_name": str, "value": str, "unit": str, "reference_range": str}}
- "vital_signs": {{"systolic_bp": str, "diastolic_bp": str, "heart_rate": str, "temperature": str, "spo2": str, "respiratory_rate": str}}
- "patient_info": {{"name": str, "age": str, "gender": str, "patient_id": str}}

Text:
{text}

JSON:"""

_PROMPT_VALIDATE_EXTRACTION = """You are a medical data quality validator. Compare the extracted structured data against the original source text. Check for:
1. Missing entities that should have been extracted
2. Incorrect values or misread numbers
3. Hallucinated data not present in the source

Source text:
{source_text}

Extracted data:
{extracted_data}

Return a JSON object with:
- "is_valid": boolean
- "confidence": float (0-1)
- "field_results": object with per-field validation
- "corrected_values": object with any corrections
- "summary": string description

JSON:"""


# =============================================================================
# LLMIntegration
# =============================================================================


class LLMIntegration:
    """
    Unified LLM interface for medical AI tasks.

    Supports multiple providers:
        * **OpenAI** — ``gpt-4``, ``gpt-4o``, ``gpt-3.5-turbo``, etc.
        * **Local / self-hosted** — Any OpenAI-compatible API endpoint.
        * **LangChain** — Any LangChain-compatible ``BaseChatModel``.

    Provides pre-built methods for common medical tasks:
        * Clinical note summarisation
        * Medical Q&A with context
        * Entity extraction to structured JSON
        * Extraction validation and correction
    """

    def __init__(self, config: Optional[LLMConfig] = None):
        """
        Args:
            config: LLM configuration.  Uses defaults when *None*.
        """
        self.config = config or LLMConfig()
        self._llm = None
        self._llm_initialized = False

        logger.info(
            "LLMIntegration initialised (provider=%s, model=%s)",
            self.config.provider,
            self.config.model or "default",
        )

    # ------------------------------------------------------------------
    # LLM Initialisation
    # ------------------------------------------------------------------

    def initialize_llm(self, provider: Optional[str] = None, model: Optional[str] = None) -> None:
        """
        Initialise (or re-initialise) the LLM backend.

        Args:
            provider: Override provider name (``openai``, ``local``, ``langchain``).
            model: Override model name.

        Raises:
            ImportError: If required packages are not installed.
            RuntimeError: If initialisation fails.
        """
        if provider:
            self.config.provider = provider
        if model:
            self.config.model = model

        try:
            if self.config.provider == "openai":
                self._init_openai()
            elif self.config.provider == "local":
                self._init_local()
            elif self.config.provider == "langchain":
                self._init_langchain()
            else:
                raise ValueError(f"Unknown provider: {self.config.provider}")

            self._llm_initialized = True
            logger.info(
                "LLM initialised successfully (provider=%s, model=%s)",
                self.config.provider,
                self.config.model or "default",
            )

        except Exception as exc:
            self._llm_initialized = False
            logger.error("LLM initialisation failed: %s", exc)
            raise

    def _init_openai(self) -> None:
        """Initialise an OpenAI-compatible client."""
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            try:
                from langchain.chat_models import ChatOpenAI
            except ImportError:
                raise ImportError(
                    "langchain-openai is required for the 'openai' provider. "
                    "Install with: pip install langchain-openai"
                )

        kwargs: Dict[str, Any] = {
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "timeout": self.config.timeout,
            "max_retries": self.config.max_retries,
        }

        if self.config.model:
            kwargs["model"] = self.config.model
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        elif settings.ENVIRONMENT == "production":
            logger.warning("No API key provided for OpenAI; requests may fail")
        if self.config.base_url:
            kwargs["base_url"] = self.config.base_url

        self._llm = ChatOpenAI(**kwargs)

    def _init_local(self) -> None:
        """Initialise a local / self-hosted model via OpenAI-compatible API."""
        self._init_openai()  # Same interface, just with base_url pointing to local

    def _init_langchain(self) -> None:
        """Initialise a generic LangChain model (requires manual setup)."""
        try:
            from langchain.chat_models.base import BaseChatModel
        except ImportError:
            raise ImportError(
                "langchain is required. Install with: pip install langchain"
            )

        if self._llm is None:
            raise RuntimeError(
                "For the 'langchain' provider, you must set `llm_integration._llm` "
                "to a LangChain BaseChatModel instance before calling methods."
            )

    def _ensure_initialized(self) -> None:
        """Ensure the LLM has been initialised before use."""
        if not self._llm_initialized or self._llm is None:
            logger.info("LLM not yet initialised — initialising with default config")
            self.initialize_llm()

    # ------------------------------------------------------------------
    # Medical Task Methods
    # ------------------------------------------------------------------

    def medical_qa(self, question: str, context: str) -> str:
        """
        Answer a medical question using the provided context.

        Args:
            question: The medical question to answer.
            context: Reference text (e.g. retrieved document chunks).

        Returns:
            The LLM-generated answer string.

        Raises:
            RuntimeError: If the LLM call fails.
        """
        self._ensure_initialized()

        prompt = _PROMPT_MEDICAL_QA.format(context=context, question=question)
        return self._call_llm(prompt)

    def summarize_clinical_notes(self, notes: str) -> str:
        """
        Generate a concise summary of clinical notes.

        The summary preserves the language of the input (Arabic or English).

        Args:
            notes: Raw clinical note text.

        Returns:
            A structured summary string.
        """
        self._ensure_initialized()

        prompt = _PROMPT_SUMMARIZE_CLINICAL_NOTES.format(text=notes[:6000])
        return self._call_llm(prompt)

    def extract_entities(self, text: str) -> Dict[str, Any]:
        """
        Extract structured medical entities from text using the LLM.

        Returns a dict with keys ``medications``, ``diagnoses``,
        ``lab_results``, ``vital_signs``, ``patient_info``.

        Args:
            text: Medical text to extract entities from.

        Returns:
            A dict of extracted entities (parsed from JSON).
        """
        self._ensure_initialized()

        prompt = _PROMPT_EXTRACT_ENTITIES.format(text=text[:6000])
        raw_response = self._call_llm(prompt)

        entities = self._parse_json_response(raw_response)

        return entities

    def validate_extraction(self, source_text: str, extracted_data: Dict[str, Any]) -> ValidationReport:
        """
        Validate extracted medical data against the original source text
        using the LLM.

        Checks for missing entities, incorrect values, and hallucinations.

        Args:
            source_text: Original medical document text.
            extracted_data: Previously extracted structured data.

        Returns:
            A :class:`ValidationReport` with validation results and corrections.
        """
        self._ensure_initialized()

        extract_json = json.dumps(extracted_data, ensure_ascii=False, indent=2)
        prompt = _PROMPT_VALIDATE_EXTRACTION.format(
            source_text=source_text[:4000],
            extracted_data=extract_json[:3000],
        )

        raw_response = self._call_llm(prompt)

        # Parse the validation result
        try:
            result = self._parse_json_response(raw_response)

            return ValidationReport(
                is_valid=result.get("is_valid", False),
                confidence=float(result.get("confidence", 0.0)),
                field_results=result.get("field_results", {}),
                corrected_values=result.get("corrected_values", {}),
                summary=result.get("summary", ""),
            )
        except Exception as exc:
            logger.error("Failed to parse validation response: %s", exc)
            return ValidationReport(
                is_valid=False,
                confidence=0.0,
                summary=f"Failed to parse LLM validation response: {exc}",
            )

    # ------------------------------------------------------------------
    # Low-level LLM Call
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str) -> str:
        """
        Invoke the LLM with a single prompt string.

        Args:
            prompt: The complete prompt to send.

        Returns:
            The LLM response text.

        Raises:
            RuntimeError: On any LLM call failure.
        """
        try:
            from langchain_core.messages import HumanMessage

            messages = [HumanMessage(content=prompt)]
            response = self._llm.invoke(messages)

            # Extract text from response
            if hasattr(response, "content"):
                return response.content
            elif isinstance(response, str):
                return response
            else:
                return str(response)

        except Exception as exc:
            logger.error("LLM call failed: %s", exc)
            raise RuntimeError(f"LLM call failed: {exc}") from exc

    # ------------------------------------------------------------------
    # JSON Parsing Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json_response(raw_response: str) -> Dict[str, Any]:
        """
        Parse a JSON object from an LLM response, handling common issues
        like markdown code fences, extra whitespace, and partial JSON.

        Args:
            raw_response: Raw LLM output text.

        Returns:
            Parsed dict, or an empty dict if parsing fails.
        """
        text = raw_response.strip()

        # Strip markdown code fences
        fence_pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
        match = re.search(fence_pattern, text, re.DOTALL)
        if match:
            text = match.group(1).strip()

        # Try to find JSON object boundaries
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find the first { ... } block
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

        logger.warning("Could not parse JSON from LLM response (first 200 chars): %s", text[:200])
        return {}

    # ------------------------------------------------------------------
    # Convenience: Build a chain for complex workflows
    # ------------------------------------------------------------------

    def build_medical_chain(self, steps: List[Dict[str, str]]) -> Any:
        """
        Build a LangChain chain from a list of prompt steps.

        Each step is a dict with:
            * ``name`` — human-readable step name
            * ``template`` — prompt template with ``{input}`` placeholder
            * ``output_key`` — key to pass to the next step

        Example::

            chain = llm.build_medical_chain([
                {
                    "name": "extract",
                    "template": "Extract entities: {input}",
                    "output_key": "extracted",
                },
                {
                    "name": "validate",
                    "template": "Validate: {extracted}",
                    "output_key": "validated",
                },
            ])
            result = chain.invoke({"input": "Patient has diabetes..."})

        Args:
            steps: Ordered list of step definitions.

        Returns:
            A LangChain chain object (SequentialChain).
        """
        self._ensure_initialized()

        try:
            from langchain.prompts import PromptTemplate
            from langchain.chains import LLMChain, SequentialChain

            chain_steps: List[Any] = []
            output_keys: List[str] = []

            for step in steps:
                prompt = PromptTemplate(
                    input_variables=["input"],
                    template=step["template"],
                )
                llm_chain = LLMChain(llm=self._llm, prompt=prompt, output_key=step["output_key"])
                chain_steps.append(llm_chain)
                output_keys.append(step["output_key"])

            chain = SequentialChain(
                chains=chain_steps,
                input_variables=["input"],
                output_variables=output_keys,
                verbose=True,
            )

            logger.info("Built medical chain with %d steps", len(steps))
            return chain

        except ImportError:
            raise ImportError(
                "langchain is required for chain building. "
                "Install with: pip install langchain"
            )
