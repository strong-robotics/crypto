#!/usr/bin/env python3
from __future__ import annotations

import numpy as np
from typing import Dict, Any, Tuple


def _safe_arr(x):
    return np.asarray(x, dtype=float) if x is not None else np.asarray([], dtype=float)


def compute_full_features(series: Dict[str, Any]) -> Dict[str, float]:
    """Compute global features on full series (from birth to current).

    series keys expected: price, liquidity, mcap, holders, buy_count, sell_count
    Each value is a list aligned by time (ascending).
    """
    prices = _safe_arr(series.get("price"))
    liq = _safe_arr(series.get("liquidity"))
    mcap = _safe_arr(series.get("mcap"))
    buys = _safe_arr(series.get("buy_count"))
    sells = _safe_arr(series.get("sell_count"))

    n = int(prices.size)
    feats: Dict[str, float] = {"n": float(n)}
    if n < 2 or not np.isfinite(prices).any():
        return feats

    eps = 1e-9
    ln_p = np.log(np.clip(prices, eps, None))
    dln = np.diff(ln_p, prepend=ln_p[0])

    # Trend (OLS) on full series
    x = np.arange(n)
    xm = x.mean(); ym = ln_p.mean()
    num = ((x - xm) * (ln_p - ym)).sum()
    den = ((x - xm) ** 2).sum() + eps
    beta = float(num / den)
    yhat = (x - xm) * beta + ym
    ss_res = ((ln_p - yhat) ** 2).sum()
    ss_tot = ((ln_p - ym) ** 2).sum() + eps
    r2 = float(1.0 - ss_res / ss_tot)

    # Drawdown / recovery
    cum_max = np.maximum.accumulate(prices)
    drawdowns = 1.0 - (prices / np.maximum(cum_max, eps))
    max_dd = float(np.nanmax(drawdowns)) if drawdowns.size > 0 else 0.0
    recovery_ratio = float(prices[-1] / (np.max(prices) + eps)) if n > 0 else 0.0
    run_up_total = float((np.max(prices) / (prices[0] + eps)) - 1.0)
    down_depth = float(1.0 - (np.min(prices) / (prices[0] + eps)))

    # Volatility/monotonicity
    vol = float(np.std(dln))
    monotonicity = float(np.mean((dln > 0).astype(float)))

    # Activity
    tx_total = float(np.nansum(buys + sells)) if buys.size and sells.size else 0.0
    sells_sum = float(np.nansum(sells)) if sells.size else 0.0
    buys_sum = float(np.nansum(buys)) if buys.size else 0.0
    sell_share = float(sells_sum / (buys_sum + sells_sum + eps)) if (buys.size or sells.size) else 0.0

    feats.update({
        "slope_total": beta,
        "r2_total": r2,
        "volatility": vol,
        "monotonicity": monotonicity,
        "max_drawdown": max_dd,
        "recovery_ratio": recovery_ratio,
        "run_up_total": run_up_total,
        "down_depth": down_depth,
        "tx_total": tx_total,
        "sell_share": sell_share,
        "price_now": float(prices[-1]),
        "price_start": float(prices[0]),
    })
    return feats


def _score_rising_phoenix(f: Dict[str, float]) -> float:
    # Требуем устойчивый рост с умеренной просадкой и почти полное восстановление
    if f.get("n", 0) < 10:
        return 0.0
    slope = f.get("slope_total", 0.0)
    r2 = f.get("r2_total", 0.0)
    mdd = f.get("max_drawdown", 1.0)
    rec = f.get("recovery_ratio", 0.0)
    run_up = f.get("run_up_total", 0.0)
    if slope <= 0 or r2 < 0.45:
        return 0.0
    base = 0.0
    base += 40.0 * min(1.0, max(0.0, slope / 0.05))
    base += 25.0 * min(1.0, max(0.0, (r2 - 0.45) / 0.45))
    base += 20.0 * min(1.0, max(0.0, run_up / 0.25))
    base += 15.0 * min(1.0, max(0.0, (rec - 0.7) / 0.3))
    base -= 30.0 * max(0.0, (mdd - 0.2) / 0.8)
    return max(0.0, min(100.0, base))


def _score_black_hole(f: Dict[str, float]) -> float:
    if f.get("n", 0) < 10:
        return 0.0
    slope = f.get("slope_total", 0.0)
    mdd = f.get("max_drawdown", 0.0)
    rec = f.get("recovery_ratio", 1.0)
    down = f.get("down_depth", 0.0)
    base = 0.0
    base += 40.0 * min(1.0, max(0.0, (-slope) / 0.05))
    base += 30.0 * min(1.0, max(0.0, mdd / 0.8))
    base += 20.0 * min(1.0, max(0.0, (1.0 - rec) / 1.0))
    base += 10.0 * min(1.0, max(0.0, down / 0.7))
    return max(0.0, min(100.0, base))


def _score_flatliner(f: Dict[str, float]) -> float:
    """Плоская линия: почти нет движения и тренда.
    Строже ограничиваем: низкая вола и почти нулевой наклон, малый общий прирост.
    """
    vol = f.get("volatility", 1.0)
    slope = abs(f.get("slope_total", 0.0))
    run_up = f.get("run_up_total", 0.0)
    if f.get("n", 0) < 10:
        return 0.0
    # Жёсткое условие: очень низкая волатильность и почти нулевой тренд, общий прирост малый
    if vol > 0.004 or slope > 0.003 or run_up > 0.06:
        return 0.0
    # Чем тише и ровнее — тем выше
    base = 100.0 - 1200.0 * vol - 800.0 * slope - 200.0 * max(0.0, run_up)
    return max(0.0, min(100.0, base))


def _score_panic_sink(f: Dict[str, float]) -> float:
    slope = f.get("slope_total", 0.0)
    vol = f.get("volatility", 0.0)
    dd = f.get("max_drawdown", 0.0)
    base = 0.0
    base += 50.0 * min(1.0, max(0.0, (-slope) / 0.05))
    base += 30.0 * min(1.0, max(0.0, dd / 0.8))
    base += 20.0 * min(1.0, max(0.0, vol / 0.05))
    return max(0.0, min(100.0, base))


def _score_wave_rider(f: Dict[str, float]) -> float:
    slope = f.get("slope_total", 0.0)
    vol = f.get("volatility", 0.0)
    r2 = f.get("r2_total", 0.0)
    if slope <= 0:
        return 0.0
    base = 0.0
    base += 40.0 * min(1.0, max(0.0, slope / 0.05))
    base += 30.0 * min(1.0, max(0.0, vol / 0.03))
    base += 30.0 * min(1.0, max(0.0, (0.8 - abs(r2 - 0.5)) / 0.8))
    return max(0.0, min(100.0, base))


def _score_tug_of_war(f: Dict[str, float]) -> float:
    vol = f.get("volatility", 0.0)
    r2 = f.get("r2_total", 0.0)
    base = 0.0
    base += 60.0 * min(1.0, max(0.0, vol / 0.03))
    base += 40.0 * min(1.0, max(0.0, (0.7 - abs(r2 - 0.5)) / 0.7))
    return max(0.0, min(100.0, base))


def _score_gravity_breaker(f: Dict[str, float]) -> float:
    """Gravity Breaker: устойчивый нарастающий тренд с ограниченной просадкой,
    высокая линейность, хорошее восстановление (ряд у максимумов).
    Отличается от flatliner тем, что есть выраженный рост.
    """
    if f.get("n", 0) < 15:
        return 0.0
    slope = f.get("slope_total", 0.0)
    r2 = f.get("r2_total", 0.0)
    mdd = f.get("max_drawdown", 1.0)
    rec = f.get("recovery_ratio", 0.0)
    run_up = f.get("run_up_total", 0.0)
    vol = f.get("volatility", 0.0)
    mono = f.get("monotonicity", 0.0)
    # базовые ворота
    if slope <= 0.008 or r2 < 0.5 or run_up < 0.15 or mdd > 0.30:
        return 0.0
    # умеренная вола (не flat), высокая монотонность, близость к максимумам
    base = 0.0
    base += 35.0 * min(1.0, max(0.0, slope / 0.06))
    base += 20.0 * min(1.0, max(0.0, (r2 - 0.5) / 0.5))
    base += 20.0 * min(1.0, max(0.0, (0.9 - mdd) / 0.9))  # меньше просадка — выше
    base += 15.0 * min(1.0, max(0.0, (rec - 0.75) / 0.25))
    base += 10.0 * min(1.0, max(0.0, (run_up - 0.15) / 0.35))
    # штраф: слишком низкая вола (подозрительно ровно — возможно flat)
    base -= 10.0 * max(0.0, (0.002 - vol) / 0.002)
    # бонус за монотонность
    base += 10.0 * min(1.0, max(0.0, (mono - 0.55) / 0.45))
    return max(0.0, min(100.0, base))


def choose_best_pattern(feats: Dict[str, float]) -> Tuple[str, float]:
    """Return (code, score0..100). UNKNOWN if nothing matches sufficiently.
    Minimalistic rules; later can be replaced by DB-driven JSON rules.
    """
    scores = {
        "rising_phoenix": _score_rising_phoenix(feats),
        "black_hole": _score_black_hole(feats),
        "flatliner": _score_flatliner(feats),
        "panic_sink": _score_panic_sink(feats),
        "wave_rider": _score_wave_rider(feats),
        "tug_of_war": _score_tug_of_war(feats),
        "gravity_breaker": _score_gravity_breaker(feats),
    }
    # best by score
    code = max(scores, key=lambda k: scores[k]) if scores else "unknown"
    score = float(scores.get(code, 0.0))
    # If everything is weak, declare unknown
    if score < 10.0:
        return "unknown", 0.0
    return code, score
