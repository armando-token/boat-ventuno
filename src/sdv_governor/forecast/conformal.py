"""Split-conformal prediction intervals by horizon (standard library only).

Implements finite-sample asymmetric conformal uncertainty intervals per §6.5 of MEGAPLAN.
No numpy or scipy used.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple


def conformal_quantile_index(n: int, alpha: float) -> int:
    """Compute 1-based conformal quantile index with finite-sample correction.
    
    Formula: ceil((n + 1) * (1 - alpha)).
    For n=19, alpha=0.1: ceil(20 * 0.9) = 18 (FC-UNIT-11).
    """
    if n <= 0:
        raise ValueError(f"Sample size n must be positive, got {n}")
    return math.ceil((n + 1) * (1.0 - alpha))


def conformal_upper_quantile(samples: Sequence[float], alpha_hi: float = 0.05) -> float:
    """Compute upper conformal quantile with finite sample correction.
    
    For 90% coverage interval with two-sided 5% tails, alpha_hi = 0.05 (nominal 0.95 quantile).
    """
    if not samples:
        return 0.0
    sorted_s = sorted(samples)
    n = len(sorted_s)
    k = conformal_quantile_index(n, alpha_hi)
    # Clip 1-based rank to [1, n], convert to 0-based index
    idx = min(max(k, 1), n) - 1
    return float(sorted_s[idx])


def conformal_lower_quantile(samples: Sequence[float], alpha_lo: float = 0.05) -> float:
    """Compute lower conformal quantile for asymmetric intervals.
    
    For the lower 5% tail, uses floor((n + 1) * alpha_lo) rank.
    """
    if not samples:
        return 0.0
    sorted_s = sorted(samples)
    n = len(sorted_s)
    k = math.floor((n + 1) * alpha_lo)
    # Clip 1-based rank to [1, n], convert to 0-based index
    idx = min(max(k, 1), n) - 1
    return float(sorted_s[idx])


def calibrate_conformal_quantiles(
    residuals_by_horizon: Dict[int, Sequence[float]],
    alpha_hi: float = 0.05,
    alpha_lo: float = 0.05,
) -> Tuple[Dict[int, float], Dict[int, float]]:
    """Calibrate asymmetric conformal residual quantiles per horizon.
    
    Args:
        residuals_by_horizon: Mapping from horizon (seconds) to residual list (z - z_hat).
        alpha_hi: Upper tail error rate (default 0.05 for 95% upper limit).
        alpha_lo: Lower tail error rate (default 0.05 for 5% lower limit).
        
    Returns:
        (q_lo_by_horizon, q_hi_by_horizon) in normalized z units.
    """
    q_lo: Dict[int, float] = {}
    q_hi: Dict[int, float] = {}

    for h, resids in residuals_by_horizon.items():
        if not resids:
            q_lo[h] = 0.0
            q_hi[h] = 0.0
            continue
        q_lo[h] = conformal_lower_quantile(resids, alpha_lo=alpha_lo)
        q_hi[h] = conformal_upper_quantile(resids, alpha_hi=alpha_hi)

    return q_lo, q_hi


def compute_conformal_band(
    y_hat: float,
    declared_w: float,
    q_lo: float,
    q_hi: float,
    status: str = "OK",
) -> Tuple[float, float]:
    """Apply asymmetric conformal band to forecast point estimate.
    
    Interval: [y_hat + declared * q_lo, y_hat + declared * q_hi].
    Per ASM-FCST-STALE, if status is "STALE", band is widened by 1.5x.
    
    Args:
        y_hat: Point forecast in Watts.
        declared_w: Declared/nominal power in Watts.
        q_lo: Calibrated lower residual quantile in normalized z units.
        q_hi: Calibrated upper residual quantile in normalized z units.
        status: Operational status string.
        
    Returns:
        (lo_w, hi_w) bounding interval in Watts (non-negative).
    """
    mult = 1.5 if status == "STALE" else 1.0
    lo = y_hat + declared_w * q_lo * mult
    hi = y_hat + declared_w * q_hi * mult
    return (max(0.0, lo), max(0.0, hi))
