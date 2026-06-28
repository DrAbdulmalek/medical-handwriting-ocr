---
title: Medical Handwriting OCR
emoji: "🏥"
colorFrom: teal
colorTo: green
sdk: docker
app_port: 7860
pinned: true
license: mit
tags:
  - ocr
  - medical
  - handwriting
  - arabic
  - english
  - paddleocr
  - gradio
models:
  - DrAbdulmalek/medical-handwriting-ocr-v1
datasets:
  - DrAbdulmalek/medical-ocr-ground-truth
---

<div align="center">

# Medical Handwriting OCR

**Adaptive OCR system for medical handwritten notes with continuous learning, Arabic dictionary integration, and UMLS/SNOMED validation.**

| Feature | Status |
|---------|--------|
| Arabic handwritten prescriptions | Supported |
| English medical notes | Supported |
| Mixed Arabic-English documents | Supported |
| Medical term dictionary (270+ terms) | Integrated |
| UMLS/SNOMED validation | Available |
| Batch processing | Supported |
| API access | REST + Gradio |

[![Open in Spaces](https://huggingface.co/datasets/huggingface/badges/raw/main/open-in-hf-spaces-sm.svg)](https://huggingface.co/spaces/DrAbdulmalek/medical-handwriting-ocr)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](https://www.docker.com/)

</div>

---

## Model Description

Medical Handwriting OCR is an end-to-end system designed to recognize and digitize handwritten medical documents including prescriptions, clinical notes, lab results, and referral letters. The system supports **Arabic** and **English** languages with special handling for **mixed-language documents** that are common in medical practice across the Middle East and North Africa region.

The core engine is built on **PaddleOCR** with custom post-processing layers including:

- **Arabic Medical Dictionary**: 270+ medical term correction rules covering diagnoses, medications, procedures, and anatomical terms
- **FHIR R4 Mapping**: Automatic transformation of extracted data into FHIR (Fast Healthcare Interoperability Resources) standard format
- **Clinical Validation**: Cross-referencing extracted terms against UMLS and SNOMED CT medical ontologies
- **Adaptive Learning**: Continuous improvement through user feedback integration

## Intended Uses & Limitations

### Intended Use
- Digitizing handwritten medical prescriptions for electronic health records
- Processing historical clinical notes for research databases
- Assisting healthcare providers in converting handwritten notes to structured data
- Supporting medical billing and coding workflows

### Limitations
- Not a substitute for professional medical transcription review
- Accuracy may vary with handwriting style, document quality, and scan resolution
- Medical decisions should not be made solely based on OCR output without clinical verification
- Best performance on printed forms with handwritten fields; pure freehand notes may have lower accuracy

## How to Use

### Gradio Demo (This Space)
Upload a handwritten medical document image and select processing options. The system will extract text, apply medical dictionary corrections, and display structured results.

### API Usage

```python
import requests

# Upload and process
with open("prescription.jpg", "rb") as f:
    response = requests.post(
        "https://drabdulmalek-medical-handwriting-ocr.hf.space/api/predict",
        files={"file": f},
        data={"language": "arabic", "apply_corrections": "true"}
    )
    result = response.json()
    print(result["text"])
```

### Local Installation

```bash
git clone https://github.com/DrAbdulmalek/medical-handwriting-ocr.git
cd medical-handwriting-ocr
pip install -r requirements.txt
python app.py
```

## Training Data

The model was fine-tuned on a curated dataset of **medical handwritten documents** from multiple healthcare facilities. The dataset includes:

| Category | Count | Languages |
|----------|-------|-----------|
| Prescriptions | 500+ | Arabic, English |
| Clinical Notes | 300+ | Arabic, English, Mixed |
| Lab Results | 200+ | English, Arabic |
| Referral Letters | 150+ | Arabic, English |

**Dataset:** [medical-ocr-ground-truth](https://huggingface.co/datasets/DrAbdulmalek/medical-ocr-ground-truth)

## Evaluation Results

Benchmarks measured on the held-out test set (see [medical-ocr-benchmarks](https://github.com/DrAbdulmalek/medical-ocr-benchmarks)):

| Metric | Arabic | English | Mixed |
|--------|--------|---------|-------|
| **CER** (Character Error Rate) | 4.2% | 3.1% | 5.8% |
| **WER** (Word Error Rate) | 8.7% | 6.3% | 11.2% |
| **Medical Term Accuracy** | 92.1% | 95.4% | 88.6% |
| **Latency** (per page) | 1.2s | 0.9s | 1.5s |

## Technical Details

- **Base Engine**: PaddleOCR (PP-OCRv4)
- **Post-Processing**: Custom medical dictionary + regex patterns
- **Backend**: FastAPI + SQLAlchemy
- **Frontend**: Gradio + React
- **Deployment**: Docker + Kubernetes (HPA 2-10 pods)
- **Standards**: FHIR R4, HL7 v2.5, DICOM

## Ethical Considerations

This system processes medical documents which may contain protected health information (PHI). Users must ensure compliance with applicable data protection regulations (HIPAA, GDPR, or local equivalents) when using this system. Documents should be anonymized before processing when possible, and results should be stored and transmitted securely.

## Citing

```bibtex
@software{medical_handwriting_ocr,
  author = {DrAbdulmalek},
  title = {Medical Handwriting OCR: Adaptive OCR for Medical Documents},
  year = {2025},
  url = {https://huggingface.co/spaces/DrAbdulmalek/medical-handwriting-ocr},
  license = {MIT}
}
```

## Links

- **Source Code**: [github.com/DrAbdulmalek/medical-handwriting-ocr](https://github.com/DrAbdulmalek/medical-handwriting-ocr)
- **Unified Suite**: [github.com/DrAbdulmalek/omni-medical-suite](https://github.com/DrAbdulmalek/omni-medical-suite)
- **Benchmarks**: [github.com/DrAbdulmalek/medical-ocr-benchmarks](https://github.com/DrAbdulmalek/medical-ocr-benchmarks)
- **Ground Truth Data**: [huggingface.co/datasets/DrAbdulmalek/medical-ocr-ground-truth](https://huggingface.co/datasets/DrAbdulmalek/medical-ocr-ground-truth)
- **Training Hub**: [github.com/DrAbdulmalek/medical-ocr-training-hub](https://github.com/DrAbdulmalek/medical-ocr-training-hub)