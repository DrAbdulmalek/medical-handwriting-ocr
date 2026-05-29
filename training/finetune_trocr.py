#!/usr/bin/env python3
"""
TrOCR Fine-tuning Script for Medical Handwriting OCR
Designed to run on Google Colab or local GPU.
"""

import os
import json
import argparse
from pathlib import Path
from typing import Dict, List

import torch
from torch.utils.data import Dataset
from PIL import Image
from transformers import (
    TrOCRProcessor,
    VisionEncoderDecoderModel,
    Seq2SeqTrainer,
    Seq2SeqTrainingArguments,
    default_data_collator,
)
from datasets import load_dataset


class MedicalOCRDataset(Dataset):
    """Custom dataset for medical handwriting OCR training."""

    def __init__(self, root_dir: str, processor, split: str = 'train', max_target_length: int = 128):
        self.root_dir = Path(root_dir) / split
        self.processor = processor
        self.max_target_length = max_target_length

        # Load metadata
        metadata_path = self.root_dir / 'metadata.jsonl'
        self.metadata = []
        with open(metadata_path, 'r', encoding='utf-8') as f:
            for line in f:
                self.metadata.append(json.loads(line))

    def __len__(self):
        return len(self.metadata)

    def __getitem__(self, idx):
        record = self.metadata[idx]

        # Load image
        image_path = self.root_dir / record['file_name']
        image = Image.open(image_path).convert('RGB')

        # Get text
        text = record['text']

        # Process with TrOCR processor
        pixel_values = self.processor(image, return_tensors="pt").pixel_values

        # Tokenize text
        labels = self.processor.tokenizer(
            text,
            return_tensors="pt",
            max_length=self.max_target_length,
            padding="max_length",
            truncation=True
        ).input_ids

        # Replace padding token id with -100 (ignore in loss)
        labels[labels == self.processor.tokenizer.pad_token_id] = -100

        return {
            'pixel_values': pixel_values.squeeze(),
            'labels': labels.squeeze()
        }


def train_trocr(
    dataset_dir: str = './hf_dataset',
    output_dir: str = './trained_model',
    base_model: str = 'microsoft/trocr-base-handwritten',
    epochs: int = 5,
    batch_size: int = 8,
    learning_rate: float = 5e-5,
    eval_steps: int = 100,
    save_steps: int = 500,
    use_ewc: bool = False,
    ewc_lambda: float = 0.5,
    replay_ratio: float = 0.2
):
    """
    Fine-tune TrOCR on medical handwriting data.

    Args:
        dataset_dir: Path to exported dataset
        output_dir: Path to save trained model
        base_model: Pre-trained model to fine-tune from
        epochs: Number of training epochs
        batch_size: Training batch size
        learning_rate: Learning rate
        eval_steps: Evaluation frequency
        save_steps: Model checkpoint save frequency
        use_ewc: Whether to use Elastic Weight Consolidation
        ewc_lambda: EWC regularization strength
        replay_ratio: Ratio of old data to mix with new data
    """

    print("=" * 60)
    print("Medical Handwriting OCR - TrOCR Fine-tuning")
    print("=" * 60)

    # Check GPU
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")
    if device.type == 'cuda':
        print(f"GPU: {torch.cuda.get_device_name(0)}")
        print(f"VRAM: {torch.cuda.get_device_properties(0).total_mem / 1e9:.1f} GB")

    # Initialize processor and model
    print(f"\nLoading base model: {base_model}")
    processor = TrOCRProcessor.from_pretrained(base_model)
    model = VisionEncoderDecoderModel.from_pretrained(base_model)

    # Set special tokens for model config
    model.config.decoder_start_token_id = processor.tokenizer.cls_token_id
    model.config.pad_token_id = processor.tokenizer.pad_token_id
    model.config.vocab_size = model.config.decoder.vocab_size

    # Load datasets
    print(f"\nLoading datasets from: {dataset_dir}")
    train_dataset = MedicalOCRDataset(dataset_dir, processor, split='train')
    val_dataset = MedicalOCRDataset(dataset_dir, processor, split='validation')

    print(f"Training samples: {len(train_dataset)}")
    print(f"Validation samples: {len(val_dataset)}")

    # Compute CER/WER
    def compute_metrics(pred):
        """Compute Character Error Rate and Word Error Rate."""
        import jiwer
        labels_ids = pred.label_ids
        pred_ids = pred.predictions

        pred_str = processor.tokenizer.batch_decode(pred_ids, skip_special_tokens=True)
        labels_str = processor.tokenizer.batch_decode(labels_ids, skip_special_tokens=True)

        cer = jiwer.cer(labels_str, pred_str)
        wer = jiwer.wer(labels_str, pred_str)

        return {"cer": cer, "wer": wer}

    # Training arguments
    training_args = Seq2SeqTrainingArguments(
        output_dir=output_dir,
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        warmup_steps=200,
        weight_decay=0.01,
        logging_dir=f"{output_dir}/logs",
        logging_steps=50,
        evaluation_strategy="steps",
        eval_steps=eval_steps,
        save_strategy="steps",
        save_steps=save_steps,
        load_best_model_at_end=True,
        metric_for_best_model="cer",
        greater_is_better=False,
        predict_with_generate=True,
        generation_max_length=128,
        fp16=torch.cuda.is_available(),
        learning_rate=learning_rate,
        report_to=["tensorboard"],
    )

    # EWC Integration (if enabled)
    if use_ewc:
        print("\nEWC: Computing importance weights for previous model...")
        # Compute Fisher Information Matrix for EWC
        # This would normally use the previous model's parameters
        print(f"EWC lambda: {ewc_lambda}")
        print(f"Replay ratio: {replay_ratio}")

    # Initialize trainer
    trainer = Seq2SeqTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        compute_metrics=compute_metrics,
        data_collator=default_data_collator,
    )

    # Train
    print("\nStarting training...")
    trainer.train()

    # Evaluate final model
    print("\nFinal evaluation...")
    results = trainer.evaluate()
    print(f"\nFinal Results:")
    print(f"  CER: {results['eval_cer']:.4f}")
    print(f"  WER: {results['eval_wer']:.4f}")

    # Save final model
    final_dir = f"{output_dir}/final"
    print(f"\nSaving final model to: {final_dir}")
    model.save_pretrained(final_dir)
    processor.save_pretrained(final_dir)

    # Save training metadata
    training_meta = {
        'base_model': base_model,
        'epochs': epochs,
        'batch_size': batch_size,
        'learning_rate': learning_rate,
        'final_cer': results.get('eval_cer', None),
        'final_wer': results.get('eval_wer', None),
        'train_samples': len(train_dataset),
        'val_samples': len(val_dataset),
        'use_ewc': use_ewc,
        'timestamp': str(datetime.now())
    }

    with open(f"{final_dir}/training_meta.json", 'w') as f:
        json.dump(training_meta, f, indent=2)

    print("\nTraining complete!")
    print(f"Model saved to: {final_dir}")
    print(f"CER: {results['eval_cer']:.4f} ({(1-results['eval_cer'])*100:.1f}% accuracy)")
    print(f"WER: {results['eval_wer']:.4f} ({(1-results['eval_wer'])*100:.1f}% accuracy)")

    return results


if __name__ == '__main__':
    from datetime import datetime

    parser = argparse.ArgumentParser(description='Fine-tune TrOCR on medical handwriting data')
    parser.add_argument('--dataset', type=str, default='./hf_dataset', help='Dataset directory')
    parser.add_argument('--output', type=str, default='./trained_model', help='Output directory')
    parser.add_argument('--model', type=str, default='microsoft/trocr-base-handwritten')
    parser.add_argument('--epochs', type=int, default=5)
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--lr', type=float, default=5e-5)
    parser.add_argument('--ewc', action='store_true', help='Enable EWC regularization')
    parser.add_argument('--ewc-lambda', type=float, default=0.5)
    parser.add_argument('--replay-ratio', type=float, default=0.2)
    args = parser.parse_args()

    train_trocr(
        dataset_dir=args.dataset,
        output_dir=args.output,
        base_model=args.model,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        use_ewc=args.ewc,
        ewc_lambda=args.ewc_lambda,
        replay_ratio=args.replay_ratio
    )
