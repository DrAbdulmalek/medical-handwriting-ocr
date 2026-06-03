# 🚀 Quick Start Guide

## Option A: CPU-Only (Recommended for First Time)

### Prerequisites
- Python 3.10+
- pip (Python package manager)
- ~2GB free RAM
- ~3GB disk space

### Steps

```bash
# 1. Clone
git clone https://github.com/DrAbdulmalek/medical-handwriting-ocr.git
cd medical-handwriting-ocr

# 2. Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 3. Install minimal dependencies
pip install --upgrade pip
pip install streamlit paddleocr paddlepaddle pillow pandas numpy medical-ocr-postprocessor

# 4. Run
streamlit run app.py
```

Open **http://localhost:8501** in your browser. Upload a handwritten medical document image and see the OCR results.

### What You Get with CPU-Only
- ✅ PaddleOCR engine (best for Arabic + English handwriting)
- ✅ Medical text correction via postprocessor
- ✅ PHI (Protected Health Information) masking
- ✅ Confidence scores per word
- ✅ JSON export of results

### Limitations
- No EasyOCR, TrOCR, or Surya (they need more RAM/GPU)
- Slower processing on complex images
- No ensemble voting (single engine only)

---

## Option B: Full Installation (GPU)

### Prerequisites
- NVIDIA GPU with CUDA support (8GB+ VRAM recommended)
- NVIDIA driver + CUDA 11.8+
- Python 3.10+
- 16GB+ system RAM

### Steps

```bash
# 1. Clone
git clone https://github.com/DrAbdulmalek/medical-handwriting-ocr.git
cd medical-handwriting-ocr

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate

# 3. Install PyTorch (GPU)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118

# 4. Install all dependencies
pip install -r requirements.txt

# 5. Run
streamlit run app.py
```

### What You Get with Full Installation
- All 4+ OCR engines with ensemble voting
- GPU-accelerated processing
- Multi-engine confidence comparison
- Full benchmarking support
- Production-ready API

---

## Troubleshooting

### "ModuleNotFoundError: paddleocr"
```bash
pip install paddleocr paddlepaddle
```

### "CUDA out of memory"
Reduce batch size or switch to CPU mode:
```bash
export OCR_DEVICE=cpu
export OCR_BATCH_SIZE=1
```

### "Tesseract not found"
```bash
# Ubuntu/Debian
sudo apt install tesseract-ocr

# macOS
brew install tesseract
```

### Slow processing on CPU
- Use smaller images (resize to 2048px max)
- Reduce batch size to 1
- Use PaddleOCR only (disable other engines)
- Close other applications to free RAM

---

## Next Steps

After the quick start:
1. 📖 Read the full [README.md](README.md) for all features
2. 🧪 Run evaluation on your own dataset
3. 🏥 Configure medical postprocessor for your use case
4. 📊 Compare engine accuracy with benchmarks
5. 🚀 Deploy as API with FastAPI/Docker
