# Medical Handwriting OCR - Architecture

## System Overview

This system implements an adaptive OCR pipeline for medical handwritten notes with Arabic-English mixed content. It features human-in-the-loop correction, continuous learning, and a robust data storage system.

## Architecture Components

### 1. OCR Pipeline
- **PaddleOCR**: Primary text detection and recognition engine supporting Arabic and English
- **TrOCR**: Specialized model for handwritten text refinement (fine-tuned on user corrections)
- **Script Router**: Classifies text regions as Arabic, Latin, Mixed, or Numeric

### 2. Backend (FastAPI)
- Image upload and processing
- OCR result storage
- Correction management API
- Dataset export for training

### 3. Frontend (HTML/CSS/JS)
- Side-by-side image and text view
- Interactive word correction
- Confidence indicators
- Statistics dashboard

### 4. Storage
- **PostgreSQL**: Metadata, corrections, model versions
- **MinIO/S3**: Image storage (scans, crops)

### 5. Training Pipeline
- Export dataset to HuggingFace format
- Fine-tune TrOCR with EWC (Elastic Weight Consolidation)
- Evaluate model performance (CER/WER)

## Data Flow

```
Upload → OCR Detection → Display → User Correction → Store → Export → Train → Deploy
```

## Continuous Learning Strategy

1. Collect corrections via UI
2. Validate corrections (confidence + dictionary check)
3. Promote to gold standard
4. Weekly fine-tuning with EWC + Replay Buffer
5. Evaluate on held-out test set
6. Deploy if metrics improve, rollback otherwise
