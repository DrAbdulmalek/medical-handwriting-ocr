#!/usr/bin/env python3
"""
Export corrections from PostgreSQL to HuggingFace Dataset format
for TrOCR fine-tuning.
"""

import os
import json
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from PIL import Image
import io
from minio import Minio
from tqdm import tqdm


class DatasetExporter:
    def __init__(
        self,
        db_url: str = "postgresql://ocr_user:ocr_password_123@localhost:5432/medical_ocr",
        minio_endpoint: str = "localhost:9000",
        minio_access: str = "minioadmin",
        minio_secret: str = os.getenv("MINIO_SECRET_KEY", "CHANGE_ME"),
        minio_bucket: str = "ocr-crops",
        output_dir: str = "./hf_dataset"
    ):
        self.db_url = db_url
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # MinIO client
        self.minio = Minio(
            minio_endpoint,
            access_key=minio_access,
            secret_key=minio_secret,
            secure=False
        )
        self.bucket = minio_bucket

        # Statistics
        self.stats = {
            'total_gold': 0,
            'arabic': 0,
            'latin': 0,
            'mixed': 0,
            'medical_terms': 0
        }

    def fetch_gold_standard(self, min_corrections: int = 1) -> List[Dict]:
        """
        Fetch gold standard regions from database.
        """
        conn = psycopg2.connect(self.db_url)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        query = """
            SELECT
                tr.id,
                tr.bbox,
                tr.script_class,
                tr.predicted_text,
                tr.corrected_text,
                tr.confidence,
                tr.correction_count,
                tr.is_medical_term,
                tr.model_version,
                tr.created_at,
                tr.corrected_at,
                p.image_path as page_image_path
            FROM text_regions tr
            JOIN pages p ON tr.page_id = p.id
            WHERE tr.status = 'gold_standard'
               OR (tr.status = 'pending'
                   AND tr.corrected_text IS NOT NULL
                   AND tr.correction_count >= %s)
            ORDER BY tr.corrected_at DESC
        """

        cursor.execute(query, (min_corrections,))
        results = cursor.fetchall()
        conn.close()

        return [dict(row) for row in results]

    def process_and_save(
        self,
        records: List[Dict],
        split: str = 'train',
        max_size: int = 384
    ) -> Dict:
        """
        Process records and save in HuggingFace-compatible format.
        """
        split_dir = self.output_dir / split
        split_dir.mkdir(exist_ok=True)
        images_dir = split_dir / 'images'
        images_dir.mkdir(exist_ok=True)

        metadata = []

        for idx, record in enumerate(tqdm(records, desc=f"Processing {split}")):
            image_filename = f"{split}_{idx:06d}_{record['id']}.png"
            image_path = images_dir / image_filename

            # Save metadata
            entry = {
                'file_name': f"images/{image_filename}",
                'text': record['corrected_text'] or record['predicted_text'],
                'predicted_text': record['predicted_text'],
                'script_class': record['script_class'],
                'is_medical_term': record['is_medical_term'],
                'confidence': float(record['confidence']) if record['confidence'] else None,
                'correction_count': record['correction_count'],
                'model_version': record['model_version'],
                'original_bbox': record['bbox'],
                'region_id': str(record['id']),
                'corrected_at': record['corrected_at'].isoformat() if record['corrected_at'] else None
            }
            metadata.append(entry)

            # Update statistics
            self.stats['total_gold'] += 1
            if record['script_class'] == 'arabic':
                self.stats['arabic'] += 1
            elif record['script_class'] == 'latin':
                self.stats['latin'] += 1
            elif record['script_class'] == 'mixed':
                self.stats['mixed'] += 1
            if record['is_medical_term']:
                self.stats['medical_terms'] += 1

        # Save metadata as JSONL
        metadata_path = split_dir / 'metadata.jsonl'
        with open(metadata_path, 'w', encoding='utf-8') as f:
            for entry in metadata:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')

        print(f"Saved {len(metadata)} records to {metadata_path}")
        return metadata

    def export_all(self, test_ratio: float = 0.15, val_ratio: float = 0.15):
        """
        Export all gold standard data, split into train/val/test.
        """
        records = self.fetch_gold_standard()
        print(f"Found {len(records)} gold standard records")

        if not records:
            print("No records found. Please add corrections first.")
            return

        # Shuffle and split
        import random
        random.shuffle(records)

        n_test = int(len(records) * test_ratio)
        n_val = int(len(records) * val_ratio)

        test_records = records[:n_test]
        val_records = records[n_test:n_test + n_val]
        train_records = records[n_test + n_val:]

        print(f"Split: train={len(train_records)}, val={len(val_records)}, test={len(test_records)}")

        self.process_and_save(train_records, 'train')
        self.process_and_save(val_records, 'validation')
        self.process_and_save(test_records, 'test')

        # Print statistics
        print("\n=== Dataset Export Statistics ===")
        for key, value in self.stats.items():
            print(f"  {key}: {value}")

        print(f"\nDataset exported to: {self.output_dir}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Export OCR corrections to HuggingFace format')
    parser.add_argument('--db-url', type=str, default=os.environ.get('DATABASE_URL'))
    parser.add_argument('--minio-endpoint', type=str, default='localhost:9000')
    parser.add_argument('--output', type=str, default='./hf_dataset')
    args = parser.parse_args()

    exporter = DatasetExporter(
        db_url=args.db_url,
        minio_endpoint=args.minio_endpoint,
        output_dir=args.output
    )
    exporter.export_all()
