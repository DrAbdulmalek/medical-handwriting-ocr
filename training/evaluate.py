#!/usr/bin/env python3
"""
Evaluate TrOCR model on test set.
Computes CER, WER, and medical term accuracy.
"""

import os
import json
import argparse
from pathlib import Path
from typing import List, Dict

import torch
from PIL import Image
from transformers import TrOCRProcessor, VisionEncoderDecoderModel
import jiwer


class ModelEvaluator:
    def __init__(
        self,
        model_path: str,
        test_dataset_dir: str = './hf_dataset/test',
        device: str = 'cuda' if torch.cuda.is_available() else 'cpu'
    ):
        self.device = torch.device(device)
        self.model_path = model_path
        self.test_dir = Path(test_dataset_dir)

        # Load model and processor
        print(f"Loading model from: {model_path}")
        self.processor = TrOCRProcessor.from_pretrained(model_path)
        self.model = VisionEncoderDecoderModel.from_pretrained(model_path)
        self.model.to(self.device)
        self.model.eval()

        # Load test metadata
        self.test_data = []
        metadata_path = self.test_dir / 'metadata.jsonl'
        if metadata_path.exists():
            with open(metadata_path, 'r', encoding='utf-8') as f:
                for line in f:
                    self.test_data.append(json.loads(line))

    def predict(self, image: Image.Image) -> str:
        """Run inference on a single image."""
        pixel_values = self.processor(image, return_tensors="pt").pixel_values.to(self.device)

        with torch.no_grad():
            generated_ids = self.model.generate(pixel_values)

        return self.processor.batch_decode(generated_ids, skip_special_tokens=True)[0]

    def evaluate(self) -> Dict:
        """Evaluate model on entire test set."""
        print(f"\nEvaluating on {len(self.test_data)} samples...")

        predictions = []
        ground_truths = []

        correct_count = 0
        medical_correct = 0
        medical_total = 0

        for record in self.test_data:
            image_path = self.test_dir / record['file_name']

            if not image_path.exists():
                continue

            image = Image.open(image_path).convert('RGB')
            pred = self.predict(image)
            gt = record['text']

            predictions.append(pred)
            ground_truths.append(gt)

            if pred == gt:
                correct_count += 1

            if record.get('is_medical_term'):
                medical_total += 1
                if pred == gt:
                    medical_correct += 1

        # Compute metrics
        cer = jiwer.cer(ground_truths, predictions)
        wer = jiwer.wer(ground_truths, predictions)
        accuracy = correct_count / len(predictions) if predictions else 0
        medical_acc = medical_correct / medical_total if medical_total > 0 else 0

        results = {
            'total_samples': len(predictions),
            'cer': cer,
            'wer': wer,
            'exact_accuracy': accuracy,
            'medical_term_accuracy': medical_acc,
            'medical_terms_count': medical_total
        }

        # Print results
        print("\n" + "=" * 50)
        print("EVALUATION RESULTS")
        print("=" * 50)
        print(f"  Total Samples:    {results['total_samples']}")
        print(f"  CER:              {results['cer']:.4f} ({(1-results['cer'])*100:.1f}% char accuracy)")
        print(f"  WER:              {results['wer']:.4f} ({(1-results['wer'])*100:.1f}% word accuracy)")
        print(f"  Exact Accuracy:   {results['exact_accuracy']*100:.1f}%")
        print(f"  Medical Term Acc: {results['medical_term_accuracy']*100:.1f}% ({medical_correct}/{medical_total})")
        print("=" * 50)

        return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Evaluate TrOCR model')
    parser.add_argument('--model', type=str, required=True, help='Path to trained model')
    parser.add_argument('--test-dir', type=str, default='./hf_dataset/test')
    args = parser.parse_args()

    evaluator = ModelEvaluator(args.model, args.test_dir)
    results = evaluator.evaluate()

    # Save results
    output_path = os.path.join(args.model, 'evaluation_results.json')
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_path}")
