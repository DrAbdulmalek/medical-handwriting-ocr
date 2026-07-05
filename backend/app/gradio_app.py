"""
Interactive Gradio UI for Medical Data Analysis Platform.

Provides four tab-based interfaces:
1. OCR Correction — Upload image, view OCR results, and get correction suggestions.
2. Document Parser — Upload PDF/DOCX/PPTX files and extract structured text.
3. Medical Analysis — Extract vitals, medications, diagnoses from free-form text.
4. Clinical QA — Ask evidence-based clinical questions with citations.
"""

import json
import logging
import os
import tempfile

import gradio as gr

# ── Local application imports (run from backend/ directory) ──────────────
from app.ocr_engine import ocr_engine
from app.parsers.document_parser import document_parser
from app.ai.schema_extractor import MedicalSchemaExtractor
from app.clinical.clinical_qa import ClinicalQA

logger = logging.getLogger(__name__)

# ── Initialize singletons ───────────────────────────────────────────────
schema_extractor = MedicalSchemaExtractor(use_llm_fallback=False)
clinical_qa = ClinicalQA()


# =============================================================================
# Tab 1: OCR Correction
# =============================================================================


def perform_ocr(image_path: str):
    """
    Run OCR on an uploaded image and return detected text regions.

    Args:
        image_path: Path to the uploaded image file.

    Returns:
        Tuple of (markdown results string, json results string).
    """
    if not image_path:
        return "No image provided.", "{}"

    try:
        regions = ocr_engine.detect_regions(image_path)

        if not regions:
            return "No text regions detected in the image.", "{}"

        # Build markdown output
        md_lines = ["# OCR Results\n"]
        for idx, region in enumerate(regions, 1):
            bbox = region["bbox"]
            md_lines.append(
                f"## Region {idx} (Order: {region['reading_order']})\n"
                f"- **Text:** `{region['predicted_text']}`\n"
                f"- **Confidence:** {region['confidence']:.2%}\n"
                f"- **BBox:** x1={bbox['x1']}, y1={bbox['y1']}, "
                f"x2={bbox['x2']}, y2={bbox['y2']}\n"
            )

        # Also provide raw JSON
        return "\n".join(md_lines), json.dumps(regions, indent=2, ensure_ascii=False)

    except Exception as exc:
        logger.exception("OCR processing failed")
        return f"Error during OCR: {exc}", "{}"


def get_suggestions(text: str):
    """
    Get correction suggestions for OCR output text.

    Args:
        text: The OCR-predicted text to analyze.

    Returns:
        Markdown-formatted suggestions.
    """
    if not text or not text.strip():
        return "Please enter text to get suggestions."

    try:
        # Classify the script
        script_type = ocr_engine.classify_script(text)

        suggestions = [
            "# Correction Suggestions\n",
            f"- **Detected script:** {script_type}\n",
            "\n### Recommendations:\n",
        ]

        # Check for common OCR issues
        issues = []
        if any(c.isdigit() for c in text) and script_type in ("arabic", "mixed"):
            issues.append("Mixed Arabic/Latin numerals detected — verify digit recognition.")

        if len(text.split()) > 50:
            issues.append("Large text block — consider splitting into smaller regions for better accuracy.")

        if text.strip() and not text.strip()[0].isalpha() and not text.strip()[0].isdigit():
            issues.append("Text starts with a non-alphanumeric character — may indicate detection noise.")

        if issues:
            for issue in issues:
                suggestions.append(f"- ⚠️ {issue}")
        else:
            suggestions.append("- ✅ Text looks clean. No common OCR issues detected.")

        suggestions.append("\n### Next Steps:\n")
        suggestions.append("1. Review the detected text above\n")
        suggestions.append("2. Apply manual corrections if needed\n")
        suggestions.append("3. Use the Medical Analysis tab to extract structured data\n")

        return "\n".join(suggestions)

    except Exception as exc:
        logger.exception("Suggestion generation failed")
        return f"Error generating suggestions: {exc}"


# =============================================================================
# Tab 2: Document Parser
# =============================================================================


def parse_document(file_path: str):
    """
    Parse an uploaded document (PDF/DOCX/PPTX) and extract text.

    Args:
        file_path: Path to the uploaded file.

    Returns:
        Markdown-formatted parse results.
    """
    if not file_path:
        return "No file provided.", "{}"

    file_ext = os.path.splitext(file_path)[1].lower()
    supported_formats = [".pdf", ".docx", ".pptx", ".html", ".htm"]

    if file_ext not in supported_formats:
        return (
            f"Unsupported file format: `{file_ext}`. Supported: {', '.join(supported_formats)}",
            "{}",
        )

    try:
        result = document_parser.parse_document(file_path)

        md_lines = [
            f"# Document Parse Results\n",
            f"- **File:** `{result.file_name}`\n"
            f"- **Type:** `{result.file_type}`\n"
            f"- **Pages:** {result.page_count}\n"
            f"- **Tables:** {result.total_tables}\n"
            f"- **Images:** {result.total_images}\n"
            f"- **Arabic:** {'Yes' if result.has_arabic else 'No'}\n"
            f"- **Processing time:** {result.processing_time_ms:.1f} ms\n",
        ]

        if result.warnings:
            md_lines.append("### Warnings\n")
            for w in result.warnings:
                md_lines.append(f"- ⚠️ {w}\n")

        md_lines.append("\n## Extracted Text\n")
        for page in result.pages:
            md_lines.append(f"### Page {page.page_number}\n")
            if page.text:
                md_lines.append(page.text)
            else:
                md_lines.append("*No text extracted from this page.*\n")

        md_json = {
            "document_id": result.document_id,
            "file_name": result.file_name,
            "file_type": result.file_type,
            "page_count": result.page_count,
            "total_tables": result.total_tables,
            "total_images": result.total_images,
            "has_arabic": result.has_arabic,
            "processing_time_ms": result.processing_time_ms,
            "warnings": result.warnings,
        }

        return "\n".join(md_lines), json.dumps(md_json, indent=2, ensure_ascii=False)

    except Exception as exc:
        logger.exception("Document parsing failed")
        return f"Error parsing document: {exc}", "{}"


# =============================================================================
# Tab 3: Medical Analysis
# =============================================================================


def analyze_medical_text(text: str):
    """
    Extract structured medical data from free-form text.

    Args:
        text: Medical text (OCR output, clinical notes, prescriptions).

    Returns:
        Markdown-formatted analysis results.
    """
    if not text or not text.strip():
        return "Please enter medical text to analyze.", "{}"

    try:
        extract = schema_extractor.extract_all(text)

        md_lines = ["# Medical Data Analysis\n"]

        # Vital Signs
        vs = extract.vital_signs
        md_lines.append("## Vital Signs\n")
        vitals_data = []
        if vs.systolic_bp is not None:
            vitals_data.append(f"**BP:** {vs.systolic_bp}/{vs.diastolic_bp} mmHg")
        if vs.heart_rate is not None:
            vitals_data.append(f"**HR:** {vs.heart_rate} bpm")
        if vs.temperature is not None:
            vitals_data.append(f"**Temp:** {vs.temperature}°C")
        if vs.spo2 is not None:
            vitals_data.append(f"**SpO2:** {vs.spo2}%")
        if vs.respiratory_rate is not None:
            vitals_data.append(f"**RR:** {vs.respiratory_rate} br/min")
        md_lines.append("\n".join(f"- {v}" for v in vitals_data) if vitals_data else "- No vital signs detected.\n")

        # Medications
        md_lines.append("\n## Medications\n")
        if extract.medications:
            for med in extract.medications:
                med_parts = [f"**{med.name}**"]
                if med.dosage:
                    med_parts.append(med.dosage)
                if med.frequency:
                    med_parts.append(med.frequency)
                if med.route:
                    med_parts.append(f"({med.route})")
                if med.duration:
                    med_parts.append(f"for {med.duration}")
                md_lines.append(f"- {' '.join(med_parts)}")
        else:
            md_lines.append("- No medications detected.\n")

        # Diagnoses
        md_lines.append("\n## Diagnoses\n")
        if extract.diagnoses:
            for diag in extract.diagnoses:
                diag_line = f"- **{diag.description}**"
                if diag.code:
                    diag_line += f" ({diag.code})"
                if diag.severity:
                    diag_line += f" — severity: {diag.severity}"
                if diag.chronic:
                    diag_line += " ⚠️ chronic"
                md_lines.append(diag_line)
        else:
            md_lines.append("- No diagnoses detected.\n")

        # Lab Results
        md_lines.append("\n## Lab Results\n")
        if extract.lab_results:
            for lab in extract.lab_results:
                lab_line = f"- **{lab.test_name}**: {lab.value}"
                if lab.unit:
                    lab_line += f" {lab.unit}"
                if lab.reference_range:
                    lab_line += f" (ref: {lab.reference_range})"
                if lab.is_abnormal:
                    flag = "🔴" if lab.status == "high" else "🟡"
                    lab_line += f" {flag} {lab.status or 'abnormal'}"
                md_lines.append(lab_line)
        else:
            md_lines.append("- No lab results detected.\n")

        # Patient Info
        md_lines.append("\n## Patient Info\n")
        pi = extract.patient_info
        patient_data = []
        if pi.name:
            patient_data.append(f"**Name:** {pi.name}")
        if pi.age:
            patient_data.append(f"**Age:** {pi.age}")
        if pi.gender:
            patient_data.append(f"**Gender:** {pi.gender}")
        if pi.patient_id:
            patient_data.append(f"**Patient ID:** {pi.patient_id}")
        if pi.allergies:
            patient_data.append(f"**Allergies:** {', '.join(pi.allergies)}")
        md_lines.append(
            "\n".join(f"- {d}" for d in patient_data) if patient_data else "- No patient info detected.\n"
        )

        # Warnings
        if extract.warnings:
            md_lines.append("\n### ⚠️ Warnings\n")
            for w in extract.warnings:
                md_lines.append(f"- {w}")

        # Confidence scores
        md_lines.append("\n## Confidence Scores\n")
        for category, score in extract.confidence_scores.items():
            bar_len = int(score * 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            md_lines.append(f"- **{category}**: [{bar}] {score:.0%}")

        # JSON output
        md_json = extract.model_dump(mode="json", exclude={"id"})

        return "\n".join(md_lines), json.dumps(md_json, indent=2, ensure_ascii=False)

    except Exception as exc:
        logger.exception("Medical analysis failed")
        return f"Error during analysis: {exc}", "{}"


# =============================================================================
# Tab 4: Clinical QA
# =============================================================================


def ask_clinical_question(question: str, patient_context: str = ""):
    """
    Answer a clinical question with evidence citations.

    Args:
        question: The clinical question (supports Arabic).
        patient_context: Optional patient context JSON string.

    Returns:
        Markdown-formatted answer with evidence.
    """
    if not question or not question.strip():
        return "Please enter a clinical question."

    try:
        context = None
        if patient_context and patient_context.strip():
            try:
                context = json.loads(patient_context)
            except json.JSONDecodeError:
                context = {"raw_context": patient_context}

        answer = clinical_qa.ask_clinical_question(question, patient_context=context)

        # Run async
        import asyncio
        answer = asyncio.run(answer) if not hasattr(answer, '__await__') else answer

        # Gradio blocks — try awaiting
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Use nest_asyncio for running inside Gradio
                import nest_asyncio
                nest_asyncio.apply()
                answer = loop.run_until_complete(
                    clinical_qa.ask_clinical_question(question, patient_context=context)
                )
            else:
                answer = loop.run_until_complete(
                    clinical_qa.ask_clinical_question(question, patient_context=context)
                )
        except RuntimeError:
            answer = asyncio.run(
                clinical_qa.ask_clinical_question(question, patient_context=context)
            )

        md_lines = [
            "# Clinical Answer\n",
            f"## Question\n{question}\n",
            f"## Answer\n{answer.answer}\n",
            f"**Confidence:** {answer.confidence:.0%}\n",
        ]

        if answer.answer_ar:
            md_lines.append(f"\n### Arabic Response\n{answer.answer_ar}\n")

        if answer.evidence:
            md_lines.append("\n## Evidence\n")
            for i, ev in enumerate(answer.evidence, 1):
                md_lines.append(
                    f"### {i}. {ev.source}\n"
                    f"- **Level:** {ev.level.value}\n"
                    f"- **Excerpt:** {ev.excerpt}\n"
                )

        if answer.related_conditions:
            md_lines.append(f"\n**Related conditions:** {', '.join(answer.related_conditions)}\n")

        md_lines.append(f"\n*{answer.disclaimer}*\n")

        return "\n".join(md_lines)

    except Exception as exc:
        logger.exception("Clinical QA failed")
        return f"Error answering question: {exc}"


# =============================================================================
# Build Gradio Interface
# =============================================================================


def build_gradio_app():
    """Construct and return the Gradio Blocks interface."""

    with gr.Blocks(
        title="Medical Data Analysis Platform",
        theme=gr.themes.Soft(),
        css="""
            .contain { max-width: 1200px; margin: auto; padding: 20px; }
            footer { display: none !important; }
            .warning { color: #f59e0b; font-weight: 600; }
        """,
    ) as demo:

        gr.Markdown(
            """
            # 🏥 Medical Data Analysis Platform
            ### التصحيح الطبي — Interactive OCR, Document Parsing & Clinical Analysis

            Upload medical documents, extract text, and get AI-powered clinical insights.
            """
        )

        with gr.Tabs():
            # ── Tab 1: OCR Correction ───────────────────────────────────
            with gr.Tab("🔍 OCR Correction"):
                with gr.Row():
                    with gr.Column(scale=1):
                        ocr_image_input = gr.Image(
                            type="filepath",
                            label="Upload Image (Prescription / Handwritten Note)",
                            elem_id="ocr-input",
                        )
                        ocr_btn = gr.Button(
                            "Run OCR",
                            variant="primary",
                            size="lg",
                        )

                    with gr.Column(scale=2):
                        ocr_md_output = gr.Markdown(
                            label="OCR Results",
                            elem_classes=["contain"],
                        )
                        ocr_json_output = gr.JSON(label="Raw JSON")

                gr.Markdown("---")
                gr.Markdown("### Enter OCR text to get correction suggestions:")
                with gr.Row():
                    ocr_text_input = gr.Textbox(
                        label="OCR Text",
                        placeholder="Paste OCR output here...",
                        lines=4,
                    )
                    ocr_suggest_btn = gr.Button("Get Suggestions")
                ocr_suggestions_output = gr.Markdown(label="Suggestions")

                ocr_btn.click(
                    fn=perform_ocr,
                    inputs=[ocr_image_input],
                    outputs=[ocr_md_output, ocr_json_output],
                )
                ocr_suggest_btn.click(
                    fn=get_suggestions,
                    inputs=[ocr_text_input],
                    outputs=[ocr_suggestions_output],
                )

            # ── Tab 2: Document Parser ─────────────────────────────────
            with gr.Tab("📄 Document Parser"):
                with gr.Row():
                    with gr.Column(scale=1):
                        doc_file_input = gr.File(
                            label="Upload Document",
                            file_types=[".pdf", ".docx", ".pptx", ".html"],
                            type="filepath",
                        )
                        doc_btn = gr.Button(
                            "Parse Document",
                            variant="primary",
                            size="lg",
                        )

                    with gr.Column(scale=2):
                        doc_md_output = gr.Markdown(
                            label="Parse Results",
                            elem_classes=["contain"],
                        )
                        doc_json_output = gr.JSON(label="Summary JSON")

                doc_btn.click(
                    fn=parse_document,
                    inputs=[doc_file_input],
                    outputs=[doc_md_output, doc_json_output],
                )

            # ── Tab 3: Medical Analysis ────────────────────────────────
            with gr.Tab("🧬 Medical Analysis"):
                with gr.Row():
                    with gr.Column(scale=1):
                        analysis_text_input = gr.Textbox(
                            label="Medical Text",
                            placeholder=(
                                "Paste OCR output, clinical notes, or prescription text...\n\n"
                                "Supports Arabic (العربية) and English."
                            ),
                            lines=10,
                        )
                        analysis_btn = gr.Button(
                            "Analyze",
                            variant="primary",
                            size="lg",
                        )

                    with gr.Column(scale=2):
                        analysis_md_output = gr.Markdown(
                            label="Analysis Results",
                            elem_classes=["contain"],
                        )
                        analysis_json_output = gr.JSON(label="Structured JSON")

                analysis_btn.click(
                    fn=analyze_medical_text,
                    inputs=[analysis_text_input],
                    outputs=[analysis_md_output, analysis_json_output],
                )

            # ── Tab 4: Clinical QA ─────────────────────────────────────
            with gr.Tab("🩺 Clinical QA"):
                with gr.Row():
                    with gr.Column(scale=1):
                        qa_question_input = gr.Textbox(
                            label="Clinical Question",
                            placeholder=(
                                "Ask a clinical question...\n\n"
                                "Examples:\n"
                                '- "What is the interaction between warfarin and aspirin?"\n'
                                '- "What is the first-line treatment for type 2 diabetes?"\n'
                                '- "What is the dosage of amoxicillin for a 70kg adult?"\n'
                                '- "ما هي الأعراض الجانبية لميتفورمين؟"'
                            ),
                            lines=5,
                        )
                        qa_context_input = gr.Textbox(
                            label="Patient Context (optional JSON)",
                            placeholder=(
                                '{"age": 65, "weight": 80, "conditions": ["hypertension"]}'
                            ),
                            lines=3,
                        )
                        qa_btn = gr.Button(
                            "Ask Question",
                            variant="primary",
                            size="lg",
                        )

                    with gr.Column(scale=2):
                        qa_output = gr.Markdown(
                            label="Answer",
                            elem_classes=["contain"],
                        )

                qa_btn.click(
                    fn=ask_clinical_question,
                    inputs=[qa_question_input, qa_context_input],
                    outputs=[qa_output],
                )

        gr.Markdown(
            """
            ---
            **Disclaimer:** This tool is for clinical decision support only and does not
            replace professional medical judgment. Always verify results with qualified
            healthcare professionals.
            """
        )

    return demo


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    demo = build_gradio_app()
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_error=True,
    )
