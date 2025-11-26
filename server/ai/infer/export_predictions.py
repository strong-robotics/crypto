"""
Export ETA model predictions to CSV for backtest/analysis.

Inputs:
  - Parquet dataset from datasets/build_eta_dataset.py
  - Trained model: models/eta_tcn.pt

Output:
  - CSV with columns: token_id, entry_ts, p_hit, eta_bin, y_hit, eta_seconds
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader


def parse_args():
    p = argparse.ArgumentParser(description="Export ETA predictions to CSV")
    p.add_argument("--data", default=os.path.join("data", "eta_dataset.parquet"))
    p.add_argument("--model", default=os.path.join("models", "eta_tcn.pt"))
    p.add_argument("--out", default="preds.csv")
    p.add_argument("--batch", type=int, default=256)
    p.add_argument("--limit", type=int, default=0, help="limit number of samples (0 = all)")
    return p.parse_args()


class ETADataset(Dataset):
    def __init__(self, df: pd.DataFrame):
        self.df = df.reset_index(drop=True)
        self.channels = [
            "price",
            "liquidity",
            "mcap",
            "holders",
            "buy_count",
            "sell_count",
        ]

    def __len__(self) -> int:
        return len(self.df)

    def __getitem__(self, idx: int):
        row = self.df.iloc[idx]
        feats: Dict[str, Any] = row["features"]
        labs: Dict[str, Any] = row.get("labels", {})

        series = np.stack([np.asarray(feats[k], dtype=float) for k in self.channels], axis=0)
        x = torch.tensor(series, dtype=torch.float32)

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

        token_id = int(row["token_id"]) if "token_id" in row else -1
        entry_ts = int(row["entry_ts"]) if "entry_ts" in row else -1
        y_hit = int(labs.get("y_hit", -1)) if isinstance(labs, dict) else -1
        eta_seconds = int(labs.get("eta_seconds", -1)) if isinstance(labs, dict) and labs.get("eta_seconds") is not None else -1

        return {"x": x, "cond": cond_t, "token_id": token_id, "entry_ts": entry_ts, "y_hit": y_hit, "eta_seconds": eta_seconds}


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
        out = out[:, :, : x.size(2)]
        return self.dropout(out + self.down(x))


class SmallTCN(nn.Module):
    def __init__(self, in_ch: int, cond_dim: int, hid: int = 64, k: int = 3, num_bins: int = 6):
        super().__init__()
        self.b1 = TemporalBlock(in_ch, hid, k=k, d=1)
        self.b2 = TemporalBlock(hid, hid, k=k, d=2)
        self.b3 = TemporalBlock(hid, hid, k=k, d=4)
        self.head_p = nn.Sequential(nn.Linear(hid + cond_dim, 64), nn.ReLU(), nn.Linear(64, 1))
        self.head_eta = nn.Sequential(nn.Linear(hid + cond_dim, 64), nn.ReLU(), nn.Linear(64, num_bins))

    def forward(self, x: torch.Tensor, cond: torch.Tensor):
        h = self.b1(x)
        h = self.b2(h)
        h = self.b3(h)
        h = h[:, :, -1]
        z = torch.cat([h, cond], dim=1)
        logit_p = self.head_p(z)
        logit_eta = self.head_eta(z)
        return logit_p, logit_eta


def main():
    args = parse_args()
    df = pd.read_parquet(args.data)
    if args.limit and len(df) > args.limit:
        df = df.iloc[: args.limit].copy()

    ds = ETADataset(df)
    dl = DataLoader(ds, batch_size=args.batch, shuffle=False)

    chk = torch.load(args.model, map_location="cpu")
    eta_bins = chk.get("bins", [30, 40, 60, 90, 120, 180, 240])
    model = SmallTCN(in_ch=6, cond_dim=9, num_bins=len(eta_bins))
    model.load_state_dict(chk["model"])
    model.eval()

    out_rows: List[Dict[str, Any]] = []
    with torch.no_grad():
        for batch in dl:
            x = batch["x"]
            cond = batch["cond"]
            logit_p, logit_eta = model(x, cond)
            p_hit = torch.sigmoid(logit_p).squeeze(1).cpu().numpy()
            eta_idx = torch.argmax(logit_eta, dim=1).cpu().numpy()
            eta_bin = [int(eta_bins[i]) for i in eta_idx]

            for i in range(x.size(0)):
                out_rows.append(
                    {
                        "token_id": int(batch["token_id"][i]),
                        "entry_ts": int(batch["entry_ts"][i]),
                        "p_hit": float(p_hit[i]),
                        "eta_bin": int(eta_bin[i]),
                        "y_hit": int(batch["y_hit"][i]),
                        "eta_seconds": int(batch["eta_seconds"][i]),
                    }
                )

    pd.DataFrame(out_rows).to_csv(args.out, index=False)
    print(f"✅ Wrote predictions to {args.out} ({len(out_rows)} rows)")


if __name__ == "__main__":
    main()

