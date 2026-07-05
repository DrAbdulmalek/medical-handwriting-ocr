#!/usr/bin/env python3
"""
Download sample medical document images for testing.
Generates synthetic Arabic medical text samples if no download URL is configured.

Usage:
    python scripts/download_samples.py
    python scripts/download_samples.py --count 5
"""

import os
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


SAMPLES_DIR = Path(__file__).parent.parent / "data" / "sample"


def generate_synthetic_sample(output_path: Path, text_lines: list, title: str = ""):
    """Generate a synthetic medical document image with Arabic text."""
    width, height = 800, 600
    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)
    
    y = 20
    if title:
        draw.text((20, y), title, fill="black")
        y += 40
    
    for line in text_lines:
        # Draw RTL-compatible text (left-aligned for Arabic)
        draw.text((20, y), line, fill="black")
        y += 30
    
    img.save(str(output_path), quality=95)
    print(f"  Generated: {output_path.name}")


def main():
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    
    samples = [
        {
            "filename": "prescription_ar_001.png",
            "title": "Medical Prescription / وصفة طبية",
            "lines": [
                "Patient: --- (anonymized)",
                "Date: 2026-01-15",
                "",
                "Rx:",
                "1. Metformin 500mg - Twice daily after meals",
                "2. Amlodipine 5mg - Once daily",
                "3. Omeprazole 20mg - Before breakfast",
                "",
                "Dr. ---",
            ]
        },
        {
            "filename": "lab_report_ar_002.png",
            "title": "Lab Report / تقرير مختبر",
            "lines": [
                "Complete Blood Count",
                "",
                "Hemoglobin: 12.5 g/dL    (Ref: 13.5-17.5)",
                "WBC: 7500 cells/uL        (Ref: 4500-11000)",
                "Platelets: 250000 cells/uL (Ref: 150000-400000)",
                "Glucose (Fasting): 110 mg/dL (Ref: 70-100) *",
                "Creatinine: 0.9 mg/dL    (Ref: 0.7-1.3)",
                "",
                "* Above reference range",
            ]
        },
        {
            "filename": "discharge_ar_003.png",
            "title": "Discharge Summary / ملخص خروج",
            "lines": [
                "Admission Date: 2026-01-10",
                "Discharge Date: 2026-01-15",
                "",
                "Diagnosis:",
                "  - Type 2 Diabetes Mellitus",
                "  - Hypertension (controlled)",
                "",
                "Treatment Given:",
                "  - IV Antibiotics (Ceftriaxone 1g x 5 days)",
                "  - Blood pressure management",
                "",
                "Follow-up: Cardiology clinic in 2 weeks",
            ]
        },
    ]
    
    print(f"Generating {len(samples)} synthetic samples in {SAMPLES_DIR}/")
    for sample in samples:
        output_path = SAMPLES_DIR / sample["filename"]
        if not output_path.exists():
            generate_synthetic_sample(output_path, sample["lines"], sample["title"])
        else:
            print(f"  Skipped (exists): {output_path.name}")
    
    print(f"\nDone. {len(list(SAMPLES_DIR.glob('*.png')))} samples ready.")
    print(f"Open the app and upload from: {SAMPLES_DIR}")


if __name__ == "__main__":
    main()