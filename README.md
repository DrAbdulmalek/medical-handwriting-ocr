<!-- ARCHIVE BANNER - AUTO-GENERATED -->
<div align="center">

# ⚠️ This repository has been archived

**Medical OCR engine merged into omni-medical-suite/backend/ocr/**

This project has been consolidated into the unified **[omni-medical-suite](https://github.com/DrAbdulmalek/omni-medical-suite)** monorepo.

All active development, bug fixes, and new features continue there.

</div>

---

> **Archived on: 2026-06-28** | **Active project:** [omni-medical-suite](https://github.com/DrAbdulmalek/omni-medical-suite)

---

> **⚠️ هذا المستودع مؤرشف. استخدم [omni-medical-suite](https://github.com/DrAbdulmalek/omni-medical-suite) بدلاً منه.**
> **⚠️ ARCHIVED: This repository is archived. Use [omni-medical-suite](https://github.com/DrAbdulmalek/omni-medical-suite) instead.**

---

# 🩺 Medical Handwriting OCR

[![CI/CD](https://github.com/DrAbdulmalek/medical-handwriting-ocr/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/DrAbdulmalek/medical-handwriting-ocr/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](https://www.docker.com/)
[![Kubernetes](https://img.shields.io/badge/kubernetes-ready-blue.svg)](https://kubernetes.io/)

> **نظام التعرف الضوئي على الخط اليدوي الطبي مع التعلم المستمر**
> 
> Adaptive OCR system for medical handwritten notes with continuous learning, Arabic dictionary integration, and UMLS/SNOMED validation.

---

> **New here?** Start with the [⚡ Quick Start Guide (CPU-Only)](#-quick-start--cpu-only-lightweight) — get running in 5 minutes with no GPU required.

## 📋 Table of Contents

- [⚡ CPU-Only Quick Start](#-quick-start--cpu-only-lightweight)
- [Quick Start Guide](QUICKSTART.md)
- [Features](#-features)
- [Architecture](#-architecture)
- [Quick Start (Docker/Full)](#-quick-start-1)
- [Installation](#-installation)
- [Usage](#-usage)
- [Dictionary Integration](#-dictionary-integration)
- [API Documentation](#-api-documentation)
- [Development](#-development)
- [Deployment](#-deployment)
- [Contributing](#-contributing)
- [License](#-license)

---

## ⚡ Quick Start — CPU-Only (Lightweight)

Get running in under 5 minutes with minimal dependencies. No GPU required.

```bash
# 1. Clone the repository
git clone https://github.com/DrAbdulmalek/medical-handwriting-ocr.git
cd medical-handwriting-ocr

# 2. Install minimal dependencies (CPU only, ~2GB)
pip install streamlit paddleocr paddlepaddle pillow pandas numpy medical-ocr-postprocessor

# 3. Run the demo
streamlit run app.py

# 4. Open in browser
# http://localhost:8501
```

### Lightweight vs Full Installation

| Feature | Lightweight (CPU) | Full (GPU) |
|---------|:---:|:---:|
| **PaddleOCR** | ✅ | ✅ |
| **EasyOCR** | ❌ (add: `pip install easyocr`) | ✅ |
| **TrOCR** | ❌ (add: `pip install transformers torch`) | ✅ |
| **Surya OCR** | ❌ (GPU only) | ✅ |
| **Medical Postprocessor** | ✅ | ✅ |
| **PHI Masking** | ✅ | ✅ |
| **RAM Required** | 2–4 GB | 16 GB+ |
| **Setup Time** | < 5 min | ~15 min |
| **Best For** | Quick testing, CPU servers, CI/CD | Full benchmarking, production |

### CPU-Only Configuration

For CPU-only environments, create a `.env` file (see [`.env.cpu-example`](.env.cpu-example)):

```env
# CPU-only lightweight config
OCR_ENGINE=paddleocr
OCR_DEVICE=cpu
ENABLE_EASYOCR=false
ENABLE_TROCR=false
ENABLE_SURYA=false
BATCH_SIZE=1
MAX_IMAGE_SIZE=2048
LOG_LEVEL=INFO
```

### Sample Dataset

A small sample dataset for testing is available in the `data/samples/` directory:

```bash
# Run evaluation on sample data
python evaluate.py --data data/samples/ --engine paddleocr --device cpu

# Or use the Streamlit UI to upload your own images
streamlit run app.py
```

> **Note:** For full production deployment with all engines, GPU acceleration, and medical postprocessing, see the [Full Installation Guide](#-quick-start-dockerfull) below.

---

## ✨ Features

### Core OCR
- 🔤 **Multilingual Support** — Arabic, English, and mixed-script recognition
- 🧠 **Specialized Models** — PaddleOCR for detection, fine-tuned TrOCR for recognition
- 📸 **Document Processing** — PDF, PNG, JPG, DICOM support
- 🎯 **High Accuracy** — CER < 5% with continuous improvement

### Smart Corrections
- 💡 **7 Suggestion Strategies** — Dictionary, edit distance, phonetic, historical, context, abbreviation, neural
- 📚 **Arabic Dictionaries** — Optional integration with [arabic-dictionaries-collection](https://github.com/DrAbdulmalek/arabic-dictionaries-collection)
- 🏥 **UMLS/SNOMED** — Medical terminology validation
- ⚡ **Real-time Suggestions** — Keyboard-navigable suggestion panel

### Continuous Learning
- 🔄 **Weekly Retraining** — Automated pipeline with EWC + Replay Buffer
- 🛡️ **Catastrophic Forgetting Prevention** — Elastic Weight Consolidation
- 📊 **4 Sampling Strategies** — Reservoir, hard examples, diversity, stratified
- 🚀 **Auto-Deployment** — Canary deployment with automatic rollback

### Production Ready
- 🔐 **RBAC Authentication** — 5 roles, 10 granular permissions
- 📈 **Monitoring** — Prometheus + Grafana dashboards
- 🐳 **Docker & Kubernetes** — Full containerization
- ☁️ **Terraform** — AWS EKS infrastructure as code
- 🧪 **Comprehensive Tests** — 9 test suites, 70% coverage

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │  Web App    │  │  PWA Mobile │  │  Gradio UI              │ │
│  │  (React)    │  │  (Offline)  │  │  (4 Tabs)               │ │
│  └──────┬──────┘  └──────┬──────┘  └───────────┬─────────────┘ │
└─────────┼────────────────┼─────────────────────┼───────────────┘
          │                │                     │
          └────────────────┴─────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                        API GATEWAY                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │  Nginx      │  │  Rate       │  │  CORS + Security        │ │
│  │  Reverse    │  │  Limiter    │  │  Headers                │ │
│  │  Proxy      │  │  (Redis)    │  │                         │ │
│  └──────┬──────┘  └──────┬──────┘  └───────────┬─────────────┘ │
└─────────┼────────────────┼─────────────────────┼───────────────┘
          │                │                     │
┌─────────▼────────────────▼─────────────────────▼───────────────┐
│                      FASTAPI BACKEND                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │  Upload     │  │  OCR        │  │  Corrections            │ │
│  │  Router     │  │  Engine     │  │  Router                 │ │
│  └──────┬──────┘  └──────┬──────┘  └───────────┬─────────────┘ │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │  Dictionary │  │  UMLS/      │  │  Suggestions            │ │
│  │  Client     │  │  SNOMED     │  │  Engine                 │ │
│  └──────┬──────┘  └──────┬──────┘  └───────────┬─────────────┘ │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │  Auth/RBAC  │  │  Deployment │  │  Metrics                │ │
│  │  (JWT)      │  │  Manager    │  │  (Prometheus)           │ │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘ │
└──────────────────────────┬─────────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────────┐
│                      DATA LAYER                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │  PostgreSQL │  │  MinIO/S3   │  │  Redis                  │ │
│  │  (Metadata) │  │  (Images)   │  │  (Cache + Queue)        │ │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────────┐
│                    TRAINING PIPELINE                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │  Export     │  │  TrOCR      │  │  Evaluation             │ │
│  │  Dataset    │  │  Fine-tune  │  │  (CER/WER)              │ │
│  └──────┬──────┘  └──────┬──────┘  └───────────┬─────────────┘ │
│         │                │                     │                │
│  ┌──────▼──────┐  ┌──────▼──────┐  ┌───────────▼─────────────┐ │
│  │  Replay     │  │  EWC        │  │  Auto-Deploy            │ │
│  │  Buffer     │  │  Regularize │  │  (Canary + Rollback)    │ │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### One-Command Setup

```bash
# Clone repository
git clone https://github.com/DrAbdulmalek/medical-handwriting-ocr.git
cd medical-handwriting-ocr

# Run setup script (installs Docker, builds images, starts services)
chmod +x setup.sh
./setup.sh

# Access the application
open http://localhost:8000        # Web UI
open http://localhost:8000/docs   # API Documentation
open http://localhost:9001        # MinIO Console
open http://localhost:3000        # Grafana Dashboard
```

### Manual Setup

```bash
# 1. Start infrastructure
docker-compose -f docker/docker-compose.full.yml up -d

# 2. Install backend dependencies
cd backend
pip install -r requirements.txt

# 3. Run migrations
alembic upgrade head

# 4. Start backend
uvicorn app.main:app --reload

# 5. Open frontend
# Serve frontend/ directory with any static server
```

---

## 📦 Installation

### Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | 4 cores | 8+ cores |
| RAM | 8 GB | 16+ GB |
| GPU | Optional | NVIDIA with 6GB+ VRAM |
| Disk | 20 GB | 100+ GB |
| Docker | 20.10+ | Latest |

### Environment Variables

Create `.env` file:

```bash
# Database
DATABASE_URL=postgresql://ocr_user:ocr_password_123@localhost:5432/medical_ocr

# MinIO
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=<YOUR_SECRET_KEY>

# Optional: Arabic Dictionaries (GitHub Token)
DICTIONARY_REPO_TOKEN=ghp_your_token_here

# Optional: UMLS (Medical Terminology)
UMLS_API_KEY=your_umls_api_key

# Security
JWT_SECRET=your-jwt-secret-min-32-chars
```

---

## 🎯 Usage

### Upload & Correct Document

```bash
# Upload image
curl -X POST http://localhost:8000/api/upload   -F "file=@medical_note.jpg"   -F "user_id=doctor_123"

# Get suggestions for a word
curl "http://localhost:8000/api/suggestions?text=Ostecb(astoma&is_medical=true"

# Submit correction
curl -X POST http://localhost:8000/api/correct   -H "Content-Type: application/json"   -d '{
    "region_id": "uuid-here",
    "corrected_text": "Osteoblastoma",
    "user_id": "doctor_123"
  }'
```

### Python SDK

```python
from medical_ocr import MedicalOCRClient

client = MedicalOCRClient("http://localhost:8000")

# Upload and process
result = client.upload_document("medical_note.jpg")

# Get smart suggestions
suggestions = client.get_suggestions(
    text="Ostecb(astoma",
    context_before="Bone tumor:",
    is_medical=True
)

# Apply correction
client.submit_correction(
    region_id=result.regions[0].id,
    corrected_text="Osteoblastoma"
)
```

---

## 📚 Dictionary Integration

### Arabic Dictionaries

Enable by setting `DICTIONARY_REPO_TOKEN`:

```bash
# Get token from GitHub Settings → Developer settings → Personal access tokens
# Required scopes: repo

export DICTIONARY_REPO_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
```

Features when enabled:
- ✅ Term validation against classical Arabic dictionaries
- ✅ Cross-reference with medical terminology
- ✅ Automatic spelling correction suggestions
- ✅ Historical form recognition

### UMLS/SNOMED-CT

Enable by setting `UMLS_API_KEY`:

```bash
# Get key from https://uts.nlm.nih.gov/uts/signup-login
export UMLS_API_KEY=your_umls_key
```

Features when enabled:
- ✅ Medical term validation
- ✅ Semantic type classification
- ✅ Cross-language mapping (English ↔ Arabic)
- ✅ Concept relationships

---

## 📖 API Documentation

Interactive API documentation available at:

- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **OpenAPI JSON**: `http://localhost:8000/openapi.json`

### Key Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/upload` | POST | Upload document for OCR |
| `/api/correct` | POST | Submit correction |
| `/api/suggestions` | GET | Get smart suggestions |
| `/api/dictionaries/search` | GET | Search Arabic dictionaries |
| `/api/umls/validate` | GET | Validate medical term |
| `/api/dictionaries/status` | GET | Dictionary integration status |
| `/health` | GET | Health check |
| `/metrics` | GET | Prometheus metrics |

---

## 🛠️ Development

### Running Tests

```bash
# Unit tests only
make test

# All tests including integration
make test-all

# With coverage report
make test-coverage

# Specific test file
pytest tests/test_suggestion_engine.py -v
```

### Code Quality

```bash
# Format code
black backend/ training/ tests/

# Lint
flake8 backend/ training/ tests/

# Type check
mypy backend/app/ training/
```

### Training Pipeline

```bash
# Export weekly corrections to dataset
python training/export_dataset.py

# Run training (locally with GPU)
python training/continual_trainer.py

# Or upload to Google Colab
# See training/colab_notebook.ipynb
```

---

## 🚀 Deployment

### Docker Compose (Single Node)

```bash
docker-compose -f docker/docker-compose.full.yml up -d
```

### Kubernetes

```bash
# Apply base configuration
kubectl apply -k k8s/base/

# Apply canary deployment
kubectl apply -k k8s/canary/

# Monitor rollout
kubectl rollout status deployment/backend
```

### Terraform (AWS)

```bash
cd terraform

# Initialize
terraform init

# Plan
terraform plan -var-file="production.tfvars"

# Apply
terraform apply -var-file="production.tfvars"
```

---

## 📊 Monitoring

### Grafana Dashboards

Access at `http://localhost:3000` (admin/<YOUR_SECURE_PASSWORD>)

Pre-configured dashboards:
- **OCR Performance** — CER/WER trends, processing time
- **System Health** — CPU, memory, GPU utilization
- **Business Metrics** — Documents processed, corrections made
- **Model Quality** — Accuracy per model version

### Alerts

Configured alerts for:
- 🚨 Error rate > 5%
- 🚨 GPU memory > 90%
- 🚨 Database connections > 80%
- ⚠️ Model accuracy degradation
- ⚠️ Disk space < 20%

---

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

### Commit Convention

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation
- `style:` Formatting
- `refactor:` Code restructuring
- `test:` Tests
- `chore:` Maintenance

---

## 📄 License

This project is licensed under the MIT License - see [LICENSE](LICENSE) file.

```
MIT License

Copyright (c) 2024 Medical OCR Project

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
```

---

## 🙏 Acknowledgments

- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) for detection engine
- [Microsoft TrOCR](https://huggingface.co/microsoft/trocr-base-handwritten) for recognition
- [Arabic Dictionaries Collection](https://github.com/DrAbdulmalek/arabic-dictionaries-collection) for terminology
- [UMLS](https://www.nlm.nih.gov/research/umls/) for medical terminology standards
- [SNOMED CT](https://www.snomed.org/) for clinical terminology

---

## 📞 Support

- 📧 Email: support@medical-ocr.dev
- 💬 Discord: [Join our server](https://discord.gg/medical-ocr)
- 🐛 Issues: [GitHub Issues](https://github.com/DrAbdulmalek/medical-handwriting-ocr/issues)
- 📖 Docs: [Full Documentation](https://medical-ocr.readthedocs.io)

---

<p align="center">
  Made with ❤️ for the medical community
</p>


---

## Ecosystem Integration

This project integrates with [medical-ocr-postprocessor](https://github.com/DrAbdulmalek/medical-ocr-postprocessor) for post-OCR text correction and PHI masking.

```python
# The postprocessor runs after OCR recognition
from app.postprocessor_integration import get_postprocessor_bridge

bridge = get_postprocessor_bridge()
corrected_text, corrections = bridge.correct(raw_ocr_text, phi_mask=True)
```

| Feature | Source | Description |
|---------|--------|-------------|
| Dictionary correction | medical-ocr-postprocessor | Exact + fuzzy + phrase matching |
| PHI masking | medical-ocr-postprocessor | 7 PHI types, 3 masking modes |
| OCR engines | This project | PaddleOCR + TrOCR |
| Training pipeline | This project | Continuous learning with EWC |

## Repository Status

| Field | Value |
|-------|-------|
| **Role** | Production OCR Platform |
| **Status** | Active Development |
| **Layer** | Applications (Product) |
| **Priority** | Highest |
| **Relation** | Production deployment of medical handwriting OCR with continuous learning |

## Who Should Use This

- Healthcare facilities needing **production-grade handwriting OCR**
- Teams requiring **Arabic + English** bilingual medical recognition
- Projects needing **continuous learning** with automated retraining
- Organizations requiring **Kubernetes deployment** with auto-scaling

## Operating Modes

| Mode | Description | Resources | Use Case |
|------|-------------|-----------|----------|
| **Lite** | CPU-only, minimal engines | 2 CPU, 4GB RAM | Development & testing |
| **Standard** | Multi-engine with GPU | 4 CPU, 8GB RAM, GPU | Staging environments |
| **GPU-Production** | Full pipeline + continuous learning | 8 CPU, 16GB RAM, GPU | Production deployment |

## Performance Benchmarks

| Metric | Target | Notes |
|--------|--------|-------|
| CER (Character Error Rate) | < 5% | With post-OCR correction |
| Latency per page | < 3s | GPU mode |
| Throughput | 10+ pages/min | Standard mode |
| Medical term recall | > 95% | With dictionary integration |

## When to Use This vs Other Repos

| Need | Repository |
|------|-----------|
| Production handwriting OCR with learning | **This repo** (medical-handwriting-ocr) |
| OCR correction engine only | [medical-ocr-postprocessor](https://github.com/DrAbdulmalek/medical-ocr-postprocessor) |
| Complete unified platform | [omni-medical-suite](https://github.com/DrAbdulmalek/omni-medical-suite) |
| Collect & correct training data | [medical-ocr-trainer](https://github.com/DrAbdulmalek/medical-ocr-trainer) |

## Related Repositories

| Repo | Role | Status |
|------|------|--------|
| [omni-medical-suite](https://github.com/DrAbdulmalek/omni-medical-suite) | Main Platform | Active |
| [medical-ocr-postprocessor](https://github.com/DrAbdulmalek/medical-ocr-postprocessor) | Core Correction Engine | Active |
| [medical-ocr-trainer](https://github.com/DrAbdulmalek/medical-ocr-trainer) | Data Collection | Active |

**License: MIT** — Dr. Abdulmalek Tamer Al-husseini


