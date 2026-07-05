"""
Medical Handwriting OCR — Lite Streamlit App
Runs with: pip install streamlit paddleocr paddlepaddle pillow
"""

import json
import tempfile
from pathlib import Path

import streamlit as st
from PIL import Image

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Medical Handwriting OCR", layout="wide")
st.title("🩺 Medical Handwriting OCR")
st.caption("Arabic + English medical document recognition powered by PaddleOCR")

# ── Sidebar settings ─────────────────────────────────────────────────────────
st.sidebar.header("Settings")
lang = st.sidebar.selectbox("Language", ["ar", "en", "ar+en"],
                             format_func=lambda x: {"ar": "Arabic", "en": "English", "ar+en": "Mixed (AR+EN)"}[x])

# ── Lazy PaddleOCR init ──────────────────────────────────────────────────────
@st.cache_resource
def get_ocr(lang_code: str):
    try:
        from paddleocr import PaddleOCR
        return PaddleOCR(use_angle_cls=True, lang=lang_code, show_log=False)
    except Exception as e:
        st.error(f"PaddleOCR init failed: {e}\nInstall with: pip install paddleocr paddlepaddle")
        return None

# ── Run OCR ──────────────────────────────────────────────────────────────────
def run_ocr(ocr_engine, image: Image.Image):
    if ocr_engine is None:
        return None
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        image.save(f.name)
        try:
            result = ocr_engine.ocr(f.name, cls=True)
            lines = []
            if result and result[0]:
                for line in result[0]:
                    text, conf = line[1] if len(line) > 1 else ("", 0.0)
                    if text.strip():
                        lines.append({"text": text, "confidence": float(conf)})
            return lines
        except Exception as e:
            st.error(f"OCR failed: {e}")
            return None

# ── Generate synthetic demo image from text ──────────────────────────────────
def make_demo_image(text: str) -> Image.Image:
    from PIL import ImageDraw, ImageFont
    img = Image.new("RGB", (800, 200), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    except (OSError, IOError):
        font = ImageFont.load_default()
    draw.text((30, 80), text, fill="black", font=font)
    return img

# ── Sample data ──────────────────────────────────────────────────────────────
SAMPLE_PATH = Path(__file__).parent / "data" / "sample" / "sample_cases.json"

def load_samples():
    if SAMPLE_PATH.exists():
        return json.loads(SAMPLE_PATH.read_text())
    return [
        {"id": "demo_001", "text_en": "Amoxicillin 500mg twice daily for 7 days", "specialty": "prescription", "difficulty": "easy"},
        {"id": "demo_002", "text_en": "Systolic 120, Diastolic 80 mmHg", "specialty": "vitals", "difficulty": "easy"},
    ]

# ── UI: Demo button ─────────────────────────────────────────────────────────
st.sidebar.markdown("---")
if st.sidebar.button("🎯 Try Demo", use_container_width=True):
    samples = load_samples()
    chosen = st.sidebar.selectbox("Select case", samples, format_func=lambda s: f"{s['id']} — {s['specialty']}")
    demo_text = chosen.get("text_ar", chosen["text_en"])
    st.session_state["demo_img"] = make_demo_image(demo_text)
    st.session_state["demo_text"] = demo_text
    st.rerun()

# ── Main area ────────────────────────────────────────────────────────────────
uploaded = st.file_uploader("Upload a medical document image", type=["png", "jpg", "jpeg", "webp", "bmp", "tiff"])

img_source = None
if uploaded:
    img_source = Image.open(uploaded)
elif "demo_img" in st.session_state:
    img_source = st.session_state["demo_img"]
    st.info(f"📝 Demo ground truth: *{st.session_state['demo_text']}*")

if img_source:
    ocr = get_ocr(lang)
    col_img, col_res = st.columns([1, 1])
    with col_img:
        st.image(img_source, caption="Uploaded Image", use_column_width=True)
    if st.button("🔍 Run OCR", type="primary"):
        if ocr is None:
            st.warning("PaddleOCR not available. Install: pip install paddleocr paddlepaddle")
        else:
            with st.spinner("Running OCR…"):
                results = run_ocr(ocr, img_source)
            if results:
                with col_res:
                    st.subheader("Results")
                    total_conf = sum(r["confidence"] for r in results) / len(results)
                    st.metric("Avg Confidence", f"{total_conf:.1%}")
                    st.metric("Lines Detected", len(results))
                    for i, r in enumerate(results):
                        st.markdown(f"**Line {i+1}** ({r['confidence']:.0%}): {r['text']}")
            else:
                st.info("No text detected in the image.")
else:
    st.markdown("""
    ### Getting Started
    1. **Upload** a medical document image (prescription, lab report, etc.)
    2. Click **Run OCR** to extract text
    3. Or click **"Try Demo"** in the sidebar for a quick test

    Supports Arabic and English medical handwriting.
    """)