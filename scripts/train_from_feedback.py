#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_from_feedback.py
=====================

جسر مباشر بين تصحيحات المستخدمين على HF Space وتدريب TrOCR.

يقرأ من قاعدة بيانات التصحيحات (corrections.db) ويُحوّلها إلى
بيانات تدريب جاهزة لنموذج TrOCR. يُغلق الحلقة:

    HF Space (تصحيحات المستخدم) → crops + نصوص صحيحة → بيانات تدريب → نموذج محسّن

الميزات:
- استخراج صور القطع من crop_base64 في قاعدة البيانات
- فلترة تلقائية: إزالة التكرارات، التصحيحات القصيرة جداً، والتصحيحات الفارغة
- تقسيم تلقائي إلى تدريب/اختبار مع ضمان عدم تسرب البيانات
- توسيع المفردات العربية تلقائياً قبل التدريب
- دمج مباشر مع إطار التدريب الموجود (train_trocr_lora.py) أو التدريب المستقل
- إحصائيات تفصيلية قبل البدء (توزيع المحركات، طول النصوص، جودة البيانات)

الاستخدام:
    # التحويل فقط (بدون تدريب):
    python scripts/train_from_feedback.py --db-path ../corrections.db --output-dir ./feedback-data --convert-only

    # التحويل + التدريب بـ LoRA:
    python scripts/train_from_feedback.py --db-path ../corrections.db --output-dir ./feedback-data --train --lora-config ../omni-medical-suite/packages/training-framework/configs/trocr_lora_arabic.yaml

    # التحويل + التدريب مباشرة (بدون LoRA):
    python scripts/train_from_feedback.py --db-path ../corrections.db --output-dir ./feedback-data --train --base-model microsoft/trocr-base-handwritten --epochs 10

    # دمج مع بيانات اصطناعية موجودة:
    python scripts/train_from_feedback.py --db-path ../corrections.db --output-dir ./feedback-data --train --merge-with ./synthetic-data

المؤلف: Dr. Abdulmalek Al-husseini
"""

import argparse
import base64
import hashlib
import json
import logging
import os
import random
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

# ============================================================================
# إعدادات التسجيل
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("train_from_feedback.log", encoding="utf-8", mode="a"),
    ],
)
logger = logging.getLogger(__name__)


# ============================================================================
# ثوابت
# ============================================================================

MIN_TEXT_LENGTH = 2          # أقل طول مقبول للنص المصحح
MIN_IMAGE_DIM = 10           # أقل بُعد مقبول للصورة (بكسل)
MAX_IMAGE_DIM = 1024         # أكبر بُعد (يُصغَّر إن تجاوز)
DEFAULT_TRAIN_RATIO = 0.85   # نسبة التدريب
DEFAULT_IMAGE_SIZE = (384, 384)  # حجم الصورة لـ TrOCR
DEDUP_HASH_LENGTH = 16       # طول هاش إزالة التكرار

# الأحرف العربية الإضافية التي قد لا تكون في مفردات النموذج الأساسي
ARABIC_EXTRA_CHARS = (
    "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"
    "آأإئةؤىًَُِّْ"
    "٠١٢٣٤٥٦٧٨٩"
    ".,:؛-()/?"
)


# ============================================================================
# المرحلة 1: قراءة وفحص قاعدة بيانات التصحيحات
# ============================================================================

def read_corrections_db(db_path: str) -> List[Dict]:
    """قراءة جميع التصحيحات من قاعدة بيانات HF Space.

    Parameters
    ----------
    db_path : str
        مسار ملف corrections.db

    Returns
    -------
    list of dict
        كل إدخال يحتوي: raw_text, corrected_text, crop_base64, all_engine_texts, confidence
    """
    if not os.path.exists(db_path):
        logger.error("قاعدة البيانات غير موجودة: %s", db_path)
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT raw_text, corrected_text, crop_base64, all_engine_texts, confidence, created_at "
        "FROM corrections ORDER BY id"
    ).fetchall()
    conn.close()

    corrections = []
    for r in rows:
        corrections.append({
            "raw_text": r["raw_text"],
            "corrected_text": r["corrected_text"],
            "crop_base64": r["crop_base64"] or "",
            "all_engine_texts": json.loads(r["all_engine_texts"]) if r["all_engine_texts"] else {},
            "confidence": r["confidence"],
            "created_at": r["created_at"],
        })

    logger.info("تمت قراءة %d تصحيح من %s", len(corrections), db_path)
    return corrections


def filter_corrections(corrections: List[Dict]) -> List[Dict]:
    """فلترة التصحيحات: إزالة غير الصالحة والتكرارات.

    الفلترة تشمل:
    1. نص مصحح فارغ أو قصير جداً
    2. نص مصحح = نص أصلي (لا تغيير)
    3. لا توجد صورة (crop_base64 فارغ)
    4. تكرارات بناءً على هاش النص + الصورة
    5. صورة صغيرة جداً أو تالفة

    Parameters
    ----------
    corrections : list of dict

    Returns
    -------
    list of dict
        التصحيحات الصالحة بعد الفلترة
    """
    valid = []
    seen_hashes = set()
    stats = {
        "total": len(corrections),
        "empty_text": 0,
        "too_short": 0,
        "no_change": 0,
        "no_image": 0,
        "duplicate": 0,
        "bad_image": 0,
    }

    for corr in corrections:
        corrected = corr["corrected_text"].strip()
        raw = corr["raw_text"].strip()

        # فلترة النص الفارغ
        if not corrected:
            stats["empty_text"] += 1
            continue

        # فلترة النص القصير جداً
        if len(corrected) < MIN_TEXT_LENGTH:
            stats["too_short"] += 1
            continue

        # فلترة عدم وجود تغيير
        if corrected == raw:
            stats["no_change"] += 1
            continue

        # فلترة عدم وجود صورة
        crop_b64 = corr.get("crop_base64", "")
        if not crop_b64:
            stats["no_image"] += 1
            continue

        # فك تشفير الصورة والتحقق منها
        try:
            img_bytes = base64.b64decode(crop_b64)
            img = Image.open(BytesIO(img_bytes))
            w, h = img.size

            if w < MIN_IMAGE_DIM or h < MIN_IMAGE_DIM:
                stats["bad_image"] += 1
                continue

        except Exception:
            stats["bad_image"] += 1
            continue

        # إزالة التكرارات
        content_hash = hashlib.md5(
            (corrected + crop_b64[:200]).encode("utf-8")
        ).hexdigest()[:DEDUP_HASH_LENGTH]

        if content_hash in seen_hashes:
            stats["duplicate"] += 1
            continue
        seen_hashes.add(content_hash)

        valid.append(corr)

    # طباعة الإحصائيات
    logger.info("=" * 50)
    logger.info("إحصائيات الفلترة:")
    logger.info("  الإجمالي:         %d", stats["total"])
    logger.info("  نص فارغ:          %d", stats["empty_text"])
    logger.info("  نص قصير جداً:     %d", stats["too_short"])
    logger.info("  بدون تغيير:       %d", stats["no_change"])
    logger.info("  بدون صورة:        %d", stats["no_image"])
    logger.info("  صورة تالفة/صغيرة: %d", stats["bad_image"])
    logger.info("  تكرارات:          %d", stats["duplicate"])
    logger.info("  الصالح للتدريب:   %d", len(valid))
    logger.info("=" * 50)

    return valid


def compute_data_stats(corrections: List[Dict]) -> Dict:
    """حساب إحصائيات تفصيلية للبيانات.

    تشمل: توزيع أطوال النصوص، توزيع المحركات، متوسط الثقة،
    توزيع زمني للتصحيحات.
    """
    if not corrections:
        return {}

    lengths = [len(c["corrected_text"]) for c in corrections]
    confidences = [c["confidence"] for c in corrections if c["confidence"] > 0]

    # توزيع المحركات
    engine_counts: Dict[str, int] = {}
    for c in corrections:
        engines = c.get("all_engine_texts", {})
        for engine_name in engines:
            engine_counts[engine_name] = engine_counts.get(engine_name, 0) + 1

    # توزيع أطوال النصوص في مجموعات
    length_bins = {"1-5": 0, "6-15": 0, "16-30": 0, "31-50": 0, "50+": 0}
    for l in lengths:
        if l <= 5:
            length_bins["1-5"] += 1
        elif l <= 15:
            length_bins["6-15"] += 1
        elif l <= 30:
            length_bins["16-30"] += 1
        elif l <= 50:
            length_bins["31-50"] += 1
        else:
            length_bins["50+"] += 1

    return {
        "total_samples": len(corrections),
        "text_length": {
            "min": min(lengths),
            "max": max(lengths),
            "mean": round(np.mean(lengths), 1),
            "median": round(float(np.median(lengths)), 1),
            "distribution": length_bins,
        },
        "confidence": {
            "mean": round(np.mean(confidences), 3) if confidences else 0,
            "min": round(min(confidences), 3) if confidences else 0,
            "max": round(max(confidences), 3) if confidences else 0,
            "has_confidence": len(confidences),
        },
        "engine_distribution": engine_counts,
    }


# ============================================================================
# المرحلة 2: تحويل إلى بيانات تدريب
# ============================================================================

def decode_crop_image(crop_base64: str, target_size: Tuple[int, int] = DEFAULT_IMAGE_SIZE) -> Optional[Image.Image]:
    """فك تشفير صورة القطع من base64 وتجهيزها لـ TrOCR.

    Parameters
    ----------
    crop_base64 : str
        الصورة بترميز base64
    target_size : tuple
        الحجم الهدف (العرض، الارتفاع). الصور الأكبر تُصغَّر مع الحفاظ على النسبة.

    Returns
    -------
    PIL.Image أو None إذا فشل الفك
    """
    try:
        img_bytes = base64.b64decode(crop_base64)
        img = Image.open(BytesIO(img_bytes)).convert("L")  # تدرج رمادي

        # تصغير الصور الكبيرة جداً
        max_dim = max(img.size)
        if max_dim > MAX_IMAGE_DIM:
            scale = MAX_IMAGE_DIM / max_dim
            new_w = int(img.width * scale)
            new_h = int(img.height * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        # إضافة حشوة بيضاء لجعل الحجم قريباً من target_size
        # TrOCR يتعامل مع أحجام مختلفة لكن حجم ثابت يُسرّع التدريب
        w, h = img.size
        target_w, target_h = target_size

        # إنشاء خلفية بيضاء
        canvas = Image.new("L", (target_w, target_h), color=255)
        # لصق الصورة في المنتصف
        paste_x = max(0, (target_w - w) // 2)
        paste_y = max(0, (target_h - h) // 2)
        # تصغير إذا كانت أكبر من الهدف
        if w > target_w or h > target_h:
            img.thumbnail((target_w - 4, target_h - 4), Image.LANCZOS)
            w, h = img.size
            paste_x = max(0, (target_w - w) // 2)
            paste_y = max(0, (target_h - h) // 2)
        canvas.paste(img, (paste_x, paste_y))

        return canvas

    except Exception as e:
        logger.debug("فشل فك تشفير صورة: %s", e)
        return None


def convert_to_training_dataset(
    corrections: List[Dict],
    output_dir: str,
    train_ratio: float = DEFAULT_TRAIN_RATIO,
    seed: int = 42,
) -> Dict:
    """تحويل التصحيحات إلى بيانات تدريب جاهزة لـ TrOCR.

    يُنشئ:
    - output_dir/images/train/  و  output_dir/images/test/
    - output_dir/train.jsonl  و  output_dir/test.jsonl
    - output_dir/metadata.json  (إحصائيات)

    Parameters
    ----------
    corrections : list of dict
        التصحيحات المفلترة
    output_dir : str
        مجلد الإخراج
    train_ratio : float
        نسبة التدريب (الافتراضي 0.85)
    seed : int
        بذرة العشوائية للتقسيم

    Returns
    -------
    dict
        إحصائيات التحويل
    """
    output_path = Path(output_dir)
    train_img_dir = output_path / "images" / "train"
    test_img_dir = output_path / "images" / "test"
    train_img_dir.mkdir(parents=True, exist_ok=True)
    test_img_dir.mkdir(parents=True, exist_ok=True)

    # خلط البيانات
    random.seed(seed)
    shuffled = list(corrections)
    random.shuffle(shuffled)

    split_idx = int(len(shuffled) * train_ratio)
    train_items = shuffled[:split_idx]
    test_items = shuffled[split_idx:]

    conversion_stats = {
        "train_total": len(train_items),
        "test_total": len(test_items),
        "train_saved": 0,
        "test_saved": 0,
        "train_failed": 0,
        "test_failed": 0,
        "output_dir": str(output_path),
        "created_at": datetime.now().isoformat(),
    }

    def _save_split(items: List[Dict], img_dir: Path, jsonl_path: Path, split_name: str):
        """حفظ مجموعة واحدة (تدريب أو اختبار)."""
        saved = 0
        failed = 0

        with open(jsonl_path, "w", encoding="utf-8") as f:
            for i, corr in enumerate(items):
                img = decode_crop_image(corr["crop_base64"])
                if img is None:
                    failed += 1
                    continue

                img_filename = f"{split_name}_{i:06d}.png"
                img_path = img_dir / img_filename
                img.save(str(img_path))

                entry = {
                    "image_path": str(img_path),
                    "text": corr["corrected_text"],
                    "raw_text": corr["raw_text"],
                    "confidence": corr["confidence"],
                    "engines": list(corr.get("all_engine_texts", {}).keys()),
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                saved += 1

        return saved, failed

    train_saved, train_failed = _save_split(
        train_items, train_img_dir,
        output_path / "train.jsonl", "train"
    )
    test_saved, test_failed = _save_split(
        test_items, test_img_dir,
        output_path / "test.jsonl", "test"
    )

    conversion_stats["train_saved"] = train_saved
    conversion_stats["test_saved"] = test_saved
    conversion_stats["train_failed"] = train_failed
    conversion_stats["test_failed"] = test_failed

    # حفظ الإحصائيات
    with open(output_path / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(conversion_stats, f, ensure_ascii=False, indent=2)

    logger.info("تم التحويل بنجاح:")
    logger.info("  التدريب:   %d / %d (فشل: %d)", train_saved, len(train_items), train_failed)
    logger.info("  الاختبار:  %d / %d (فشل: %d)", test_saved, len(test_items), test_failed)
    logger.info("  الإخراج:   %s", output_path)

    return conversion_stats


# ============================================================================
# المرحلة 3: دمج مع بيانات موجودة (اختياري)
# ============================================================================

def merge_with_existing(
    feedback_dir: str,
    existing_dir: str,
    output_dir: str,
    feedback_weight: float = 0.7,
    seed: int = 42,
) -> Dict:
    """دمج بيانات التصحيحات مع بيانات تدريب موجودة (اصطناعية أو حقيقية).

    يعطي وزناً أعلى لبيانات التصحيحات (feedback_weight) لأنها أكثر دقة.

    Parameters
    ----------
    feedback_dir : str
        مجلد بيانات التصحيحات المحوَّلة
    existing_dir : str
        مجلد البيانات الموجودة (يحتوي train.jsonl أو metadata.jsonl)
    output_dir : str
        مجلد الإخراج المدمج
    feedback_weight : float
        نسبة بيانات التصحيحات في المجموعة المدمجة
    seed : int
        بذرة العشوائية

    Returns
    -------
    dict
        إحصائيات الدمج
    """
    feedback_path = Path(feedback_dir)
    existing_path = Path(existing_dir)
    output_path = Path(output_dir)

    # تحميل بيانات التصحيحات
    fb_train = _load_jsonl(feedback_path / "train.jsonl")
    fb_test = _load_jsonl(feedback_path / "test.jsonl")

    # تحميل البيانات الموجودة
    ex_items = []
    for jsonl_name in ["train.jsonl", "metadata.jsonl"]:
        p = existing_path / jsonl_name
        if p.exists():
            ex_items.extend(_load_jsonl(p))
            break

    if not ex_items:
        logger.warning("لا توجد بيانات موجودة في %s — سيتم استخدام بيانات التصحيحات فقط", existing_dir)
        ex_items = []

    # حساب الأحجام
    fb_count = len(fb_train)
    ex_count = len(ex_items)

    # أخذ عينة من البيانات الموجودة بما يتناسب مع الوزن
    if ex_count > 0 and fb_count > 0:
        target_ex_count = int(fb_count * (1 - feedback_weight) / feedback_weight)
        target_ex_count = max(target_ex_count, 10)  # حد أدنى
        target_ex_count = min(target_ex_count, ex_count)

        random.seed(seed)
        ex_sampled = random.sample(ex_items, target_ex_count)
    else:
        ex_sampled = ex_items

    # دمج
    merged_train = fb_train + ex_sampled
    random.seed(seed)
    random.shuffle(merged_train)

    # حفظ
    output_path.mkdir(parents=True, exist_ok=True)
    merged_img_dir = output_path / "images" / "train"
    merged_img_dir.mkdir(parents=True, exist_ok=True)

    with open(output_path / "train.jsonl", "w", encoding="utf-8") as f:
        for i, item in enumerate(merged_train):
            # نسخ الصور إن كانت محلية
            src_img = item.get("image_path", "")
            if src_img and os.path.exists(src_img):
                dst_img = merged_img_dir / f"merged_{i:06d}.png"
                try:
                    img = Image.open(src_img).convert("L")
                    img.save(str(dst_img))
                    item["image_path"] = str(dst_img)
                except Exception:
                    pass
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    # نسخ مجموعة الاختبار من التصحيحات فقط (أكثر دقة)
    import shutil
    test_img_dir = output_path / "images" / "test"
    if test_img_dir.exists():
        shutil.rmtree(test_img_dir)
    shutil.copytree(feedback_path / "images" / "test", test_img_dir)
    shutil.copy2(feedback_path / "test.jsonl", output_path / "test.jsonl")

    stats = {
        "feedback_train": fb_count,
        "existing_sampled": len(ex_sampled),
        "merged_train": len(merged_train),
        "test": len(fb_test),
        "feedback_weight": feedback_weight,
        "output_dir": str(output_path),
    }

    logger.info("تم الدمج:")
    logger.info("  تصحيحات:     %d", fb_count)
    logger.info("  موجودة:      %d → %d (بعد العينات)", ex_count, len(ex_sampled))
    logger.info("  المدمجة:      %d", len(merged_train))
    logger.info("  الاختبار:    %d", len(fb_test))

    return stats


def _load_jsonl(path: Path) -> List[Dict]:
    """تحميل ملف JSONL."""
    if not path.exists():
        return []
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return items


# ============================================================================
# المرحلة 4: التدريب (اختياري)
# ============================================================================

def train_with_lora(
    data_dir: str,
    lora_config_path: str,
    output_model_dir: str,
) -> bool:
    """تشغيل التدريب باستخدام إطار LoRA الموجود.

    يستدعي train_trocr_lora.py من omni-medical-suite مع البيانات المحوَّلة.

    Parameters
    ----------
    data_dir : str
        مجلد البيانات المحوَّلة (يحتوي train.jsonl)
    lora_config_path : str
        مسار ملف إعدادات LoRA (YAML)
    output_model_dir : str
        مجلد إخراج النموذج المدرب

    Returns
    -------
    bool
        True إذا نجح التدريب
    """
    import importlib.util

    lora_script = Path(lora_config_path).parent.parent / "scripts" / "train_trocr_lora.py"
    if not lora_script.exists():
        logger.error("سكريبت LoRA غير موجود: %s", lora_script)
        return False

    logger.info("بدء التدريب بـ LoRA...")
    logger.info("  بيانات:  %s", data_dir)
    logger.info("  إعدادات: %s", lora_config_path)
    logger.info("  إخراج:   %s", output_model_dir)

    # استدعاء سكريبت التدريب
    import subprocess
    cmd = [
        sys.executable, str(lora_script),
        "--config", lora_config_path,
        "--dataset", data_dir,
        "--output-dir", output_model_dir,
    ]

    logger.info("أمر التدريب: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=False)

    if result.returncode == 0:
        logger.info("تم التدريب بنجاح! النموذج في: %s", output_model_dir)
        return True
    else:
        logger.error("فشل التدريب (رمز الخروج: %d)", result.returncode)
        return False


def train_standalone(
    data_dir: str,
    output_model_dir: str,
    base_model: str = "microsoft/trocr-base-handwritten",
    epochs: int = 10,
    batch_size: int = 4,
    learning_rate: float = 3e-5,
) -> bool:
    """تدريب مستقل بـ Trainer API (بدون LoRA).

    مفيد عندما لا يتوفر إطار LoRA أو للتجربة السريعة.

    Parameters
    ----------
    data_dir : str
        مجلد البيانات (train.jsonl + images/)
    output_model_dir : str
        مجلد إخراج النموذج
    base_model : str
        اسم النموذج الأساسي من HuggingFace
    epochs : int
        عدد دورات التدريب
    batch_size : int
        حجم الدفعة
    learning_rate : float
        معدل التعلم

    Returns
    -------
    bool
    """
    try:
        import torch
        from transformers import (
            TrOCRProcessor,
            VisionEncoderDecoderModel,
            Seq2SeqTrainingArguments,
            Seq2SeqTrainer,
            default_data_collator,
        )
        from torch.utils.data import Dataset
        from evaluate import load as load_metric
    except ImportError as e:
        logger.error("تبعيات مفقودة: %s", e)
        logger.error("ثبّت: pip install transformers torch accelerate evaluate jiwer")
        return False

    logger.info("بدء التدريب المستقل...")
    logger.info("  نموذج أساسي: %s", base_model)
    logger.info("  دورات: %d | دفعة: %d | معدل تعلم: %s", epochs, batch_size, learning_rate)

    # تحميل المعالج والنموذج
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("  الجهاز: %s", device)

    processor = TrOCRProcessor.from_pretrained(base_model)
    model = VisionEncoderDecoderModel.from_pretrained(base_model)

    # إعداد token IDs
    model.config.decoder_start_token_id = processor.tokenizer.cls_token_id
    model.config.pad_token_id = processor.tokenizer.pad_token_id
    model.config.eos_token_id = processor.tokenizer.sep_token_id

    # توسيع المفردات العربية
    tokenizer = processor.tokenizer
    vocab = tokenizer.get_vocab()
    new_tokens = [c for c in ARABIC_EXTRA_CHARS if c not in vocab]
    if new_tokens:
        logger.info("إضافة %d رمز عربي جديد للمفردات", len(new_tokens))
        tokenizer.add_tokens(new_tokens)
        model.decoder.resize_token_embeddings(len(tokenizer))

    model.to(device)

    # تحميل البيانات
    class FeedbackDataset(Dataset):
        def __init__(self, jsonl_path: str, processor: TrOCRProcessor, max_length: int = 128):
            self.processor = processor
            self.max_length = max_length
            self.items = _load_jsonl(Path(jsonl_path))
            logger.info("حمّل %d عينة من %s", len(self.items), jsonl_path)

        def __len__(self):
            return len(self.items)

        def __getitem__(self, idx):
            item = self.items[idx]
            img_path = item.get("image_path", "")

            if img_path and os.path.exists(img_path):
                image = Image.open(img_path).convert("RGB")
            else:
                # صورة فارغة ك fallback
                image = Image.new("RGB", (384, 384), color=(255, 255, 255))

            pixel_values = self.processor(image, return_tensors="pt").pixel_values.squeeze(0)
            labels = self.processor.tokenizer(
                item["text"],
                padding="max_length",
                max_length=self.max_length,
                truncation=True,
                return_tensors="pt",
            ).input_ids.squeeze(0)
            labels[labels == self.processor.tokenizer.pad_token_id] = -100

            return {"pixel_values": pixel_values, "labels": labels}

    data_path = Path(data_dir)
    train_dataset = FeedbackDataset(str(data_path / "train.jsonl"), processor)
    eval_dataset = FeedbackDataset(str(data_path / "test.jsonl"), processor) if (data_path / "test.jsonl").exists() else None

    if len(train_dataset) == 0:
        logger.error("لا توجد بيانات تدريب!")
        return False

    # مقاييس التقييم
    cer_metric = load_metric("cer")
    wer_metric = load_metric("wer")

    def compute_metrics(pred):
        labels = pred.label_ids
        pred_ids = pred.predictions
        labels[labels == -100] = processor.tokenizer.pad_token_id
        pred_str = processor.tokenizer.batch_decode(pred_ids, skip_special_tokens=True)
        label_str = processor.tokenizer.batch_decode(labels, skip_special_tokens=True)
        return {
            "cer": cer_metric.compute(predictions=pred_str, references=label_str),
            "wer": wer_metric.compute(predictions=pred_str, references=label_str),
        }

    # إعدادات التدريب
    os.makedirs(output_model_dir, exist_ok=True)

    training_args = Seq2SeqTrainingArguments(
        output_dir=output_model_dir,
        evaluation_strategy="steps" if eval_dataset else "no",
        eval_steps=50 if eval_dataset else None,
        save_steps=50,
        logging_steps=10,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        gradient_accumulation_steps=2,
        learning_rate=learning_rate,
        warmup_steps=max(10, len(train_dataset) // (batch_size * 4)),
        num_train_epochs=epochs,
        weight_decay=0.01,
        fp16=torch.cuda.is_available(),
        predict_with_generate=True,
        generation_max_length=128,
        load_best_model_at_end=eval_dataset is not None,
        metric_for_best_model="cer",
        greater_is_better=False,
        save_total_limit=2,
        report_to="none",
    )

    trainer_kwargs = {
        "model": model,
        "args": training_args,
        "train_dataset": train_dataset,
        "compute_metrics": compute_metrics if eval_dataset else None,
        "data_collator": default_data_collator,
    }
    if eval_dataset:
        trainer_kwargs["eval_dataset"] = eval_dataset

    trainer = Seq2SeqTrainer(**trainer_kwargs)

    # بدء التدريب
    logger.info("بدء %d دورة تدريب على %d عينة...", epochs, len(train_dataset))
    trainer.train()

    # حفظ النموذج النهائي
    final_dir = os.path.join(output_model_dir, "final")
    model.save_pretrained(final_dir)
    processor.save_pretrained(final_dir)
    logger.info("تم حفظ النموذج النهائي في: %s", final_dir)

    # تقييم نهائي
    if eval_dataset and len(eval_dataset) > 0:
        metrics = trainer.evaluate()
        logger.info("نتائج التقييم النهائي:")
        logger.info("  CER: %.4f", metrics.get("eval_cer", 0))
        logger.info("  WER: %.4f", metrics.get("eval_wer", 0))

    return True


# ============================================================================
# المرحلة 5: التصدير (اختياري)
# ============================================================================

def export_for_huggingface(
    model_dir: str,
    repo_name: str,
    commit_message: str = "TrOCR fine-tuned on HF Space user feedback",
) -> bool:
    """رفع النموذج المدرب إلى HuggingFace Hub.

    Parameters
    ----------
    model_dir : str
        مجلد النموذج النهائي
    repo_name : str
        اسم المستودع (مثل: DrAbdulmalek/trocr-medical-arabic)
    commit_message : str
        رسالة الرفع

    Returns
    -------
    bool
    """
    try:
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
    except ImportError:
        logger.error("تبعية transformers مفقودة")
        return False

    logger.info("رفع النموذج إلى: %s", repo_name)

    try:
        model = VisionEncoderDecoderModel.from_pretrained(model_dir)
        processor = TrOCRProcessor.from_pretrained(model_dir)

        model.push_to_hub(repo_name, commit_message=commit_message)
        processor.push_to_hub(repo_name, commit_message=commit_message)

        logger.info("تم الرفع بنجاح إلى %s", repo_name)
        return True

    except Exception as e:
        logger.error("فشل الرفع: %s", e)
        logger.error("تأكد من تسجيل الدخول: huggingface-cli login")
        return False


# ============================================================================
# التطبيق الرئيسي
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="تحويل تصحيحات HF Space إلى بيانات تدريب TrOCR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
أمثلة:
  # تحويل فقط:
  python %(prog)s --db-path corrections.db --output-dir ./feedback-data --convert-only

  # تحويل + تدريب مستقل:
  python %(prog)s --db-path corrections.db --output-dir ./feedback-data --train --epochs 10

  # تحويل + تدريب بـ LoRA:
  python %(prog)s --db-path corrections.db --output-dir ./feedback-data --train --lora-config configs/trocr_lora_arabic.yaml

  # تحويل + دمج + تدريب + رفع:
  python %(prog)s --db-path corrections.db --output-dir ./feedback-data --train --merge-with ./synthetic --push-to-hub DrAbdulmalek/trocr-medical-arabic
        """,
    )

    # المدخلات
    parser.add_argument("--db-path", type=str, default=None,
                        help="مسار قاعدة بيانات التصحيحات (corrections.db). الافتراضي: ../corrections.db")
    parser.add_argument("--output-dir", type=str, default="./feedback-training-data",
                        help="مجلد إخراج البيانات المحوَّلة")
    parser.add_argument("--train-ratio", type=float, default=DEFAULT_TRAIN_RATIO,
                        help="نسبة التدريب (الافتراضي: 0.85)")

    # خيارات التحويل
    parser.add_argument("--convert-only", action="store_true",
                        help="تحويل البيانات فقط بدون تدريب")
    parser.add_argument("--merge-with", type=str, default=None,
                        help="دمج مع بيانات تدريب موجودة في هذا المجلد")
    parser.add_argument("--feedback-weight", type=float, default=0.7,
                        help="وزن بيانات التصحيحات عند الدمج (الافتراضي: 0.7)")

    # خيارات التدريب
    parser.add_argument("--train", action="store_true",
                        help="تشغيل التدريب بعد التحويل")
    parser.add_argument("--lora-config", type=str, default=None,
                        help="مسار ملف إعدادات LoRA (YAML). إذا لم يُحدد، يُستخدم التدريب المستقل.")
    parser.add_argument("--base-model", type=str, default="microsoft/trocr-base-handwritten",
                        help="النموذج الأساسي للتدريب المستقل")
    parser.add_argument("--epochs", type=int, default=10, help="عدد دورات التدريب")
    parser.add_argument("--batch-size", type=int, default=4, help="حجم الدفعة")
    parser.add_argument("--learning-rate", type=float, default=3e-5, help="معدل التعلم")
    parser.add_argument("--model-output", type=str, default=None,
                        help="مجلد إخراج النموذج (الافتراضي: output_dir/model)")

    # خيارات التصدير
    parser.add_argument("--push-to-hub", type=str, default=None,
                        help="رفع النموذج إلى HuggingFace (مثل: username/trocr-medical-arabic)")
    parser.add_argument("--dry-run", action="store_true",
                        help="عرض الإحصائيات فقط بدون كتابة ملفات")

    args = parser.parse_args()

    # تحديد مسار قاعدة البيانات
    db_path = args.db_path
    if db_path is None:
        # البحث التلقائي
        script_dir = Path(__file__).parent.parent
        possible_paths = [
            script_dir / "corrections.db",
            Path("../corrections.db"),
            Path("./corrections.db"),
        ]
        for p in possible_paths:
            if p.exists():
                db_path = str(p)
                break

    if not db_path:
        logger.error("لم يتم العثور على قاعدة بيانات التصحيحات. حدد المسار بـ --db-path")
        sys.exit(1)

    # ─── المرحلة 1: القراءة والفحص ───
    logger.info("=" * 60)
    logger.info("المرحلة 1: قراءة قاعدة بيانات التصحيحات")
    logger.info("=" * 60)

    corrections = read_corrections_db(db_path)
    if not corrections:
        logger.error("لا توجد تصحيحات في قاعدة البيانات!")
        sys.exit(1)

    # إحصائيات قبل الفلترة
    pre_stats = compute_data_stats(corrections)
    if pre_stats:
        logger.info("إحصائيات البيانات الخام:")
        logger.info("  إجمالي التصحيحات: %d", pre_stats["total_samples"])
        logger.info("  طول النصوص: %s", pre_stats["text_length"])
        logger.info("  متوسط الثقة: %s", pre_stats["confidence"]["mean"])
        if pre_stats["engine_distribution"]:
            logger.info("  المحركات: %s", pre_stats["engine_distribution"])

    # ─── الفلترة ───
    logger.info("")
    logger.info("فلترة البيانات...")
    valid_corrections = filter_corrections(corrections)

    if not valid_corrections:
        logger.error("لا توجد تصحيحات صالحة بعد الفلترة! لا يمكن التدريب.")
        logger.info("تأكد أن المستخدمين أجروا تصحيحات تحتوي على صور (crop_base64).")
        sys.exit(1)

    if args.dry_run:
        logger.info("[DRY RUN] لن يتم كتابة أي ملفات.")
        logger.info("التصحيحات الصالحة: %d", len(valid_corrections))
        sys.exit(0)

    # ─── المرحلة 2: التحويل ───
    logger.info("")
    logger.info("=" * 60)
    logger.info("المرحلة 2: تحويل إلى بيانات تدريب")
    logger.info("=" * 60)

    convert_stats = convert_to_training_dataset(
        valid_corrections,
        args.output_dir,
        train_ratio=args.train_ratio,
    )

    # ─── المرحلة 3: الدمج (اختياري) ───
    if args.merge_with:
        logger.info("")
        logger.info("=" * 60)
        logger.info("المرحلة 3: دمج مع بيانات موجودة")
        logger.info("=" * 60)

        merge_output = args.output_dir + "-merged"
        merge_stats = merge_with_existing(
            args.output_dir,
            args.merge_with,
            merge_output,
            feedback_weight=args.feedback_weight,
        )
        # استخدام المجلد المدمج للتدريب
        args.output_dir = merge_output

    # ─── المرحلة 4: التدريب (اختياري) ───
    if args.train and not args.convert_only:
        logger.info("")
        logger.info("=" * 60)
        logger.info("المرحلة 4: التدريب")
        logger.info("=" * 60)

        model_output = args.model_output or os.path.join(args.output_dir, "model")

        if args.lora_config:
            success = train_with_lora(args.output_dir, args.lora_config, model_output)
        else:
            success = train_standalone(
                args.output_dir,
                model_output,
                base_model=args.base_model,
                epochs=args.epochs,
                batch_size=args.batch_size,
                learning_rate=args.learning_rate,
            )

        if not success:
            logger.error("فشل التدريب!")
            sys.exit(1)

        # ─── المرحلة 5: التصدير (اختياري) ───
        if args.push_to_hub:
            logger.info("")
            logger.info("=" * 60)
            logger.info("المرحلة 5: رفع إلى HuggingFace Hub")
            logger.info("=" * 60)

            final_model_dir = os.path.join(model_output, "final")
            if os.path.exists(args.lora_config):
                # مع LoRA، المسار مختلف
                final_model_dir = model_output

            export_for_huggingface(final_model_dir, args.push_to_hub)

    # ─── الملخص النهائي ───
    logger.info("")
    logger.info("=" * 60)
    logger.info("اكتمل بنجاح!")
    logger.info("=" * 60)
    logger.info("مجلد البيانات:  %s", args.output_dir)
    if args.train and not args.convert_only:
        model_out = args.model_output or os.path.join(args.output_dir, "model")
        logger.info("مجلد النموذج:  %s", model_out)
    logger.info("الخطوة التالية:")
    logger.info("  1. راجع البيانات في %s", args.output_dir)
    if not args.train:
        logger.info("  2. شغّل التدريب: python %s --db-path %s --output-dir %s --train",
                     __file__, db_path, args.output_dir)
    else:
        logger.info("  2. اختبر النموذج المدرب على صور جديدة")
        if args.push_to_hub:
            logger.info("  3. حدّث hf-space-push/app/ocr_engine.py لاستخدام النموذج الجديد")


# ============================================================================
# حاجة لـ BytesIO
# ============================================================================
from io import BytesIO


if __name__ == "__main__":
    main()