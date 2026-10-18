"""Holt damped linear trend forecasting model (standard library only).

Implements damped Holt-Winters formulation on normalized telemetry series
z_t = y_t / declared_w(t) per §6.4 of MEGAPLAN.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from sdv_governor.models import canonical_json_hash


@dataclass(frozen=True)
class HoltParams:
    """Hyperparameters for damped Holt forecast model."""
    alpha: float = 0.3
    beta: float = 0.05
    phi: float = 0.90
    horizons_s: Sequence[int] = (10, 20, 30, 45, 60)
    warmup_s: int = 20
    regime_k: float = 4.0
    conformal_q: Dict[int, float] = field(default_factory=dict)
    conformal_q_hi: Dict[int, float] = field(default_factory=dict)
    conformal_q_lo: Dict[int, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alpha": self.alpha,
            "beta": self.beta,
            "phi": self.phi,
            "horizons_s": list(self.horizons_s),
            "warmup_s": self.warmup_s,
            "regime_k": self.regime_k,
            "conformal_q": {str(k): v for k, v in self.conformal_q.items()},
            "conformal_q_hi": {str(k): v for k, v in self.conformal_q_hi.items()},
            "conformal_q_lo": {str(k): v for k, v in self.conformal_q_lo.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> HoltParams:
        raw_q = data.get("conformal_q", {})
        raw_q_hi = data.get("conformal_q_hi", {})
        raw_q_lo = data.get("conformal_q_lo", {})
        q = {int(k): float(v) for k, v in raw_q.items()} if isinstance(raw_q, dict) else {}
        q_hi = {int(k): float(v) for k, v in raw_q_hi.items()} if isinstance(raw_q_hi, dict) else {}
        q_lo = {int(k): float(v) for k, v in raw_q_lo.items()} if isinstance(raw_q_lo, dict) else {}
        return cls(
            alpha=float(data.get("alpha", 0.3)),
            beta=float(data.get("beta", 0.05)),
            phi=float(data.get("phi", 0.90)),
            horizons_s=tuple(int(h) for h in data.get("horizons_s", [10, 20, 30, 45, 60])),
            warmup_s=int(data.get("warmup_s", 20)),
            regime_k=float(data.get("regime_k", 4.0)),
            conformal_q=q,
            conformal_q_hi=q_hi,
            conformal_q_lo=q_lo,
        )


def interp_quantile(qmap: Dict[int, float], h: float, default: float = 0.05) -> float:
    """Calibrated conformal quantile at horizon h: linear between calibrated horizons, clamped at the ends."""
    if not qmap:
        return default
    keys = sorted(qmap)
    if h <= keys[0]:
        return float(qmap[keys[0]])
    if h >= keys[-1]:
        return float(qmap[keys[-1]])
    for k0, k1 in zip(keys, keys[1:]):
        if k0 <= h <= k1:
            frac = (h - k0) / float(k1 - k0)
            return float(qmap[k0] + frac * (qmap[k1] - qmap[k0]))
    return float(qmap[keys[-1]])


@dataclass
class HoltState:
    """State tracking for a single guest's Holt-Winters forecaster."""
    level: float
    trend: float
    n_obs: int
    last_t: int
    resid_ewm_var: float
    break_streak: int
    mode: str
    status: str  # "WARMUP" | "OK" | "STALE" | "UNAVAILABLE" | "REGIME_BREAK"
    rejected: int = 0
    warmup_remaining: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "trend": self.trend,
            "n_obs": self.n_obs,
            "last_t": self.last_t,
            "resid_ewm_var": self.resid_ewm_var,
            "break_streak": self.break_streak,
            "mode": self.mode,
            "status": self.status,
            "rejected": self.rejected,
            "warmup_remaining": self.warmup_remaining,
        }


def create_initial_holt_state(mode: str = "", t: int = 0) -> HoltState:
    """Create a pristine initial HoltState awaiting first observation."""
    return HoltState(
        level=1.0,
        trend=0.0,
        n_obs=0,
        last_t=t,
        resid_ewm_var=0.0,
        break_streak=0,
        mode=mode,
        status="WARMUP",
        rejected=0,
        warmup_remaining=20,
    )


def holt_update(
    st: Optional[HoltState],
    t: int,
    z: Optional[float],
    mode: str,
    params: HoltParams,
) -> HoltState:
    """Update Holt state with observation z at time t (per §6.4).
    
    Args:
        st: Current state (or None if first call).
        t: Integer timestamp (seconds).
        z: Normalized observation (y_t / declared_w) or None if missing.
        mode: Operating mode string.
        params: Model parameters.
        
    Returns:
        New HoltState reflecting the observation and operational rules.
    """
    if st is None:
        st = create_initial_holt_state(mode=mode, t=t)

    # Check whether z is a valid finite non-negative number
    is_valid_z = (
        z is not None
        and isinstance(z, (int, float))
        and math.isfinite(z)
        and z >= 0.0
    )

    rejected_count = st.rejected
    if z is not None and not is_valid_z:
        # Non-finite or negative values rejected and counted in quality.rejected
        rejected_count += 1

    # First observation initialization
    if st.n_obs == 0:
        if not is_valid_z:
            return HoltState(
                level=1.0,
                trend=0.0,
                n_obs=0,
                last_t=t,
                resid_ewm_var=0.0,
                break_streak=0,
                mode=mode,
                status="UNAVAILABLE",
                rejected=rejected_count,
                warmup_remaining=params.warmup_s,
            )
        return HoltState(
            level=float(z),
            trend=0.0,
            n_obs=1,
            last_t=t,
            resid_ewm_var=0.0,
            break_streak=0,
            mode=mode,
            status="WARMUP",
            rejected=rejected_count,
            warmup_remaining=params.warmup_s - 1,
        )

    # Mode change check (reset level & trend, warmup 20s per ASM-FCST-MODE-RESET)
    if mode != st.mode:
        lvl = float(z) if is_valid_z else st.level
        return HoltState(
            level=lvl,
            trend=0.0,
            n_obs=1 if is_valid_z else 0,
            last_t=t,
            resid_ewm_var=st.resid_ewm_var,
            break_streak=0,
            mode=mode,
            status="WARMUP",
            rejected=rejected_count,
            warmup_remaining=params.warmup_s - 1 if is_valid_z else params.warmup_s,
        )

    gap = t - st.last_t

    # Missing or invalid sample handling
    if not is_valid_z:
        # Advance level without observation: l <- l + phi * b
        new_level = st.level + params.phi * st.trend
        new_trend = params.phi * st.trend

        if gap > 10:
            new_status = "UNAVAILABLE"
        elif gap > 3:
            new_status = "STALE"
        else:
            new_status = st.status

        # last_t tracks the last VALID sample so that gaps accumulate across dropouts.
        return HoltState(
            level=new_level,
            trend=new_trend,
            n_obs=st.n_obs,
            last_t=st.last_t,
            resid_ewm_var=st.resid_ewm_var,
            break_streak=st.break_streak,
            mode=st.mode,
            status=new_status,
            rejected=rejected_count,
            warmup_remaining=st.warmup_remaining,
        )

    # Valid sample arrived: check gap before this sample
    if gap > 10:
        # Gap > 10s: reconnect after long hiatus -> UNAVAILABLE recovery / reset warmup
        return HoltState(
            level=float(z),
            trend=0.0,
            n_obs=1,
            last_t=t,
            resid_ewm_var=st.resid_ewm_var,
            break_streak=0,
            mode=mode,
            status="WARMUP",
            rejected=rejected_count,
            warmup_remaining=params.warmup_s - 1,
        )

    # 1-step ahead prediction from previous state
    pred_level = st.level + params.phi * st.trend
    err = float(z) - pred_level

    # Check regime break if not currently in initial/mode warmup
    sigma_floor = 1e-4
    sigma_ewm = math.sqrt(max(st.resid_ewm_var, sigma_floor ** 2))

    is_outlier = (
        st.status == "OK"
        and st.n_obs >= params.warmup_s
        and abs(err) > params.regime_k * sigma_ewm
    )

    if is_outlier:
        new_streak = st.break_streak + 1
        if new_streak >= 3:
            # Regime break detected! Reset level, b=0, 10s warmup per ASM-FCST-REGIME
            return HoltState(
                level=float(z),
                trend=0.0,
                n_obs=st.n_obs + 1,
                last_t=t,
                resid_ewm_var=max(err ** 2, sigma_floor ** 2),
                break_streak=0,
                mode=mode,
                status="REGIME_BREAK",
                rejected=rejected_count,
                warmup_remaining=10,
            )
    else:
        new_streak = 0

    # Holt damped update equations
    # l_t = alpha * z_t + (1 - alpha) * (l_{t-1} + phi * b_{t-1})
    # b_t = beta * (l_t - l_{t-1}) + (1 - beta) * phi * b_{t-1}
    new_level = params.alpha * float(z) + (1.0 - params.alpha) * pred_level
    new_trend = params.beta * (new_level - st.level) + (1.0 - params.beta) * params.phi * st.trend

    # Update residual EWM variance (gamma = 0.05)
    if is_outlier:
        new_var = st.resid_ewm_var
    elif st.resid_ewm_var == 0.0:
        new_var = err ** 2
    else:
        new_var = 0.95 * st.resid_ewm_var + 0.05 * (err ** 2)

    # Determine status
    if st.status == "REGIME_BREAK":
        # First sample after REGIME_BREAK enters 10s WARMUP
        remaining = max(0, st.warmup_remaining - 1)
        new_status = "WARMUP" if remaining > 0 else "OK"
    elif st.warmup_remaining > 0:
        remaining = st.warmup_remaining - 1
        new_status = "WARMUP" if remaining > 0 else "OK"
    else:
        remaining = 0
        new_status = "OK"

    return HoltState(
        level=new_level,
        trend=new_trend,
        n_obs=st.n_obs + 1,
        last_t=t,
        resid_ewm_var=new_var,
        break_streak=new_streak,
        mode=mode,
        status=new_status,
        rejected=rejected_count,
        warmup_remaining=remaining,
    )


def holt_forecast(
    st: HoltState,
    horizons: Sequence[int],
    params: Optional[HoltParams] = None,
    declared_w: Optional[float] = None,
) -> Optional[Dict[int, float]]:
    """Generate forecast for given horizons.
    
    Returns:
        Dict[horizon_s, forecast_value] in normalized units (or in Watts if declared_w given),
        or None if status is UNAVAILABLE per §6.4.
    """
    if st.status == "UNAVAILABLE":
        return None

    phi = params.phi if params is not None else 0.90
    forecasts: Dict[int, float] = {}

    for h in horizons:
        if abs(1.0 - phi) < 1e-9:
            scale = float(h)
        else:
            scale = phi * (1.0 - math.pow(phi, h)) / (1.0 - phi)
        z_hat = st.level + st.trend * scale
        if declared_w is not None:
            forecasts[h] = z_hat * declared_w
        else:
            forecasts[h] = z_hat

    return forecasts


def load_forecast_params(path: str | Path) -> HoltParams:
    """Load forecast params from YAML and verify canonical SHA-256 (FC-UNIT-22)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Forecast params file not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid forecast params structure in {p}")

    declared_hash = data.get("params_sha256")
    if not declared_hash:
        raise ValueError(f"PARAMS_HASH_MISMATCH: Missing params_sha256 in {p}")

    # Compute canonical hash excluding params_sha256
    dict_to_hash = {k: v for k, v in data.items() if k != "params_sha256"}
    computed_hash = canonical_json_hash(dict_to_hash)
    if declared_hash != computed_hash:
        raise ValueError(
            f"PARAMS_HASH_MISMATCH: declared {declared_hash} != computed {computed_hash}"
        )

    return HoltParams.from_dict(data)


def save_forecast_params(
    params_dict: Dict[str, Any],
    output_path: str | Path,
) -> str:
    """Save forecast parameters dictionary to YAML with canonical params_sha256.
    
    Returns:
        The computed canonical params_sha256.
    """
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    # Compute hash over dictionary without params_sha256
    dict_to_hash = {k: v for k, v in params_dict.items() if k != "params_sha256"}
    computed_hash = canonical_json_hash(dict_to_hash)
    params_dict["params_sha256"] = computed_hash

    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(params_dict, f, sort_keys=False)

    return computed_hash
