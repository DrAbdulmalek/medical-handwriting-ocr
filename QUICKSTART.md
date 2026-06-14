# 🚀 Quick Start — Medical Handwriting OCR

## Option A: Lite Mode (2 minutes, no Docker)

### Step 1: Install
```bash
pip install streamlit paddleocr paddlepaddle pillow
```

### Step 2: Run
```bash
git clone https://github.com/DrAbdulmalek/medical-handwriting-ocr.git
cd medical-handwriting-ocr
streamlit run app.py
```

### Step 3: Test
- Click **"Try Demo"** in the sidebar to see built-in samples
- Or upload any medical document image

**That's it!** You're running with PaddleOCR + Arabic support.

---

## Option B: Full Mode (Docker, all engines)

Requires: Docker, 8GB RAM, 10GB disk

```bash
git clone https://github.com/DrAbdulmalek/medical-handwriting-ocr.git
cd medical-handwriting-ocr
./setup.sh
```

See [README.md](README.md) for full details.

---

## Option C: HuggingFace Spaces

Visit the [HF Space](https://huggingface.co/spaces/DrAbdulmalek/medical-handwriting-ocr) for a zero-install demo.

---

## What Works in Lite Mode?
| Feature | Lite | Full (Docker) |
|---------|------|---------------|
| Single image OCR | ✅ | ✅ |
| Arabic + English | ✅ | ✅ |
| Batch processing | ❌ | ✅ |
| 5 OCR Engines | ❌ (PaddleOCR only) | ✅ |
| AI Corrections | ❌ | ✅ |
| Dictionary Lookup | ❌ | ✅ |
| Training Pipeline | ❌ | ✅ |
| Clinical QA | ❌ | ✅ |