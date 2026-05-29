# Medical Handwriting OCR

Adaptive OCR system for medical handwritten notes with human-in-the-loop correction and continuous learning.

## Features

- Arabic and English support with mixed text handling
- Interactive correction interface
- Continuous learning from user corrections
- Word crop storage with metadata for training
- Quality monitoring dashboard

## Quick Start

### Requirements

- Docker & Docker Compose
- 8GB RAM (16GB recommended)
- 10GB storage

### Installation

```bash
# 1. Clone repository
git clone https://github.com/drabdulmalrk/medical-handwriting-ocr.git
cd medical-handwriting-ocr

# 2. Run setup
chmod +x setup.sh
./setup.sh

# 3. Start all services
cd docker
docker-compose up -d
```

### Usage

1. Open browser at `http://localhost:8000`
2. Upload a medical note image
3. Review extracted words and correct errors
4. Save corrections

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/upload` | POST | Upload image and extract text |
| `/api/correct` | POST | Save correction |
| `/api/pending` | GET | Get pending corrections |

### Training

```bash
# Export dataset
cd training
python export_dataset.py --output ./hf_dataset

# Fine-tune TrOCR (GPU required)
python finetune_trocr.py --dataset ./hf_dataset --output ./trained_model --epochs 5

# Evaluate
python evaluate.py --model ./trained_model/final
```

## Architecture

```
Frontend (HTML/JS)  →  FastAPI Backend  →  PostgreSQL (Metadata)
                                        →  MinIO (Images)
                                        →  PaddleOCR / TrOCR (OCR Engine)
```

## Future Development

- [ ] TrOCR fine-tuning on collected data
- [ ] Double-blind medical review system
- [ ] Automatic weekly training pipeline
- [ ] Additional format support (PDF, DICOM)

## License

MIT License - Open source for medical and research use.
