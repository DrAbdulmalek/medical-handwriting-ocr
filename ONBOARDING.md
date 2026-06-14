# Onboarding Guide — دليل البدء السريع

> This guide gets you from zero to running in under 5 minutes.

## I'm new here. What do I do?

### Step 1: Install (30 seconds)

```bash
pip install streamlit paddleocr paddlepaddle pillow pandas numpy medical-ocr-postprocessor
```

### Step 2: Run (10 seconds)

```bash
git clone https://github.com/DrAbdulmalek/medical-handwriting-ocr.git
cd medical-handwriting-ocr
streamlit run app.py
```

### Step 3: Test (1 minute)

1. Open http://localhost:8501
2. Upload any medical document image (prescription, lab report, etc.)
3. Click "Run OCR"
4. Review results and correct any errors
5. Export corrected data

**That's it.** You're running medical OCR.

---

## I want more accuracy. What next?

1. **Add more OCR engines** (optional):
   ```bash
   pip install easyocr          # +500MB, better for printed text
   pip install surya-ocr        # +1.6GB, better layout analysis
   ```

2. **Use the training tool** to improve accuracy:
   - [medical-ocr-trainer](https://github.com/DrAbdulmalek/medical-ocr-trainer) — collect corrections and train

3. **Measure improvement**:
   - [medical-ocr-benchmarks](https://github.com/DrAbdulmalek/medical-ocr-benchmarks) — track CER/WER

---

## I want to deploy this.

See [README.md](README.md) → Deployment section for Docker, Kubernetes, and cloud options.

---

## I'm confused by all the repos.

See the [Ecosystem Map](https://github.com/DrAbdulmalek/omni-medical-suite/blob/main/PORTFOLIO.md) — 
it explains which repo to use and when.

---

## Environment Variables

Minimum required for basic usage (CPU mode):

```bash
# Copy the minimal config
cp .env.cpu-example .env

# That's it — defaults work out of the box
```

For full configuration, see `.env.example` (100+ options for production).