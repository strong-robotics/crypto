#!/usr/bin/env python3
"""
Train a simple ML classifier for three pattern segments (best/good/...).
Uses historical tokens with вручную размеченными сегментами.
Outputs models/pattern_segments.pkl with sklearn RandomForest.
"""

import asyncio
import json
from pathlib import Path
import sys
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from joblib import dump

ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "server"
for path in (ROOT, SERVER_DIR):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from server._v3_db_pool import get_db_pool
from server.ai.pattern_segments import (
    SEGMENT_BOUNDS,
    SEGMENT_FEATURE_KEYS,
    extract_series,
    feature_vector_for_segments,
)

MODEL_PATH = ROOT / "models" / "pattern_segments.pkl"
META_PATH = ROOT / "models" / "pattern_segments_meta.json"


def normalize_label(label: Optional[str]) -> Optional[str]:
    if not label:
        return None
    lbl = label.strip().lower()
    synonyms = {
        "super": "best",
        "middle": "middle",
        "mid": "middle",
    }
    return synonyms.get(lbl, lbl)


async def fetch_segment_dataset() -> List[Dict[str, float]]:
    pool = await get_db_pool()
    dataset: List[Dict[str, float]] = []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id,
                   pattern_segment_1,
                   pattern_segment_2,
                   pattern_segment_3
            FROM tokens_history
            WHERE pattern_segment_1 IS NOT NULL
              AND pattern_segment_2 IS NOT NULL
              AND pattern_segment_3 IS NOT NULL
            """
        )
        for row in rows:
            token_id = row["id"]
            metrics = await conn.fetch(
                """
                SELECT usd_price, buy_count, sell_count
                FROM token_metrics_seconds_history
                WHERE token_id=$1 AND usd_price IS NOT NULL
                ORDER BY ts ASC
                LIMIT 150
                """,
                token_id,
            )
            if not metrics:
                continue
            series = extract_series(metrics)
            segment_dicts = feature_vector_for_segments(series)
            labels = [
                normalize_label(row.get("pattern_segment_1")),
                normalize_label(row.get("pattern_segment_2")),
                normalize_label(row.get("pattern_segment_3")),
            ]
            for idx, feats in enumerate(segment_dicts):
                label = labels[idx]
                if label in (None, "unknown"):
                    continue
                if feats is None:
                    continue
                record = {
                    "token_id": token_id,
                    "segment_index": idx + 1,
                }
                for key in SEGMENT_FEATURE_KEYS:
                    record[key] = float(feats.get(key, 0.0))
                record["label"] = label
                dataset.append(record)
    return dataset


def train_model(dataset: List[Dict[str, float]]):
    if not dataset:
        raise RuntimeError("Dataset пуст — нет данных для обучения")
    df = pd.DataFrame(dataset)
    feature_names = ["segment_index"] + SEGMENT_FEATURE_KEYS
    X = df[feature_names].values
    y_raw = df["label"].values
    encoder = LabelEncoder()
    y = encoder.fit_transform(y_raw)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    clf = RandomForestClassifier(
        n_estimators=400,
        random_state=42,
        class_weight="balanced_subsample",
        max_depth=12,
        min_samples_leaf=2,
    )
    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)
    report = classification_report(
        y_test, y_pred, target_names=encoder.classes_, digits=3
    )
    print("=== Classification report ===")
    print(report)
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    dump(
        {
            "model": clf,
            "label_encoder": encoder,
            "feature_names": feature_names,
        },
        MODEL_PATH,
    )
    META_PATH.write_text(
        json.dumps(
            {
                "classes": encoder.classes_.tolist(),
                "feature_names": feature_names,
                "segment_bounds": SEGMENT_BOUNDS,
                "samples": len(dataset),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"Модель сохранена в {MODEL_PATH}")


async def main():
    dataset = await fetch_segment_dataset()
    print(f"Подготовлено {len(dataset)} сегментов для обучения")
    train_model(dataset)


if __name__ == "__main__":
    asyncio.run(main())
