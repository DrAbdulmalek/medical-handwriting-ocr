> ⚠️ **مستودع مُوَحَّد**: التطوير النشط انتقل إلى [omni-medical-suite](https://github.com/DrAbdulmalek/omni-medical-suite).
> البنية التحتية المحذوفة محفوظة في [future-dev-ideas](https://github.com/DrAbdulmalek/future-dev-ideas).

---
title: Medical Handwriting OCR
emoji: 🏥
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

> **Role**: Production Deployment & Live Demo — This HF Space is the public-facing deployment of the [OmniMedical OCR Engine](https://github.com/DrAbdulmalek/omni-medical-suite). It provides a ready-to-use web interface for medical document OCR with multi-engine support and real-time correction.

# 🏥 Medical Handwriting OCR — Production Deployment

### التصحيح الطبي — PaddleOCR + Tesseract + EasyOCR + TrOCR

Upload medical documents → multi-engine OCR ensemble → edit corrections → save for future improvements.

---

## What This Is

This HF Space is the **production deployment** of the OmniMedical OCR system. It runs the full OCR pipeline as a live, publicly accessible web application — not a prototype, not a research notebook, and not a limited demo. It is the place where end users interact with the system, where corrections are collected for model improvement, and where the multi-engine ensemble is exercised in production.

**Ecosystem links**: [Platform (GitHub)](https://github.com/DrAbdulmalek/omni-medical-suite) · [Trainer](https://huggingface.co/spaces/DrAbdulmalek/medical-ocr-trainer) · [Dashboard](https://huggingface.co/spaces/DrAbdulmalek/mission-control) · [Preprocessing](https://github.com/DrAbdulmalek/scanner-fixer)

## What This Is NOT

| This is NOT… | It lives in… |
|---|---|
| The backend source code | [omni-medical-suite](https://github.com/DrAbdulmalek/omni-medical-suite) (GitHub) |
| The training/ingestion pipeline | [medical-ocr-training-hub](https://huggingface.co/spaces/DrAbdulmalek/medical-ocr-training-hub) (HF) |
| A secondary or legacy demo | [handwriting-ocr](https://huggingface.co/spaces/DrAbdulmalek/handwriting-ocr) (HF, legacy — has redirect banner) |
| A specialized training tool | [medical-ocr-trainer](https://huggingface.co/spaces/DrAbdulmalek/medical-ocr-trainer) (HF) |
| An operational dashboard | [mission-control](https://huggingface.co/spaces/DrAbdulmalek/mission-control) (HF) |

---

## Architecture

### Where This Space Fits

```
┌─────────────────────────────────────────────────────┐
│              OmniMedical Ecosystem                 │
├─────────────────────────────────────────────────────┤
│  [GitHub]            [HF Spaces]                   │
│  omni-medical-suite ──► medical-handwriting-ocr ◄── YOU ARE HERE
│  (backend code)       (THIS — Live Deployment)      │
│                       medical-ocr-trainer (training)│
│  scanner-fixer        mission-control (dashboard)   │
│  (preprocessing)      handwriting-ocr (legacy)      │
└─────────────────────────────────────────────────────┘
```

### Internal Dependencies

| Component | Repo | Access |
|---|---|---|
| 🔒 **Medical Dictionaries** | [DrAbdulmalek/arabic-dictionaries-collection](https://github.com/DrAbdulmalek/arabic-dictionaries-collection) | Private — requires `GITHUB_TOKEN` |
| 🔒 **Work Data & Training** | [DrAbdulmalek/medical-ocr-work-data](https://github.com/DrAbdulmalek/medical-ocr-work-data) | Private — requires `GITHUB_TOKEN` |
| 🌐 **This Project (HF Space)** | [DrAbdulmalek/medical-handwriting-ocr](https://github.com/DrAbdulmalek/medical-handwriting-ocr) | Public |

### Data Flow

```
┌─────────────────────┐
│   HF Space (Public) │
│  medical-handwriting │
│       -ocr           │
└─────┬───────┬───────┘
      │       │
      ▼       ▼
┌──────────┐  ┌──────────────┐
│ 🔒 Dict  │  │ 🔒 Work Data │
│  Repo    │  │    Repo      │
│(reads)   │  │ (reads+writes)│
└──────────┘  └──────────────┘
```

- **Dictionary Repo**: Medical terms loaded at startup for auto-correction
- **Work Data Repo**: Corrections, training exports, and work logs saved after each session

### Accessing Private Repos

The private repositories contain sensitive training data and medical dictionaries. To access them:

1. Request access from the repository owner
2. Set `GITHUB_TOKEN` in HF Space secrets (Settings → Repository secrets)
3. The application will automatically sync corrections and training data

---

## Features

- **Multi-Engine OCR**: PaddleOCR, Tesseract, EasyOCR, TrOCR running as an ensemble
- **Batch Processing**: Upload and process multiple medical documents in one session
- **Confidence Thresholds**: Adjustable per-engine confidence filtering
- **Real-Time Correction Editing**: Edit OCR output directly in the interface
- **Dictionary Auto-Correction**: Fuzzy matching against 900K+ medical terms
- **Noise Filtering**: Automatically removes garbage results (dots, symbols)
- **Training Export**: JSONL export ready for model fine-tuning
- **GitHub Sync**: Corrections auto-sync to private work-data repo
- **Arabic RTL**: Full Arabic text support with reshaping