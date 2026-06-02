

---

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
