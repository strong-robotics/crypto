#!/usr/bin/env python3
"""Train TCN model for crypto price forecasting.

Replaces CatBoost with TCN for better time series prediction.
"""

import asyncio
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader
import numpy as np
from typing import Tuple, Dict, List
import os
import json
import sys
from datetime import datetime

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ai.models.tcn import TCNPriceForecaster, create_tcn_model
from ai.datasets import load_tcn_data, create_tcn_dataloader
from ai.models.registry import register_model
from ai.config import ENCODER_SEC, HORIZONS, TARGET_RETURN, MODELS_DIR


class TCNTrainer:
    """TCN model trainer."""
    
    def __init__(self, 
                 device: torch.device = None,
                 learning_rate: float = 0.001,
                 batch_size: int = 32,
                 num_epochs: int = 100,
                 patience: int = 10):
        self.device = device or torch.device('mps' if torch.backends.mps.is_available() else 'cpu')
        self.learning_rate = learning_rate
        self.batch_size = batch_size
        self.num_epochs = num_epochs
        self.patience = patience
        
        self.model = None
        self.optimizer = None
        self.scheduler = None
        self.criterion = {
            'price': nn.MSELoss(),
            'pump': nn.BCELoss(),
            'return': nn.MSELoss()
        }
        
    def setup_model(self, input_size: int = 4, sequence_length: int = 15, 
                   output_horizon: int = 300, static_features: int = 3):
        """Setup TCN model and optimizer."""
        self.model = create_tcn_model(
            input_size=input_size,
            sequence_length=sequence_length,
            output_horizon=output_horizon,
            static_features=static_features,
            device=self.device
        )
        
        self.optimizer = optim.Adam(self.model.parameters(), lr=self.learning_rate)
        self.scheduler = optim.lr_scheduler.ReduceLROnPlateau(
            self.optimizer, mode='min', factor=0.5, patience=5
        )
        
        print(f"✅ TCN model setup on {self.device}")
        print(f"   Parameters: {sum(p.numel() for p in self.model.parameters()):,}")
        
    def train_epoch(self, dataloader: DataLoader) -> Dict[str, float]:
        """Train one epoch."""
        self.model.train()
        total_loss = 0.0
        price_loss = 0.0
        pump_loss = 0.0
        return_loss = 0.0
        num_batches = 0
        
        for batch in dataloader:
            # Move to device
            time_series = batch['time_series'].to(self.device)
            static = batch['static'].to(self.device)
            price_target = batch['price_target'].to(self.device)
            pump_target = batch['pump_target'].to(self.device)
            return_target = batch['return_target'].to(self.device)
            
            # Forward pass
            self.optimizer.zero_grad()
            price_pred, pump_pred, return_pred = self.model(time_series, static)
            
            # Calculate losses
            loss_price = self.criterion['price'](price_pred, price_target)
            loss_pump = self.criterion['pump'](pump_pred, pump_target)
            loss_return = self.criterion['return'](return_pred, return_target)
            
            # Combined loss
            total_loss_batch = loss_price + loss_pump + loss_return
            
            # Backward pass
            total_loss_batch.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
            self.optimizer.step()
            
            # Accumulate losses
            total_loss += total_loss_batch.item()
            price_loss += loss_price.item()
            pump_loss += loss_pump.item()
            return_loss += loss_return.item()
            num_batches += 1
        
        return {
            'total_loss': total_loss / num_batches,
            'price_loss': price_loss / num_batches,
            'pump_loss': pump_loss / num_batches,
            'return_loss': return_loss / num_batches
        }
    
    def validate(self, dataloader: DataLoader) -> Dict[str, float]:
        """Validate model."""
        self.model.eval()
        total_loss = 0.0
        price_loss = 0.0
        pump_loss = 0.0
        return_loss = 0.0
        num_batches = 0
        
        with torch.no_grad():
            for batch in dataloader:
                # Move to device
                time_series = batch['time_series'].to(self.device)
                static = batch['static'].to(self.device)
                price_target = batch['price_target'].to(self.device)
                pump_target = batch['pump_target'].to(self.device)
                return_target = batch['return_target'].to(self.device)
                
                # Forward pass
                price_pred, pump_pred, return_pred = self.model(time_series, static)
                
                # Calculate losses
                loss_price = self.criterion['price'](price_pred, price_target)
                loss_pump = self.criterion['pump'](pump_pred, pump_target)
                loss_return = self.criterion['return'](return_pred, return_target)
                
                # Combined loss
                total_loss_batch = loss_price + loss_pump + loss_return
                
                # Accumulate losses
                total_loss += total_loss_batch.item()
                price_loss += loss_price.item()
                pump_loss += loss_pump.item()
                return_loss += loss_return.item()
                num_batches += 1
        
        return {
            'total_loss': total_loss / num_batches,
            'price_loss': price_loss / num_batches,
            'pump_loss': pump_loss / num_batches,
            'return_loss': return_loss / num_batches
        }
    
    def train(self, train_loader: DataLoader, val_loader: DataLoader) -> Dict[str, List[float]]:
        """Train TCN model."""
        print(f"🚀 Starting TCN training for {self.num_epochs} epochs...")
        
        train_losses = []
        val_losses = []
        best_val_loss = float('inf')
        patience_counter = 0
        
        for epoch in range(self.num_epochs):
            # Train
            train_metrics = self.train_epoch(train_loader)
            train_losses.append(train_metrics['total_loss'])
            
            # Validate
            val_metrics = self.validate(val_loader)
            val_losses.append(val_metrics['total_loss'])
            
            # Learning rate scheduling
            self.scheduler.step(val_metrics['total_loss'])
            
            # Early stopping
            if val_metrics['total_loss'] < best_val_loss:
                best_val_loss = val_metrics['total_loss']
                patience_counter = 0
                # Save best model
                self.save_model(f"tcn_best_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pth")
            else:
                patience_counter += 1
            
            # Print progress
            if epoch % 10 == 0 or epoch == self.num_epochs - 1:
                print(f"Epoch {epoch:3d}/{self.num_epochs}: "
                      f"Train Loss: {train_metrics['total_loss']:.6f}, "
                      f"Val Loss: {val_metrics['total_loss']:.6f}, "
                      f"LR: {self.optimizer.param_groups[0]['lr']:.2e}")
            
            # Early stopping
            if patience_counter >= self.patience:
                print(f"🛑 Early stopping at epoch {epoch}")
                break
        
        return {
            'train_losses': train_losses,
            'val_losses': val_losses,
            'best_val_loss': best_val_loss
        }
    
    def save_model(self, filename: str):
        """Save model to file."""
        os.makedirs(MODELS_DIR, exist_ok=True)
        filepath = os.path.join(MODELS_DIR, filename)
        
        torch.save({
            'model_state_dict': self.model.state_dict(),
            'optimizer_state_dict': self.optimizer.state_dict(),
            'scheduler_state_dict': self.scheduler.state_dict(),
            'model_config': {
                'input_size': getattr(self.model, 'input_size', None) or 4,
                'sequence_length': getattr(self.model, 'sequence_length', None) or 15,
                'output_horizon': getattr(self.model, 'output_horizon', None) or 300,
                'static_features': getattr(self.model, 'static_features', None) or 3
            }
        }, filepath)
        
        print(f"💾 Model saved to {filepath}")
        return filepath


async def train_tcn_model():
    """Main training function."""
    print("🧠 Training TCN model for crypto price forecasting...")
    
    # Setup device
    device = torch.device('mps' if torch.backends.mps.is_available() else 'cpu')
    print(f"🖥️  Using device: {device}")
    
    # Load data
    print("📊 Loading training data...")
    exclude_tokens = ["mqrtbGegiCbUpbiH2RibPwv5EPDoKzUzASrkMKuxHD7"]  # Broken token
    
    horizon = int(HORIZONS[-1]) if HORIZONS else 300
    sequences, static, prices, pumps, returns = await load_tcn_data(
        encoder_sec=int(ENCODER_SEC),
        horizon_sec=horizon,
        target_return=float(TARGET_RETURN),
        exclude_tokens=exclude_tokens
    )
    
    if len(sequences) == 0:
        print("❌ No training data available")
        return
    
    print(f"✅ Loaded {len(sequences)} training samples")
    
    # Split data
    split_idx = int(0.8 * len(sequences))
    train_sequences = sequences[:split_idx]
    train_static = static[:split_idx]
    train_prices = prices[:split_idx]
    train_pumps = pumps[:split_idx]
    train_returns = returns[:split_idx]
    
    val_sequences = sequences[split_idx:]
    val_static = static[split_idx:]
    val_prices = prices[split_idx:]
    val_pumps = pumps[split_idx:]
    val_returns = returns[split_idx:]
    
    print(f"📈 Train samples: {len(train_sequences)}")
    print(f"📊 Val samples: {len(val_sequences)}")
    
    # Create data loaders
    train_loader = create_tcn_dataloader(
        train_sequences, train_static, train_prices, train_pumps, train_returns,
        batch_size=32, shuffle=True
    )
    
    val_loader = create_tcn_dataloader(
        val_sequences, val_static, val_prices, val_pumps, val_returns,
        batch_size=32, shuffle=False
    )
    
    # Setup trainer
    trainer = TCNTrainer(
        device=device,
        learning_rate=0.001,
        batch_size=32,
        num_epochs=100,
        patience=10
    )
    
    # Infer shapes from data
    inferred_input_size = int(train_sequences.shape[1])
    inferred_seq_len = int(train_sequences.shape[2])

    inferred_static_size = int(train_static.shape[1])

    trainer.setup_model(
        input_size=inferred_input_size,
        sequence_length=inferred_seq_len,
        output_horizon=horizon,
        static_features=inferred_static_size
    )
    
    # Train model
    results = trainer.train(train_loader, val_loader)
    
    # Register model
    model_files = [
        os.path.join(MODELS_DIR, f)
        for f in os.listdir(MODELS_DIR)
        if f.startswith("tcn_best_") and f.endswith(".pth")
    ]
    if model_files:
        # Pick the newest by modification time
        model_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        model_path = model_files[0]
        model_id = await register_model(
            name="TCN Price Forecaster",
            version="1.0",
            model_type="tcn",
            framework="pytorch",
            hyperparams={
                "input_size": inferred_input_size,
                "sequence_length": inferred_seq_len,
                "output_horizon": 300,
                "static_features": inferred_static_size,
                "device": str(device)
            },
            train_window_sec=inferred_seq_len,
            predict_horizons_sec=[horizon],
            path=model_path,
            metrics={"best_val_loss": results['best_val_loss']}
        )
    else:
        model_id = 0
    
    print(f"🎉 TCN training completed!")
    print(f"   Best validation loss: {results['best_val_loss']:.6f}")
    print(f"   Model ID: {model_id}")
    
    return results


if __name__ == "__main__":
    asyncio.run(train_tcn_model())
