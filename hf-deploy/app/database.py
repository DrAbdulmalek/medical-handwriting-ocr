"""
SQLite-based corrections database for Medical Handwriting OCR.

Stores user corrections and provides fuzzy pattern matching for improving
future OCR predictions through a learning feedback loop.

Schema:
- corrections: individual correction records (crop + texts + metadata)
- correction_patterns: aggregated pattern → correction mappings (for fast lookup)
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_db_connection: Optional[sqlite3.Connection] = None
_DB_PATH = Path(__file__).parent.parent / "corrections.db"


def get_db() -> sqlite3.Connection:
    """Get or create the SQLite database connection (thread-safe via check_same_thread=False)."""
    global _db_connection
    if _db_connection is None:
        _db_connection = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _db_connection.row_factory = sqlite3.Row
        _init_tables(_db_connection)
        logger.info("Corrections database opened at %s", _DB_PATH)
    return _db_connection


def _init_tables(conn: sqlite3.Connection) -> None:
    """Create tables if they do not exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crop_base64 TEXT,
            raw_text TEXT NOT NULL,
            corrected_text TEXT NOT NULL,
            all_engine_texts TEXT,
            best_engine TEXT,
            confidence REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            image_hash TEXT
        );

        CREATE TABLE IF NOT EXISTS correction_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern TEXT NOT NULL,
            correction TEXT NOT NULL,
            frequency INTEGER DEFAULT 1,
            last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(pattern, correction)
        );

        CREATE INDEX IF NOT EXISTS idx_corrections_raw ON corrections(raw_text);
        CREATE INDEX IF NOT EXISTS idx_corrections_corrected ON corrections(corrected_text);
        CREATE INDEX IF NOT EXISTS idx_patterns_pattern ON correction_patterns(pattern);
        CREATE INDEX IF NOT EXISTS idx_patterns_freq ON correction_patterns(frequency DESC);
    """)
    conn.commit()


def save_correction(
    raw_text: str,
    corrected_text: str,
    crop_base64: str = "",
    all_engine_texts: Optional[Dict[str, str]] = None,
    best_engine: str = "",
    confidence: float = 0.0,
    image_hash: str = "",
) -> int:
    """Save a user correction to the database.

    Returns the new correction id.
    """
    db = get_db()

    cursor = db.execute(
        """INSERT INTO corrections
           (crop_base64, raw_text, corrected_text, all_engine_texts,
            best_engine, confidence, image_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            crop_base64,
            raw_text,
            corrected_text,
            json.dumps(all_engine_texts, ensure_ascii=False) if all_engine_texts else None,
            best_engine,
            confidence,
            image_hash,
        ),
    )

    # Update aggregated pattern
    pattern = _normalize(raw_text)
    if pattern:
        db.execute(
            """INSERT INTO correction_patterns (pattern, correction, frequency, last_used)
               VALUES (?, ?, 1, ?)
               ON CONFLICT(pattern, correction)
               DO UPDATE SET frequency = frequency + 1, last_used = ?""",
            (pattern, corrected_text, datetime.utcnow().isoformat(), datetime.utcnow().isoformat()),
        )

    db.commit()
    return cursor.lastrowid


def save_corrections_batch(
    regions: List[Dict],
    corrections_data: List[Dict],
) -> Dict:
    """Save multiple corrections at once.

    Parameters
    ----------
    regions : list of dicts from OCR detection
    corrections_data : list of dicts with keys ``idx``, ``original_text``, ``corrected_text``

    Returns
    -------
    dict with ``saved``, ``skipped``, ``total`` counts.
    """
    saved = 0
    skipped = 0

    for corr in corrections_data:
        idx = corr.get("idx", -1)
        corrected = corr.get("corrected_text", "").strip()
        original = corr.get("original_text", "").strip()

        if not corrected or idx < 0 or idx >= len(regions):
            skipped += 1
            continue

        if corrected == original:
            skipped += 1
            continue

        region = regions[idx]
        save_correction(
            raw_text=original,
            corrected_text=corrected,
            crop_base64=region.get("crop_base64", ""),
            all_engine_texts=region.get("all_texts", {}),
            best_engine=region.get("best_engine", ""),
            confidence=region.get("confidence", 0.0),
        )
        saved += 1

    return {"saved": saved, "skipped": skipped, "total": len(corrections_data)}


def lookup_correction(text: str) -> Optional[str]:
    """Look up a prior correction for *text*.

    Strategy:
    1. Exact pattern match → return most frequent correction
    2. Fuzzy match using rapidfuzz (threshold 70 %)
    """
    if not text or not text.strip():
        return None

    db = get_db()
    pattern = _normalize(text)

    # 1) Exact match
    row = db.execute(
        "SELECT correction, frequency FROM correction_patterns WHERE pattern = ? ORDER BY frequency DESC LIMIT 1",
        (pattern,),
    ).fetchone()

    if row:
        logger.info("DB exact match: '%s' -> '%s' (freq %d)", text, row["correction"], row["frequency"])
        return row["correction"]

    # 2) Fuzzy match against recent corrections
    try:
        from rapidfuzz import fuzz

        rows = db.execute(
            "SELECT raw_text, corrected_text FROM corrections ORDER BY id DESC LIMIT 200"
        ).fetchall()

        best_correction = None
        best_score = 0.0

        for r in rows:
            score = fuzz.ratio(text, r["raw_text"])
            if score > best_score and score > 70:
                best_score = score
                best_correction = r["corrected_text"]

        if best_correction:
            logger.info("DB fuzzy match: '%s' -> '%s' (score %.0f%%)", text, best_correction, best_score)
            return best_correction
    except ImportError:
        logger.debug("rapidfuzz not available, skipping fuzzy lookup")

    return None


def _normalize(text: str) -> str:
    """Normalize text for pattern storage (lowercase, stripped)."""
    if not text:
        return ""
    return text.strip()


def get_stats() -> Dict:
    """Return database statistics."""
    db = get_db()
    total_corrections = db.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
    unique_patterns = db.execute("SELECT COUNT(*) FROM correction_patterns").fetchone()[0]
    top_patterns = db.execute(
        "SELECT pattern, correction, frequency FROM correction_patterns ORDER BY frequency DESC LIMIT 10"
    ).fetchall()

    return {
        "total_corrections": total_corrections,
        "unique_patterns": unique_patterns,
        "top_patterns": [
            {"pattern": r["pattern"], "correction": r["correction"], "frequency": r["frequency"]}
            for r in top_patterns
        ],
    }


def export_training_data() -> List[Dict]:
    """Export all corrections as training-ready data."""
    db = get_db()
    rows = db.execute(
        "SELECT raw_text, corrected_text, crop_base64, all_engine_texts, confidence FROM corrections ORDER BY id"
    ).fetchall()

    return [
        {
            "original_text": r["raw_text"],
            "corrected_text": r["corrected_text"],
            "crop_base64": r["crop_base64"],
            "all_engine_texts": json.loads(r["all_engine_texts"]) if r["all_engine_texts"] else {},
            "confidence": r["confidence"],
        }
        for r in rows
    ]
