"""
Interactive Gradio UI for Medical Data Analysis Platform — HuggingFace Spaces Edition.

Provides four tab-based interfaces:
1. **OCR Correction** — Upload image → annotated bboxes → editable word crops → ground truth collection
2. **Document Parser** — Upload PDF/DOCX/PPTX files and extract structured text
3. **Medical Analysis** — Extract vitals, medications, diagnoses from free-form text
4. **Clinical QA** — Ask evidence-based clinical questions with citations

Key fixes over the original deployment:
- Arabic text is correctly reshaped and reordered for RTL display (python-bidi + arabic-reshaper)
- Each detected word shows its crop + editable text + confidence + ground truth input
- Ground truth can be collected and exported as JSON training data
"""

import asyncio
import base64
import json
import logging
import os
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import cv2
import gradio as gr
import numpy as np
from PIL import Image

from app.ocr_engine import ocr_engine
from app.ai.schema_extractor import MedicalSchemaExtractor
from app.clinical.clinical_qa import ClinicalQA

logger = logging.getLogger(__name__)

# ── Initialise singletons ───────────────────────────────────────────────
schema_extractor = MedicalSchemaExtractor(use_llm_fallback=False)
clinical_qa = ClinicalQA()

# ── Global state for current OCR session ─────────────────────────────────
_current_regions: List[Dict] = []


# =============================================================================
# Helpers
# =============================================================================


def _base64_to_pil(b64_str: str) -> Optional[Image.Image]:
    """Decode a base64 PNG string to a PIL Image."""
    try:
        data = base64.b64decode(b64_str)
        return Image.open(BytesIO(data)).convert("RGB")
    except Exception as exc:
        logger.warning("Failed to decode base64 image: %s", exc)
        return None


def draw_bboxes(
    image_path: str,
    regions: List[Dict],
) -> np.ndarray:
    """Draw numbered, colour-coded bounding boxes on the image.

    Colour coding:
    - GREEN  → confidence > 90 %
    - YELLOW → 70 % ≤ confidence ≤ 90 %
    - RED    → confidence < 70 %
    """
    img = cv2.imread(image_path)
    if img is None:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    overlay = img.copy()

    for idx, region in enumerate(regions):
        bbox = region["bbox"]
        conf = region.get("confidence", 0.0)

        if conf > 0.90:
            colour = (0, 200, 0)       # green
        elif conf > 0.70:
            colour = (0, 200, 255)     # yellow
        else:
            colour = (0, 80, 255)      # red

        x1, y1 = int(bbox["x1"]), int(bbox["y1"])
        x2, y2 = int(bbox["x2"]), int(bbox["y2"])

        cv2.rectangle(overlay, (x1, y1), (x2, y2), colour, 2)

        label = f"{idx + 1}  {conf:.0%}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(overlay, (x1, y1 - th - 6), (x1 + tw + 4, y1), colour, -1)
        cv2.putText(overlay, label, (x1 + 2, y1 - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    annotated = cv2.addWeighted(img, 0.7, overlay, 0.3, 0)
    return cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)


# =============================================================================
# Tab 1: OCR Correction (MAIN — completely redesigned)
# =============================================================================


def perform_ocr(image_path: str):
    """Run OCR and return annotated image + gallery + editable texts."""
    global _current_regions

    if not image_path:
        _current_regions = []
        return (
            None,
            [],
            "",
            "",
            "Upload an image to begin.",
        )

    try:
        regions = ocr_engine.detect_regions_with_crops(image_path)

        if not regions:
            _current_regions = []
            return (
                None,
                [],
                "",
                "",
                "No text regions detected in the image.",
            )

        _current_regions = regions

        # Build annotated image
        annotated = draw_bboxes(image_path, regions)

        # Build gallery: list of (PIL Image, label) tuples
        gallery_items = []
        for idx, region in enumerate(regions):
            pil_img = _base64_to_pil(region["crop_base64"])
            if pil_img is None:
                continue
            conf_pct = f"{region['confidence']:.0%}"
            gallery_items.append(
                (pil_img, f"#{idx + 1} — {region['predicted_text']}  ({conf_pct})")
            )

        # Build editable predicted text: one per line
        predicted_lines = "\n".join(r["predicted_text"] for r in regions)

        # Build summary markdown
        summary_md = f"## Detected {len(regions)} text regions\n\n"
        summary_md += "| # | Text | Confidence |\n|---|------|------------|\n"
        for idx, region in enumerate(regions):
            conf_bar = "🟢" if region["confidence"] > 0.9 else ("🟡" if region["confidence"] > 0.7 else "🔴")
            summary_md += f"| {idx + 1} | {region['predicted_text']} | {conf_bar} {region['confidence']:.0%} |\n"

        return (
            annotated,
            gallery_items,
            predicted_lines,
            "",  # ground truth textarea starts empty
            summary_md,
        )

    except Exception as exc:
        logger.exception("OCR processing failed")
        _current_regions = []
        return None, [], "", "", f"Error during OCR: {exc}"


def save_corrections(predicted_text: str, ground_truth: str):
    """Collect corrections and ground truth, return JSON training data.

    *predicted_text*: one predicted text per line (editable)
    *ground_truth*: one ground-truth per line (matching order)
    """
    global _current_regions

    if not _current_regions:
        return json.dumps({"error": "No OCR data. Run OCR first."}, ensure_ascii=False, indent=2)

    pred_lines = predicted_text.strip().split("\n") if predicted_text.strip() else []
    gt_lines = ground_truth.strip().split("\n") if ground_truth.strip() else []

    training_data = []

    for i, region in enumerate(_current_regions):
        edited_pred = pred_lines[i] if i < len(pred_lines) else region["predicted_text"]
        gt = gt_lines[i] if i < len(gt_lines) else ""

        entry = {
            "region_idx": i,
            "reading_order": region.get("reading_order", i),
            "predicted_text": edited_pred,
            "original_predicted": region["predicted_text"],
            "confidence": region["confidence"],
            "bbox": region["bbox"],
        }

        if gt.strip():
            entry["ground_truth"] = gt.strip()
            entry["crop_base64"] = region.get("crop_base64", "")

        training_data.append(entry)

    result = {
        "total_regions": len(_current_regions),
        "regions_with_ground_truth": sum(1 for e in training_data if "ground_truth" in e),
        "regions_with_corrections": sum(
            1 for e in training_data
            if e["predicted_text"] != e["original_predicted"]
        ),
        "data": training_data,
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


def get_correction_suggestions(text: str):
    """Provide correction suggestions for OCR output text."""
    if not text or not text.strip():
        return "Please enter text to get suggestions."

    script_type = ocr_engine.classify_script(text)

    suggestions = [
        "# Correction Suggestions\n",
        f"- **Detected script:** {script_type}\n",
        "\n### Recommendations:\n",
    ]

    issues = []
    if any(c.isdigit() for c in text) and script_type in ("arabic", "mixed"):
        issues.append("Mixed Arabic/Latin numerals detected — verify digit recognition.")
    if len(text.split()) > 50:
        issues.append("Large text block — consider splitting into smaller regions.")
    if text.strip() and not text.strip()[0].isalpha() and not text.strip()[0].isdigit():
        issues.append("Text starts with a non-alphanumeric character — may indicate noise.")

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


# =============================================================================
# Tab 2: Document Parser
# =============================================================================


def parse_document(file_path: str):
    """Parse an uploaded document and return extracted text."""
    if not file_path:
        return "No file provided.", "{}"

    file_ext = os.path.splitext(file_path)[1].lower()
    supported_formats = [".pdf", ".docx", ".pptx", ".html", ".htm"]

    if file_ext not in supported_formats:
        return (
            f"Unsupported format: `{file_ext}`. Supported: {', '.join(supported_formats)}",
            "{}",
        )

    try:
        from app.parsers.document_parser import document_parser

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
            md_lines.append(page.text if page.text else "*No text extracted.*\n")

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
    """Extract structured medical data from free-form text."""
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
        md_lines.append(
            "\n".join(f"- {v}" for v in vitals_data)
            if vitals_data
            else "- No vital signs detected.\n"
        )

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
            "\n".join(f"- {d}" for d in patient_data)
            if patient_data
            else "- No patient info detected.\n"
        )

        if extract.warnings:
            md_lines.append("\n### ⚠️ Warnings\n")
            for w in extract.warnings:
                md_lines.append(f"- {w}")

        md_lines.append("\n## Confidence Scores\n")
        for category, score in extract.confidence_scores.items():
            bar_len = int(score * 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            md_lines.append(f"- **{category}**: [{bar}] {score:.0%}")

        md_json = extract.model_dump(mode="json", exclude={"id"})
        return "\n".join(md_lines), json.dumps(md_json, indent=2, ensure_ascii=False)

    except Exception as exc:
        logger.exception("Medical analysis failed")
        return f"Error during analysis: {exc}", "{}"


# =============================================================================
# Tab 4: Clinical QA
# =============================================================================


def ask_clinical_question(question: str, patient_context: str = ""):
    """Answer a clinical question with evidence citations."""
    if not question or not question.strip():
        return "Please enter a clinical question."

    try:
        context = None
        if patient_context and patient_context.strip():
            try:
                context = json.loads(patient_context)
            except json.JSONDecodeError:
                context = {"raw_context": patient_context}

        try:
            answer = asyncio.run(
                clinical_qa.ask_clinical_question(question, patient_context=context)
            )
        except RuntimeError:
            loop = asyncio.new_event_loop()
            answer = loop.run_until_complete(
                clinical_qa.ask_clinical_question(question, patient_context=context)
            )
            loop.close()

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
        title="Medical Handwriting OCR",
        theme=gr.themes.Soft(),
        css="""
            .contain { max-width: 1200px; margin: auto; padding: 20px; }
            footer { display: none !important; }
        """,
    ) as demo:

        gr.Markdown(
            """
            # 🏥 Medical Handwriting OCR
            ### التصحيح الطبي — Interactive OCR, Document Parsing & Clinical Analysis

            Upload medical documents, extract text, correct OCR errors, and collect ground truth for training.
            **Arabic text is now correctly displayed (RTL) using arabic-reshaper + python-bidi.**
            """
        )

        with gr.Tabs():
            # ── Tab 1: OCR Correction (MAIN) ─────────────────────────
            with gr.Tab("🔍 OCR Correction"):
                gr.Markdown(
                    "**How to use:**\n"
                    "1. Upload a handwritten prescription or medical note\n"
                    "2. Click **Run OCR** to detect text regions\n"
                    "3. Review the annotated image and word crops below\n"
                    "4. Edit predicted texts if needed (one per line)\n"
                    "5. Provide ground truth for training data (one per line)\n"
                    "6. Click **Save** to export corrections + ground truth as JSON"
                )

                with gr.Row():
                    with gr.Column(scale=1):
                        ocr_image_input = gr.Image(
                            type="filepath",
                            label="Upload Image (Prescription / Handwritten Note)",
                        )
                        ocr_btn = gr.Button(
                            "🔍 Run OCR",
                            variant="primary",
                            size="lg",
                        )

                    with gr.Column(scale=2):
                        annotated_image_output = gr.Image(
                            label="Annotated Image (numbered bounding boxes — 🟢 >90% 🟡 >70% 🔴 <70%)",
                        )
                        ocr_summary_md = gr.Markdown(
                            value="*Upload an image and click 'Run OCR' to begin.*",
                        )

                # Word gallery
                gr.Markdown("### Detected Word Crops")
                word_gallery = gr.Gallery(
                    label="Word Crops (click to enlarge)",
                    columns=4,
                    height=200,
                    object_fit="contain",
                )

                # Editable correction area
                gr.Markdown("### ✏️ Edit Predictions & Provide Ground Truth")
                with gr.Row():
                    with gr.Column(scale=1):
                        predicted_text_output = gr.Textbox(
                            label="Predicted Texts (editable — one per line)",
                            lines=8,
                            interactive=True,
                            placeholder="After OCR, predicted texts will appear here. Edit to correct.",
                        )
                    with gr.Column(scale=1):
                        ground_truth_input = gr.Textbox(
                            label="Ground Truth (one per line, matching order above)",
                            lines=8,
                            interactive=True,
                            placeholder="Type the correct text for each word, one per line.\n"
                                       "Only lines with text will be saved as training data.",
                        )

                with gr.Row():
                    save_btn = gr.Button(
                        "💾 Save Corrections & Ground Truth",
                        variant="secondary",
                        size="lg",
                    )
                    result_json_output = gr.JSON(label="Exported Training Data")

                # Suggestions
                gr.Markdown("---")
                gr.Markdown("### Get Correction Suggestions")
                with gr.Row():
                    suggestion_text_input = gr.Textbox(
                        label="OCR Text",
                        placeholder="Paste OCR output here for suggestions...",
                        lines=3,
                    )
                    suggest_btn = gr.Button("Get Suggestions")
                suggestion_output = gr.Markdown(label="Suggestions")

            # ── Tab 2: Document Parser ───────────────────────────────
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
                        doc_md_output = gr.Markdown(label="Parse Results")
                        doc_json_output = gr.JSON(label="Summary JSON")

            # ── Tab 3: Medical Analysis ─────────────────────────────
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
                        analysis_md_output = gr.Markdown(label="Analysis Results")
                        analysis_json_output = gr.JSON(label="Structured JSON")

            # ── Tab 4: Clinical QA ──────────────────────────────────
            with gr.Tab("🩺 Clinical QA"):
                with gr.Row():
                    with gr.Column(scale=1):
                        qa_question_input = gr.Textbox(
                            label="Clinical Question",
                            placeholder=(
                                "Ask a clinical question...\n\n"
                                'Examples:\n'
                                '- "What is the interaction between warfarin and aspirin?"\n'
                                '- "What is the first-line treatment for type 2 diabetes?"\n'
                                '- "ما هي الأعراض الجانبية لميتفورمين؟"'
                            ),
                            lines=5,
                        )
                        qa_context_input = gr.Textbox(
                            label="Patient Context (optional JSON)",
                            placeholder='{"age": 65, "weight": 80, "conditions": ["hypertension"]}',
                            lines=3,
                        )
                        qa_btn = gr.Button(
                            "Ask Question",
                            variant="primary",
                            size="lg",
                        )

                    with gr.Column(scale=2):
                        qa_output = gr.Markdown(label="Answer")

        gr.Markdown(
            """
            ---
            **Disclaimer:** This tool is for clinical decision support only and does not
            replace professional medical judgment. Always verify results with qualified
            healthcare professionals.
            """
        )

        # ── Wire up events ─────────────────────────────────────────────

        # Tab 1: OCR
        ocr_btn.click(
            fn=perform_ocr,
            inputs=[ocr_image_input],
            outputs=[
                annotated_image_output,
                word_gallery,
                predicted_text_output,
                ground_truth_input,
                ocr_summary_md,
            ],
        )

        save_btn.click(
            fn=save_corrections,
            inputs=[predicted_text_output, ground_truth_input],
            outputs=[result_json_output],
        )

        suggest_btn.click(
            fn=get_correction_suggestions,
            inputs=[suggestion_text_input],
            outputs=[suggestion_output],
        )

        # Tab 2: Document Parser
        doc_btn.click(
            fn=parse_document,
            inputs=[doc_file_input],
            outputs=[doc_md_output, doc_json_output],
        )

        # Tab 3: Medical Analysis
        analysis_btn.click(
            fn=analyze_medical_text,
            inputs=[analysis_text_input],
            outputs=[analysis_md_output, analysis_json_output],
        )

        # Tab 4: Clinical QA
        qa_btn.click(
            fn=ask_clinical_question,
            inputs=[qa_question_input, qa_context_input],
            outputs=[qa_output],
        )

    return demo
