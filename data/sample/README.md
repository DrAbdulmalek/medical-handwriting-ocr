# Sample Data / بيانات تجريبية

This directory contains sample medical document images for testing.

## Download Sample Images

Run the following to download sample Arabic medical documents:

```bash
python scripts/download_samples.py
```

Or manually place medical document images (PNG/JPG) here.

## Supported Formats

- PNG, JPG, JPEG, WEBP, BMP, TIFF
- Maximum size: 10MB per image
- Recommended: 300 DPI scans for best OCR accuracy

## Sample Categories

| Category | Description | Example |
|----------|-------------|---------|
| Prescriptions | وصفات طبية | Drug name, dosage, frequency |
| Lab Reports | تقارير مختبر | Test name, value, unit, range |
| Discharge Summary | ملخص خروج | Diagnosis, treatment, follow-up |
| Radiology | تقرير أشعة | Findings, impression, recommendation |

> **Note**: No real patient data is included. All samples are synthetic or properly anonymized.