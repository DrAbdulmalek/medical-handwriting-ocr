"""
Continual Trainer for incremental TrOCR fine-tuning.
Implements Elastic Weight Consolidation (EWC) + Replay Buffer
to enable continuous model improvement without catastrophic forgetting.
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

import torch
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from PIL import Image
from transformers import TrOCRProcessor, VisionEncoderDecoderModel
from datasets import load_dataset
import numpy as np

from training.replay_buffer import ReplayBuffer

logger = logging.getLogger(__name__)


class EWCRegularizer:
    """
    Elastic Weight Consolidation (EWC) implementation.
    
    Computes Fisher Information Matrix to identify important model parameters
    and penalizes changes to these parameters during fine-tuning, preventing
    catastrophic forgetting of previously learned patterns.
    """

    def __init__(self, model: VisionEncoderDecoderModel, lambda_ewc: float = 0.5):
        """
        Args:
            model: The TrOCR model to regularize.
            lambda_ewc: Strength of EWC regularization penalty.
                        Higher = more conservative (less forgetting, slower learning).
        """
        self.model = model
        self.lambda_ewc = lambda_ewc
        self.fisher_information = {}
        self.optimal_params = {}

    def compute_fisher(self, dataloader: DataLoader, num_samples: int = 200) -> None:
        """
        Compute Fisher Information Matrix using a sample of training data.
        Higher Fisher values = more important parameters for current task.
        """
        logger.info(f"Computing Fisher Information on {num_samples} samples...")
        
        self.model.eval()
        fisher = {n: torch.zeros_like(p) for n, p in self.model.named_parameters() 
                  if p.requires_grad}
        
        samples_computed = 0
        
        for batch in dataloader:
            if samples_computed >= num_samples:
                break
            
            pixel_values = batch["pixel_values"].to(self.model.device)
            labels = batch["labels"].to(self.model.device)
            
            # Forward pass
            outputs = self.model(pixel_values=pixel_values, labels=labels)
            loss = outputs.loss
            
            # Compute gradients (log-likelihood gradients for Fisher)
            self.model.zero_grad()
            loss.backward()
            
            # Accumulate squared gradients (Fisher approximation)
            for n, p in self.model.named_parameters():
                if p.requires_grad and p.grad is not None:
                    fisher[n] += p.grad.data ** 2
            
            samples_computed += len(pixel_values)
        
        # Average Fisher information
        for n in fisher:
            fisher[n] /= min(samples_computed, num_samples)
        
        self.fisher_information = fisher
        
        # Store current optimal parameters
        self.optimal_params = {
            n: p.data.clone() for n, p in self.model.named_parameters()
            if p.requires_grad
        }
        
        logger.info(f"Fisher Information computed for {len(fisher)} parameters")

    def ewc_loss(self) -> torch.Tensor:
        """
        Compute EWC penalty term.
        Penalizes deviation from optimal parameters weighted by Fisher importance.
        """
        if not self.fisher_information or not self.optimal_params:
            return torch.tensor(0.0)
        
        loss = torch.tensor(0.0).to(self.model.device)
        
        for n, p in self.model.named_parameters():
            if n in self.fisher_information and n in self.optimal_params:
                # L2 penalty weighted by Fisher importance
                fisher = self.fisher_information[n].to(self.model.device)
                optimal = self.optimal_params[n].to(self.model.device)
                loss += (fisher * (p - optimal) ** 2).sum()
        
        return self.lambda_ewc * loss

    def save(self, path: str) -> None:
        """Save Fisher information and optimal parameters."""
        state = {
            "lambda_ewc": self.lambda_ewc,
            "fisher": {n: t.cpu() for n, t in self.fisher_information.items()},
            "optimal": {n: t.cpu() for n, t in self.optimal_params.items()},
        }
        torch.save(state, path)
        logger.info(f"EWC state saved to {path}")

    def load(self, path: str) -> bool:
        """Load Fisher information and optimal parameters."""
        if not os.path.exists(path):
            return False
        try:
            state = torch.load(path, map_location="cpu")
            self.lambda_ewc = state.get("lambda_ewc", self.lambda_ewc)
            self.fisher_information = {n: t.to(self.model.device) for n, t in state.get("fisher", {}).items()}
            self.optimal_params = {n: t.to(self.model.device) for n, t in state.get("optimal", {}).items()}
            logger.info(f"EWC state loaded from {path} ({len(self.fisher_information)} params)")
            return True
        except Exception as e:
            logger.error(f"Failed to load EWC state: {e}")
            return False


class ContinualTrainer:
    """
    Manages continual/incremental training of TrOCR model.
    
    Combines:
    - Elastic Weight Consolidation (EWC) to prevent forgetting
    - Replay Buffer to maintain representative historical data
    - Automated evaluation and deployment decisions
    """

    def __init__(
        self,
        model_path: str = "./trained_model",
        dataset_dir: str = "./hf_dataset",
        replay_buffer_path: str = "./replay_buffer.json",
        replay_capacity: int = 2000,
        replay_ratio: float = 0.2,
        ewc_lambda: float = 0.5,
        device: Optional[str] = None,
    ):
        self.model_path = Path(model_path)
        self.dataset_dir = Path(dataset_dir)
        self.replay_ratio = replay_ratio
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        
        # Initialize replay buffer
        self.replay_buffer = ReplayBuffer(
            capacity=replay_capacity,
            persist_path=replay_buffer_path,
        )
        self.replay_buffer.load()
        
        # Training state
        self.trained_on_count = 0
        self.training_history = []
        self._load_training_history()

    def _load_training_history(self):
        """Load previous training history."""
        history_path = self.model_path / "training_history.json"
        if history_path.exists():
            with open(history_path, "r") as f:
                self.training_history = json.load(f)
            if self.training_history:
                self.trained_on_count = self.training_history[-1].get("cumulative_samples", 0)

    def _save_training_history(self):
        """Save training history."""
        self.model_path.mkdir(parents=True, exist_ok=True)
        history_path = self.model_path / "training_history.json"
        with open(history_path, "w") as f:
            json.dump(self.training_history, f, indent=2)

    def prepare_training_data(
        self,
        new_samples: List[Dict],
        test_split: bool = True,
        test_ratio: float = 0.15,
    ) -> tuple:
        """
        Prepare training data by merging new samples with replay buffer.
        
        Args:
            new_samples: Fresh corrections from users.
            test_split: Whether to hold out a test set.
            test_ratio: Fraction to use for testing.
        
        Returns:
            (train_samples, test_samples, replay_samples_count)
        """
        import random
        
        # Merge with replay buffer
        combined = self.replay_buffer.merge_with_new(new_samples, self.replay_ratio)
        
        # Split test set
        if test_split and len(combined) > 20:
            random.shuffle(combined)
            test_size = max(1, int(len(combined) * test_ratio))
            test_samples = combined[:test_size]
            train_samples = combined[test_size:]
        else:
            train_samples = combined
            test_samples = []
        
        replay_count = len(combined) - len(new_samples)
        
        logger.info(
            f"Training data prepared: {len(train_samples)} train, "
            f"{len(test_samples)} test, {replay_count} from replay buffer"
        )
        
        return train_samples, test_samples, replay_count

    def train_step(
        self,
        new_samples: List[Dict],
        epochs: int = 3,
        batch_size: int = 8,
        learning_rate: float = 3e-5,
        use_ewc: bool = True,
        min_improvement: float = 0.005,
    ) -> Dict:
        """
        Execute one training step with EWC + Replay Buffer.
        
        Args:
            new_samples: New correction samples.
            epochs: Number of training epochs.
            batch_size: Batch size.
            learning_rate: Learning rate for optimizer.
            use_ewc: Whether to apply EWC regularization.
            min_improvement: Minimum CER improvement to accept new model.
        
        Returns:
            Training results dict with metrics and deployment decision.
        """
        from training.finetune_trocr import MedicalOCRDataset, compute_metrics_fn
        
        # Prepare data
        train_samples, test_samples, replay_count = self.prepare_training_data(new_samples)
        
        if len(train_samples) < 10:
            logger.warning("Not enough training samples. Skipping training.")
            return {"status": "skipped", "reason": "insufficient_samples"}
        
        # Load or initialize model
        model_dir = self.model_path / "final"
        if model_dir.exists():
            logger.info(f"Loading existing model from {model_dir}")
            processor = TrOCRProcessor.from_pretrained(str(model_dir))
            model = VisionEncoderDecoderModel.from_pretrained(str(model_dir))
        else:
            logger.info("Initializing new model from base")
            base_model = "microsoft/trocr-base-handwritten"
            processor = TrOCRProcessor.from_pretrained(base_model)
            model = VisionEncoderDecoderModel.from_pretrained(base_model)
        
        model.to(self.device)
        
        # Compute EWC on current model (before training)
        ewc = EWCRegularizer(model, lambda_ewc=0.5)
        if use_ewc and self.trained_on_count > 0:
            # Create temporary dataset for Fisher computation
            temp_dataset = MedicalOCRDataset(
                str(self.dataset_dir), processor, split="validation"
            )
            temp_loader = DataLoader(temp_dataset, batch_size=batch_size, shuffle=True)
            ewc.compute_fisher(temp_loader)
            logger.info("EWC computed from previous model knowledge")
        
        # Optimizer
        optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
        
        # Training loop
        model.train()
        total_loss = 0
        steps = 0
        
        for epoch in range(epochs):
            epoch_loss = 0
            epoch_steps = 0
            
            for i in range(0, len(train_samples), batch_size):
                batch_samples = train_samples[i:i + batch_size]
                
                # Process batch
                pixel_values_list = []
                labels_list = []
                
                for sample in batch_samples:
                    img_path = Path(self.dataset_dir) / sample.get("file_name", "")
                    if img_path.exists():
                        image = Image.open(img_path).convert("RGB")
                        pv = processor(image, return_tensors="pt").pixel_values
                        pixel_values_list.append(pv)
                        
                        text = sample["text"]
                        label_ids = processor.tokenizer(
                            text, return_tensors="pt", max_length=128,
                            padding="max_length", truncation=True
                        ).input_ids
                        label_ids[label_ids == processor.tokenizer.pad_token_id] = -100
                        labels_list.append(label_ids)
                
                if not pixel_values_list:
                    continue
                
                pixel_values = torch.cat(pixel_values_list).to(self.device)
                labels = torch.cat(labels_list).to(self.device)
                
                # Forward pass
                outputs = model(pixel_values=pixel_values, labels=labels)
                loss = outputs.loss
                
                # Add EWC penalty
                if use_ewc:
                    ewc_penalty = ewc.ewc_loss()
                    loss = loss + ewc_penalty
                
                # Backward pass
                optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()
                
                epoch_loss += loss.item()
                epoch_steps += 1
                total_loss += loss.item()
                steps += 1
            
            avg_loss = epoch_loss / max(epoch_steps, 1)
            logger.info(f"Epoch {epoch + 1}/{epochs} - Loss: {avg_loss:.4f}")
        
        avg_total_loss = total_loss / max(steps, 1)
        
        # Evaluate
        model.eval()
        metrics = {"cer": 1.0, "wer": 1.0}  # Default (worst case)
        if test_samples:
            try:
                import jiwer
                predictions = []
                ground_truths = []
                
                for sample in test_samples:
                    img_path = Path(self.dataset_dir) / sample.get("file_name", "")
                    if img_path.exists():
                        image = Image.open(img_path).convert("RGB")
                        pv = processor(image, return_tensors="pt").pixel_values.to(self.device)
                        with torch.no_grad():
                            generated = model.generate(pv)
                        pred = processor.batch_decode(generated, skip_special_tokens=True)[0]
                        predictions.append(pred)
                        ground_truths.append(sample["text"])
                
                if predictions:
                    metrics["cer"] = jiwer.cer(ground_truths, predictions)
                    metrics["wer"] = jiwer.wer(ground_truths, predictions)
            except Exception as e:
                logger.error(f"Evaluation failed: {e}")
        
        # Determine if model should be deployed
        previous_cer = 1.0
        if self.training_history:
            previous_cer = self.training_history[-1].get("cer", 1.0)
        
        improved = (previous_cer - metrics["cer"]) >= min_improvement
        should_deploy = improved and metrics["cer"] < previous_cer
        
        # Record training history
        self.trained_on_count += len(new_samples)
        record = {
            "timestamp": datetime.now().isoformat(),
            "new_samples": len(new_samples),
            "replay_samples": replay_count,
            "total_training_samples": len(train_samples),
            "epochs": epochs,
            "avg_loss": round(avg_total_loss, 4),
            "cer": round(metrics["cer"], 4),
            "wer": round(metrics["wer"], 4),
            "cumulative_samples": self.trained_on_count,
            "ewc_enabled": use_ewc,
            "deployed": should_deploy,
        }
        self.training_history.append(record)
        self._save_training_history()
        
        # Save EWC state for next iteration
        if use_ewc and should_deploy:
            ewc_path = self.model_path / "ewc_state.pt"
            model.to("cpu")
            ewc.save(str(ewc_path))
            logger.info(f"EWC state saved for next training cycle")
        
        # Save new model if improved
        if should_deploy:
            model.to("cpu")
            model.save_pretrained(str(self.model_path / "final"))
            processor.save_pretrained(str(self.model_path / "final"))
            logger.info(f"New model deployed! CER: {previous_cer:.4f} -> {metrics['cer']:.4f}")
        else:
            logger.info(
                f"Model NOT deployed. CER: {previous_cer:.4f} -> {metrics['cer']:.4f} "
                f"(improvement: {previous_cer - metrics['cer']:.4f} < threshold {min_improvement})"
            )
        
        # Save replay buffer
        self.replay_buffer.save()
        
        return {
            "status": "deployed" if should_deploy else "not_deployed",
            "metrics": metrics,
            "previous_cer": previous_cer,
            "improved": improved,
            "new_samples": len(new_samples),
            "replay_samples": replay_count,
            "cumulative_training": self.trained_on_count,
        }
