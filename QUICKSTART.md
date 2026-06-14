# Quick Start — البدء السريع

> **3 خطوات فقط** لتشغيل Medical Handwriting OCR

## الطريقة السريعة (CPU فقط — 350MB)

```bash
# 1. استنساخ المستودع
git clone https://github.com/DrAbdulmalek/medical-handwriting-ocr.git
cd medical-handwriting-ocr

# 2. تثبيت المتطلبات
pip install streamlit paddleocr paddlepaddle pillow pandas numpy medical-ocr-postprocessor

# 3. التشغيل
streamlit run app.py
```
> يفتح تلقائياً على http://localhost:8501

---

## الطريقة الكاملة (GPU + كل المحركات — 3.1GB+)

```bash
git clone https://github.com/DrAbdulmalek/medical-handwriting-ocr.git
cd medical-handwriting-ocr

# تثبيت PyTorch (GPU)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118

# تثبيت كل المتطلبات
pip install -r requirements.txt

streamlit run app.py
```

---

## أوضاع التشغيل / Running Modes

| الوضع | الحجم | المحركات | الأمر |
|-------|-------|----------|-------|
| **Lite** | 350MB | PaddleOCR فقط | `pip install streamlit paddleocr paddlepaddle` |
| **Medium** | 850MB | + EasyOCR | `pip install -e ".[medium]"` |
| **Full** | 3.1GB+ | + TrOCR + Surya | `pip install -e ".[full]"` |

---

## ملف التهيئة / Configuration

```bash
# انسخ ملف التهيئة وعدّله حسب حاجتك
cp .env.cpu-example .env
```

---

## مشاكل شائعة / Troubleshooting

| المشكلة | الحل |
|---------|------|
| `ModuleNotFoundError: paddleocr` | `pip install paddleocr paddlepaddle` |
| CUDA Out of Memory | استخدم الوضع Lite أو قلّل `BATCH_SIZE` في `.env` |
| `tesseract not found` | `sudo apt install tesseract-ocr` (اختياري) |
| البطء على CPU | فعّل `ENABLE_EASYOCR=false` في `.env` |

---

## الخطوة التالية / Next Steps

- 📖 [README.md](README.md) — التوثيق الكامل
- 🔧 [المنظومة الكاملة](https://github.com/DrAbdulmalek/omni-medical-suite) — المنصة المتكاملة
- 📊 [المعايير](https://github.com/DrAbdulmalek/medical-ocr-benchmarks) — قياس الأداء
- 📦 [المكتبة](https://github.com/DrAbdulmalek/medical-ocr-postprocessor) — مكتبة التصحيح

---

> جزء من [منظومة OCR الطبية](https://github.com/DrAbdulmalek/omni-medical-suite/blob/main/PORTFOLIO.md)