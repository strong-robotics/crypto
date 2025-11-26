"""
Train multi-head TCN for early-entry classification (p_hit) and ETA-bin.

Inputs: data/eta_dataset.parquet produced by datasets/build_eta_dataset.py
Model: lightweight TCN backbone + two heads
  - Head A: p_hit (BCEWithLogitsLoss)
  - Head C: ETA_bin (CrossEntropyLoss over bins)

This is a minimal v0 training skeleton with simple token-level split.
"""

from __future__ import annotations

import os
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader


ETA_BINS = [30, 40, 60, 90, 120, 180, 240]
MODEL_DIR = os.getenv("ETA_MODEL_DIR", "models")
DATA_PATH = os.getenv("ETA_DATA_PATH", os.path.join("data", "eta_dataset.parquet"))
EPOCHS = int(os.getenv("ETA_EPOCHS", 8))
BATCH_SIZE = int(os.getenv("ETA_BATCH", 64))
LR = float(os.getenv("ETA_LR", 3e-4))
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


class ETADataset(Dataset):
    def __init__(self, df: pd.DataFrame):
        self.df = df.reset_index(drop=True)

        # infer channels from features dict shape
        f0 = self.df.loc[0, "features"]
        # channels: price, liquidity, mcap, holders, buy_count, sell_count = 6 channels
        # we store them as (C, T)
        self.channels = [
            "price",
            "liquidity",
            "mcap",
            "holders",
            "buy_count",
            "sell_count",
        ]
        self.window = int(self.df.loc[0, "window_sec"]) if "window_sec" in self.df.columns else 15

    def __len__(self) -> int:
        return len(self.df)

    def __getitem__(self, idx: int):
        row = self.df.iloc[idx]
        feats: Dict[str, Any] = row["features"]
        labs: Dict[str, Any] = row["labels"]
        stat: Dict[str, Any] = row["static"]

        series = np.stack([np.asarray(feats[k], dtype=float) for k in self.channels], axis=0)  # (C,T)
        x = torch.tensor(series, dtype=torch.float32)

        # Derived scalars appended as conditioning
        cond = np.array([
            float(feats.get("slope_5", 0.0)),
            float(feats.get("slope_10", 0.0)),
            float(feats.get("slope_15", 0.0)),
            float(feats.get("accel", 0.0)),
            float(feats.get("vol_dln", 0.0)),
            float(feats.get("r2_10", 0.0)),
            float(feats.get("r2_15", 0.0)),
            float(feats.get("run_up", 0.0)),
            float(feats.get("drawdown", 0.0)),
        ], dtype=np.float32)
        cond_t = torch.tensor(cond, dtype=torch.float32)

        y_hit = torch.tensor([float(labs.get("y_hit", 0))], dtype=torch.float32)
        eta_bin = labs.get("eta_bin")
        # if eta_bin is None, set to last class (no-hit proxy)
        if eta_bin is None:
            eta_cls = len(ETA_BINS) - 1
        else:
            # map bin value to index
            try:
                eta_cls = ETA_BINS.index(int(eta_bin))
            except ValueError:
                eta_cls = len(ETA_BINS) - 1
        eta_cls_t = torch.tensor(eta_cls, dtype=torch.long)

        return {
            "x": x,  # (C,T)
            "cond": cond_t,  # (9,)
            "y_hit": y_hit,  # (1,)
            "eta_cls": eta_cls_t,  # ()
        }


class TemporalBlock(nn.Module):
    def __init__(self, in_ch: int, out_ch: int, k: int, d: int, p: float = 0.1):
        super().__init__()
        pad = (k - 1) * d
        self.conv1 = nn.Conv1d(in_ch, out_ch, k, padding=pad, dilation=d)
        self.relu1 = nn.ReLU()
        self.conv2 = nn.Conv1d(out_ch, out_ch, k, padding=pad, dilation=d)
        self.relu2 = nn.ReLU()
        self.net = nn.Sequential(self.conv1, self.relu1, self.conv2, self.relu2)
        self.down = nn.Conv1d(in_ch, out_ch, 1) if in_ch != out_ch else nn.Identity()
        self.dropout = nn.Dropout(p)

    def forward(self, x):
        out = self.net(x)
        out = out[:, :, : x.size(2)]  # crop causal
        return self.dropout(out + self.down(x))


class SmallTCN(nn.Module):
    def __init__(self, in_ch: int, cond_dim: int, hid: int = 64, k: int = 3):
        super().__init__()
        self.b1 = TemporalBlock(in_ch, hid, k=k, d=1)
        self.b2 = TemporalBlock(hid, hid, k=k, d=2)
        self.b3 = TemporalBlock(hid, hid, k=k, d=4)
        self.head_p = nn.Sequential(
            nn.Linear(hid + cond_dim, 64), nn.ReLU(), nn.Linear(64, 1)
        )
        self.head_eta = nn.Sequential(
            nn.Linear(hid + cond_dim, 64), nn.ReLU(), nn.Linear(64, len(ETA_BINS))
        )

    def forward(self, x: torch.Tensor, cond: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        # x: (B,C,T)
        h = self.b1(x)
        h = self.b2(h)
        h = self.b3(h)
        h = h[:, :, -1]  # (B,hid)
        z = torch.cat([h, cond], dim=1)
        logit_p = self.head_p(z)
        logit_eta = self.head_eta(z)
        return logit_p, logit_eta


def split_by_token(df: pd.DataFrame, valid_frac: float = 0.2) -> Tuple[pd.DataFrame, pd.DataFrame]:
    tokens = df["token_id"].unique().tolist()
    np.random.shuffle(tokens)
    cut = int(len(tokens) * (1.0 - valid_frac))
    train_ids = set(tokens[:cut])
    is_train = df["token_id"].isin(train_ids)
    return df[is_train].copy(), df[~is_train].copy()


def train():
    os.makedirs(MODEL_DIR, exist_ok=True)
    df = pd.read_parquet(DATA_PATH)
    if df.empty:
        raise RuntimeError("Empty dataset")

    train_df, valid_df = split_by_token(df, valid_frac=0.2)
    tr_ds = ETADataset(train_df)
    va_ds = ETADataset(valid_df)

    tr = DataLoader(tr_ds, batch_size=BATCH_SIZE, shuffle=True)
    va = DataLoader(va_ds, batch_size=BATCH_SIZE, shuffle=False)

    in_ch = len(tr_ds.channels)
    cond_dim = 9
    model = SmallTCN(in_ch, cond_dim).to(DEVICE)

    bce = nn.BCEWithLogitsLoss()
    ce = nn.CrossEntropyLoss()
    opt = optim.Adam(model.parameters(), lr=LR)

    for epoch in range(1, EPOCHS + 1):
        model.train()
        loss_sum = 0.0
        n = 0
        for batch in tr:
            x = batch["x"].to(DEVICE)
            cond = batch["cond"].to(DEVICE)
            y_hit = batch["y_hit"].to(DEVICE)
            eta_cls = batch["eta_cls"].to(DEVICE)

            opt.zero_grad()
            logit_p, logit_eta = model(x, cond)
            l1 = bce(logit_p.squeeze(1), y_hit.squeeze(1))
            l2 = ce(logit_eta, eta_cls)
            loss = l1 + 0.5 * l2
            loss.backward()
            opt.step()
            loss_sum += float(loss.item()) * x.size(0)
            n += x.size(0)
        tr_loss = loss_sum / max(1, n)

        # Valid
        model.eval()
        with torch.no_grad():
            vl_sum = 0.0
            vn = 0
            phit_list: List[float] = []
            y_list: List[int] = []
            for batch in va:
                x = batch["x"].to(DEVICE)
                cond = batch["cond"].to(DEVICE)
                y_hit = batch["y_hit"].to(DEVICE)
                eta_cls = batch["eta_cls"].to(DEVICE)
                logit_p, logit_eta = model(x, cond)
                l1 = bce(logit_p.squeeze(1), y_hit.squeeze(1))
                l2 = ce(logit_eta, eta_cls)
                loss = l1 + 0.5 * l2
                vl_sum += float(loss.item()) * x.size(0)
                vn += x.size(0)
                phit = torch.sigmoid(logit_p).squeeze(1).cpu().numpy().tolist()
                phit_list.extend(phit)
                y_list.extend(y_hit.squeeze(1).cpu().numpy().astype(int).tolist())
            va_loss = vl_sum / max(1, vn)
            # simple calibration stats
            avg_p = float(np.mean(phit_list)) if phit_list else 0.0
            pos_rate = float(np.mean(y_list)) if y_list else 0.0

        print(f"Epoch {epoch:02d}  train_loss={tr_loss:.4f}  valid_loss={va_loss:.4f}  avg_p={avg_p:.3f}  pos_rate={pos_rate:.3f}")

    # Save
    path = os.path.join(MODEL_DIR, "eta_tcn.pt")
    torch.save({"model": model.state_dict(), "bins": ETA_BINS}, path)
    print(f"✅ Saved model to {path}")


if __name__ == "__main__":
    train()

