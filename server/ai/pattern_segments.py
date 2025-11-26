import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Dict

import numpy as np


SEGMENT_BOUNDS = [
    (0, 35),   # Segment 1: birth -> first corridor
    (35, 85),  # Segment 2: between first and second corridor
    (85, 170), # Segment 3: final window before exit (extended to 170s to detect post-entry drops)
]

MIN_POINTS_PER_SEGMENT = 6
SEGMENT_FEATURE_KEYS = [
    "slope",
    "r2",
    "drop",
    "recovery",
    "neg_ratio",
    "volatility",
    "mean_price",
    "median_price",
    "max_price",
    "min_price",
    "length",
    "buy_sum",
    "sell_sum",
    "sell_share",
]


@dataclass
class SegmentSeries:
    prices: List[float]
    buys: List[float]
    sells: List[float]


def _safe_array(values: Sequence[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return arr
    return np.nan_to_num(arr, copy=False, nan=0.0, posinf=0.0, neginf=0.0)


def _linear_regression(values: np.ndarray) -> Dict[str, float]:
    if values.size <= 1:
        return {"slope": 0.0, "r2": 0.0}
    x = np.arange(values.size, dtype=float)
    y = np.log(np.clip(values, 1e-9, None))
    x_mean = x.mean()
    y_mean = y.mean()
    cov = np.sum((x - x_mean) * (y - y_mean))
    var = np.sum((x - x_mean) ** 2)
    slope = cov / var if var > 0 else 0.0
    y_hat = slope * (x - x_mean) + y_mean
    ss_res = np.sum((y - y_hat) ** 2)
    ss_tot = np.sum((y - y_mean) ** 2)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-9 else 0.0
    return {"slope": float(slope), "r2": float(r2)}


def _drawdown(prefix: np.ndarray, segment: np.ndarray) -> Dict[str, float]:
    if segment.size == 0:
        return {"drop": 0.0, "recovery": 0.0}
    peak_source = prefix if prefix.size else segment
    peak = float(np.max(np.clip(peak_source, 1e-9, None)))
    trough = float(np.min(np.clip(segment, 1e-9, None)))
    last = float(segment[-1])
    drop = (peak - trough) / peak if peak > 1e-9 else 0.0
    span = peak - trough
    recovery = (last - trough) / span if span > 1e-9 else 0.0
    return {"drop": float(drop), "recovery": float(recovery)}


def _neg_ratio(segment: np.ndarray) -> float:
    if segment.size <= 1:
        return 0.0
    diffs = np.diff(segment)
    return float(np.mean(diffs < 0))


def _volatility(segment: np.ndarray) -> float:
    if segment.size <= 1:
        return 0.0
    diffs = np.diff(np.log(np.clip(segment, 1e-9, None)))
    return float(np.std(diffs))


def _buy_sell_stats(buys: np.ndarray, sells: np.ndarray) -> Dict[str, float]:
    b = float(np.sum(buys))
    s = float(np.sum(sells))
    total = b + s
    share = s / total if total > 0 else 0.0
    return {
        "buy_sum": b,
        "sell_sum": s,
        "sell_share": share,
    }


def compute_segment_features(
    prices: List[float],
    buys: List[float],
    sells: List[float],
    start: int,
    end: int,
) -> Optional[Dict[str, float]]:
    if not prices:
        return None
    start_idx = max(start - 1, 0)
    end_idx = min(end, len(prices))
    segment_prices = _safe_array(prices[start_idx:end_idx])
    segment_buys = _safe_array(buys[start_idx:end_idx])
    segment_sells = _safe_array(sells[start_idx:end_idx])
    if segment_prices.size < MIN_POINTS_PER_SEGMENT:
        return None
    prefix_prices = _safe_array(prices[:start_idx])
    lr = _linear_regression(segment_prices)
    dd = _drawdown(prefix_prices, segment_prices)
    stats = {
        **lr,
        **dd,
        "neg_ratio": _neg_ratio(segment_prices),
        "volatility": _volatility(segment_prices),
        "mean_price": float(np.mean(segment_prices)),
        "median_price": float(np.median(segment_prices)),
        "max_price": float(np.max(segment_prices)),
        "min_price": float(np.min(segment_prices)),
        "length": float(segment_prices.size),
        **_buy_sell_stats(segment_buys, segment_sells),
    }
    return stats


def extract_series(rows: Sequence[Dict[str, float]]) -> SegmentSeries:
    prices, buys, sells = [], [], []
    for row in rows:
        price = row.get("usd_price")
        if price is None:
            continue
        prices.append(float(price))
        buys.append(float(row.get("buy_count") or 0.0))
        sells.append(float(row.get("sell_count") or 0.0))
    return SegmentSeries(prices=prices, buys=buys, sells=sells)


def feature_vector_for_segments(series: SegmentSeries) -> List[Optional[Dict[str, float]]]:
    vectors: List[Optional[Dict[str, float]]] = []
    for start, end in SEGMENT_BOUNDS:
        vec = compute_segment_features(series.prices, series.buys, series.sells, start, end)
        vectors.append(vec)
    return vectors


def flatten_features(segments: List[Optional[Dict[str, float]]]) -> Optional[List[float]]:
    if not segments:
        return None
    ordered_keys: List[str] = []
    for idx, seg in enumerate(segments):
        if seg:
            ordered_keys.extend([f"seg{idx+1}_{k}" for k in seg.keys()])
        else:
            # Ensure consistent ordering even if later segments have data
            ordered_keys.extend([])
    # To keep deterministic ordering across different segments, define canonical key order
    base_keys = SEGMENT_FEATURE_KEYS
    vector: List[float] = []
    for seg in segments:
        if seg is None:
            vector.extend([0.0] * len(base_keys))
        else:
            vector.extend([float(seg.get(k, 0.0)) for k in base_keys])
    return vector


FEATURE_NAMES = []
for idx in range(len(SEGMENT_BOUNDS)):
    prefix = f"seg{idx+1}_"
    FEATURE_NAMES.extend([
        f"{prefix}slope",
        f"{prefix}r2",
        f"{prefix}drop",
        f"{prefix}recovery",
        f"{prefix}neg_ratio",
        f"{prefix}volatility",
        f"{prefix}mean_price",
        f"{prefix}median_price",
        f"{prefix}max_price",
        f"{prefix}min_price",
        f"{prefix}length",
        f"{prefix}buy_sum",
        f"{prefix}sell_sum",
        f"{prefix}sell_share",
    ])
