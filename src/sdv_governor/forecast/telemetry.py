"""Seeded synthetic 1 Hz guest telemetry generator for E2 (Consumption Observer).

Standard library ONLY: dataclasses, math, random, typing.
Reproducible with random.Random(seed) (Mersenne Twister).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import random
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

VALID_PROFILE_KINDS: Tuple[str, ...] = (
    "steady",
    "ramp",
    "spike",
    "mode_change",
    "lag",
    "dropout",
    "quantized",
    "card_modified",
)

SPLIT_SEEDS: Dict[str, Tuple[int, int]] = {
    "train": (1000, 1239),
    "calib": (2000, 2079),
    "test": (3000, 3119),
    "ood": (4000, 4119),
    "control_steady_1h": (5000, 5005),
}

TARGET_GUESTS: Tuple[str, ...] = ("G1", "G3", "G4", "G5")

DEFAULT_GUEST_SPECS: Dict[str, Dict[str, Any]] = {
    "G1": {
        "nominal_w": 1500.0,
        "default_mode": "NOMINAL",
        "alt_mode": "STANDBY",
        "alt_nominal_w": 300.0,
    },
    "G3": {
        "nominal_w": 1000.0,
        "default_mode": "SURVEY_QUIET",
        "alt_mode": "RECHARGE_CRADLE",
        "alt_nominal_w": 25000.0,
    },
    "G4": {
        "nominal_w": 35000.0,
        "default_mode": "LAUNCH_RECOVERY_OPTICS",
        "alt_mode": "FLIGHT_PATROL",
        "alt_nominal_w": 10000.0,
    },
    "G5": {
        "nominal_w": 45000.0,
        "default_mode": "FULL_SPECTRUM",
        "alt_mode": "DEGRADED_70PCT_LOSS",
        "alt_nominal_w": 8000.0,
    },
}


@dataclass(frozen=True)
class TelemetryProfile:
    """Configuration for synthetic telemetry generation."""

    kind: str
    params: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in VALID_PROFILE_KINDS:
            raise ValueError(
                f"Unknown telemetry profile kind: {self.kind}. "
                f"Expected one of {VALID_PROFILE_KINDS}"
            )


def verify_disjoint_seeds() -> bool:
    """Verify that all seed ranges defined in SPLIT_SEEDS are pairwise disjoint."""
    seen: Set[int] = set()
    for name, (start_seed, end_seed) in SPLIT_SEEDS.items():
        current_range = set(range(start_seed, end_seed + 1))
        if not seen.isdisjoint(current_range):
            return False
        seen.update(current_range)
    return True


def compute_truth_cross_s(
    y_true: Sequence[float],
    declared_w: Sequence[float],
    threshold_ratio: float = 1.10,
    consecutive_s: int = 3,
) -> Optional[int]:
    """Calculate first t where y_true > threshold_ratio * declared_w for consecutive_s seconds."""
    n = len(y_true)
    for t in range(n - consecutive_s + 1):
        if all(
            y_true[t + k] > threshold_ratio * declared_w[t + k]
            for k in range(consecutive_s)
        ):
            return t
    return None


def get_guest_spec(guest_id: str, catalog: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extract guest parameters from catalog or fallback to known default specs."""
    if catalog and guest_id in catalog:
        item = catalog[guest_id]
        if hasattr(item, "modes") and hasattr(item, "default_mode"):
            default_mode = item.default_mode
            nominal_w = float(item.modes[default_mode].nominal_power_w)
            alt_modes = [m for m in item.modes.keys() if m != default_mode]
            alt_mode = alt_modes[0] if alt_modes else default_mode
            alt_nominal_w = float(item.modes[alt_mode].nominal_power_w)
            return {
                "nominal_w": nominal_w,
                "default_mode": default_mode,
                "alt_mode": alt_mode,
                "alt_nominal_w": alt_nominal_w,
            }
        if isinstance(item, dict):
            default_mode = item.get("default_mode", "DEFAULT")
            modes = item.get("modes", {})
            nominal_w = float(modes.get(default_mode, {}).get("nominal_power_w", 1000.0))
            alt_modes = [m for m in modes.keys() if m != default_mode]
            alt_mode = alt_modes[0] if alt_modes else default_mode
            alt_nominal_w = float(modes.get(alt_mode, {}).get("nominal_power_w", nominal_w * 0.6))
            return {
                "nominal_w": nominal_w,
                "default_mode": default_mode,
                "alt_mode": alt_mode,
                "alt_nominal_w": alt_nominal_w,
            }
    return DEFAULT_GUEST_SPECS.get(
        guest_id,
        {
            "nominal_w": 1000.0,
            "default_mode": "DEFAULT",
            "alt_mode": "STANDBY",
            "alt_nominal_w": 500.0,
        },
    )


def generate_episode(
    guest_id: str,
    nominal_w: float,
    profile: TelemetryProfile,
    duration_s: int,
    seed: int,
) -> Dict[str, Any]:
    """Generate a deterministic synthetic episode of guest consumption telemetry.

    Noise: Gaussian + AR(1) phi=0.7.
    Returns:
        {
            "t": [0..N-1],
            "y_obs": [W|None],
            "y_true": [W],
            "mode": [str],
            "declared_w": [W],
            "truth_cross_s": Optional[int],
            "meta": {...}
        }
    """
    rng = random.Random(seed)
    spec = DEFAULT_GUEST_SPECS.get(
        guest_id,
        {
            "nominal_w": nominal_w,
            "default_mode": "DEFAULT",
            "alt_mode": "STANDBY",
            "alt_nominal_w": nominal_w * 0.6,
        },
    )
    default_mode = spec["default_mode"]
    alt_mode = spec["alt_mode"]
    alt_nominal_w = spec["alt_nominal_w"]

    t_series = list(range(duration_s))
    y_true: List[float] = [0.0] * duration_s
    mode_series: List[str] = [default_mode] * duration_s
    declared_w_series: List[float] = [nominal_w] * duration_s
    truth_cross_s: Optional[int] = None

    kind = profile.kind
    params = profile.params

    # 1. Profile synthesis (y_true, mode, declared_w)
    if kind == "steady":
        if "steady_factor" in params:
            factor = float(params["steady_factor"])
        elif "factor" in params:
            factor = float(params["factor"])
        elif "multiplier" in params:
            factor = float(params["multiplier"])
        else:
            factor = rng.uniform(0.90, 1.02)
        y_true = [nominal_w * factor] * duration_s
        truth_cross_s = None

    elif kind in ("ramp", "lag"):
        t0 = float(params.get("t0", params.get("start_s", rng.randint(60, min(180, max(61, duration_s // 3))))))
        ramp_dur = float(
            params.get(
                "ramp_duration_s",
                params.get("duration", params.get("ramp_s", rng.randint(120, min(300, max(121, duration_s - int(t0) - 10))))),
            )
        )
        initial_w = float(params.get("initial_w", params.get("start_w", params.get("base_w", nominal_w))))
        if "target_w" in params:
            target_w = float(params["target_w"])
        elif "target_power_w" in params:
            target_w = float(params["target_power_w"])
        elif "end_w" in params:
            target_w = float(params["end_w"])
        elif "slope_w_per_s" in params or "r" in params:
            r = float(params.get("slope_w_per_s", params.get("r", 0.0)))
            target_w = initial_w + r * ramp_dur
        elif "target_ratio" in params:
            target_w = nominal_w * float(params["target_ratio"])
        elif "target_pct" in params:
            pct = float(params["target_pct"])
            target_w = nominal_w * (pct / 100.0 if pct > 2.0 else pct)
        else:
            target_w = nominal_w * rng.uniform(1.10, 1.25)

        slope = (target_w - initial_w) / ramp_dur if ramp_dur > 0 else 0.0
        for t in range(duration_s):
            if t < t0:
                y_true[t] = initial_w
            elif t <= t0 + ramp_dur:
                y_true[t] = initial_w + slope * (t - t0)
            else:
                y_true[t] = target_w

        truth_cross_s = compute_truth_cross_s(y_true, declared_w_series)

    elif kind == "spike":
        peak_w = float(params.get("peak_w", nominal_w * 1.35))
        n_spikes = int(params.get("n_spikes", rng.randint(1, 3)))
        factor = float(params.get("factor", rng.uniform(0.90, 1.02)))
        y_true = [nominal_w * factor] * duration_s
        for _ in range(n_spikes):
            spike_dur = int(params.get("spike_duration_s", rng.randint(2, 8)))
            spike_start = rng.randint(30, max(31, duration_s - spike_dur - 30))
            for t in range(spike_start, min(duration_s, spike_start + spike_dur)):
                y_true[t] = peak_w
        truth_cross_s = None

    elif kind == "mode_change":
        t_change = int(params.get("t_change", duration_s // 2))
        initial_mode = str(params.get("initial_mode", default_mode))
        new_mode = str(params.get("new_mode", alt_mode))
        new_nominal_w = float(params.get("new_nominal_w", alt_nominal_w))
        factor = float(params.get("factor", rng.uniform(0.95, 1.01)))

        for t in range(duration_s):
            if t < t_change:
                y_true[t] = nominal_w * factor
                mode_series[t] = initial_mode
                declared_w_series[t] = nominal_w
            else:
                y_true[t] = new_nominal_w * factor
                mode_series[t] = new_mode
                declared_w_series[t] = new_nominal_w
        truth_cross_s = None

    elif kind == "dropout":
        base_kind = str(params.get("base", "ramp" if "t0" in params else "steady"))
        if base_kind == "ramp":
            t0 = float(params.get("t0", 120.0))
            ramp_dur = float(params.get("ramp_duration_s", 300.0))
            initial_w = float(params.get("initial_w", nominal_w))
            target_w = float(params.get("target_w", nominal_w * 1.17))
            slope = (target_w - initial_w) / ramp_dur if ramp_dur > 0 else 0.0
            for t in range(duration_s):
                if t < t0:
                    y_true[t] = initial_w
                elif t <= t0 + ramp_dur:
                    y_true[t] = initial_w + slope * (t - t0)
                else:
                    y_true[t] = target_w
            truth_cross_s = compute_truth_cross_s(y_true, declared_w_series)
        else:
            factor = float(params.get("factor", rng.uniform(0.90, 1.02)))
            y_true = [nominal_w * factor] * duration_s
            truth_cross_s = None

    elif kind == "quantized":
        base_kind = str(params.get("base", "ramp" if "t0" in params else "steady"))
        if base_kind == "ramp":
            t0 = float(params.get("t0", 120.0))
            ramp_dur = float(params.get("ramp_duration_s", 300.0))
            initial_w = float(params.get("initial_w", nominal_w))
            target_w = float(params.get("target_w", nominal_w * 1.17))
            slope = (target_w - initial_w) / ramp_dur if ramp_dur > 0 else 0.0
            for t in range(duration_s):
                if t < t0:
                    y_true[t] = initial_w
                elif t <= t0 + ramp_dur:
                    y_true[t] = initial_w + slope * (t - t0)
                else:
                    y_true[t] = target_w
            truth_cross_s = compute_truth_cross_s(y_true, declared_w_series)
        else:
            factor = float(params.get("factor", rng.uniform(0.90, 1.02)))
            y_true = [nominal_w * factor] * duration_s
            truth_cross_s = None

    elif kind == "card_modified":
        factor = float(params.get("factor", 1.0))
        y_true = [nominal_w * factor] * duration_s
        t_modify = int(params.get("t_modify", duration_s // 2))
        new_declared_w = float(params.get("new_declared_w", nominal_w * 0.88))
        for t in range(duration_s):
            if t < t_modify:
                declared_w_series[t] = nominal_w
            else:
                declared_w_series[t] = new_declared_w
        truth_cross_s = compute_truth_cross_s(y_true, declared_w_series)

    # 2. Noise synthesis: Gaussian + AR(1) phi=0.7
    noise_sigma_pct = float(params.get("noise_sigma_pct", 0.015))
    phi = float(params.get("phi", 0.7))
    scale = math.sqrt(max(0.0, 1.0 - phi * phi))

    if kind == "quantized" or noise_sigma_pct <= 0.0:
        noise = [0.0] * duration_s
    else:
        ar_val = rng.gauss(0.0, 1.0)
        noise = []
        for t in range(duration_s):
            sigma_t = declared_w_series[t] * noise_sigma_pct
            noise.append(ar_val * sigma_t)
            ar_val = phi * ar_val + rng.gauss(0.0, scale)

    # 3. Observation synthesis
    y_obs_raw: List[Optional[float]] = [
        max(0.0, y_true[t] + noise[t]) for t in range(duration_s)
    ]

    y_obs: List[Optional[float]]
    if kind == "lag":
        lag_s = int(params.get("lag_s", rng.randint(2, 5)))
        y_obs = [
            y_obs_raw[0] if t < lag_s else y_obs_raw[t - lag_s]
            for t in range(duration_s)
        ]
    elif kind == "dropout":
        y_obs = list(y_obs_raw)
        if "gap_start_s" in params:
            gap_start = int(params["gap_start_s"])
            gap_dur = int(params.get("gap_duration_s", params.get("gap_s", 15.0)))
            gaps = [(gap_start, min(duration_s, gap_start + gap_dur))]
        else:
            n_gaps = int(params.get("n_gaps", 1))
            gaps = []
            for _ in range(n_gaps):
                gap_dur = int(params.get("gap_duration_s", rng.randint(5, min(30, max(6, duration_s // 4)))))
                gap_start = rng.randint(60, max(61, duration_s - gap_dur - 60))
                gaps.append((gap_start, min(duration_s, gap_start + gap_dur)))
        for g_start, g_end in gaps:
            for t in range(g_start, g_end):
                y_obs[t] = None
    elif kind == "quantized":
        step_w = float(params.get("step_w", 100.0))
        y_obs = [round(y_true[t] / step_w) * step_w for t in range(duration_s)]
    else:
        y_obs = y_obs_raw

    meta = {
        "guest_id": guest_id,
        "profile_kind": kind,
        "seed": seed,
        "nominal_w": nominal_w,
        "duration_s": duration_s,
        "params": dict(params),
        "provenance": "SIMULATED",
        "default_mode": default_mode,
    }

    return {
        "t": t_series,
        "y_obs": y_obs,
        "y_true": y_true,
        "mode": mode_series,
        "declared_w": declared_w_series,
        "truth_cross_s": truth_cross_s,
        "meta": meta,
    }


def get_dataset_split(
    split_name: str,
    catalog: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Build pre-registered dataset split with disjoint seeds and balanced profiles.

    Mixed profiles:
        30% steady, 30% ramp, 10% spike, 10% mode_change,
        10% lag/dropout/quantized, 10% card_modified
        across guests G1, G3, G4, G5 in equal proportions.
    """
    if split_name not in SPLIT_SEEDS:
        raise ValueError(
            f"Unknown split name: '{split_name}'. Available: {list(SPLIT_SEEDS.keys())}"
        )
    if not verify_disjoint_seeds():
        raise ValueError("SPLIT_SEEDS are not pairwise disjoint!")

    start_seed, end_seed = SPLIT_SEEDS[split_name]
    episodes: List[Dict[str, Any]] = []

    if split_name == "control_steady_1h":
        duration_s = 3600
        for idx, seed in enumerate(range(start_seed, end_seed + 1)):
            guest_id = TARGET_GUESTS[idx % len(TARGET_GUESTS)]
            spec = get_guest_spec(guest_id, catalog)
            profile = TelemetryProfile(kind="steady")
            ep = generate_episode(
                guest_id=guest_id,
                nominal_w=spec["nominal_w"],
                profile=profile,
                duration_s=duration_s,
                seed=seed,
            )
            episodes.append(ep)
        return episodes

    duration_s = 600
    is_ood = (split_name == "ood")
    other_subkinds = ("lag", "dropout", "quantized")

    for idx, seed in enumerate(range(start_seed, end_seed + 1)):
        guest_id = TARGET_GUESTS[idx % len(TARGET_GUESTS)]
        spec = get_guest_spec(guest_id, catalog)
        block = idx // 10
        pos = idx % 10

        if pos < 3:
            kind = "steady"
        elif pos < 6:
            kind = "ramp"
        elif pos == 6:
            kind = "spike"
        elif pos == 7:
            kind = "mode_change"
        elif pos == 8:
            kind = other_subkinds[block % len(other_subkinds)]
        else:
            kind = "card_modified"

        params: Dict[str, float] = {}
        if is_ood:
            params["noise_sigma_pct"] = 0.045
            if kind in ("ramp", "lag"):
                params["ramp_duration_s"] = 150.0
            elif kind == "dropout":
                params["gap_duration_s"] = 45.0
            elif kind == "quantized":
                params["step_w"] = 500.0
        else:
            if kind == "mode_change":
                params["new_nominal_w"] = spec["alt_nominal_w"]

        profile = TelemetryProfile(kind=kind, params=params)
        ep = generate_episode(
            guest_id=guest_id,
            nominal_w=spec["nominal_w"],
            profile=profile,
            duration_s=duration_s,
            seed=seed,
        )
        episodes.append(ep)

    return episodes
