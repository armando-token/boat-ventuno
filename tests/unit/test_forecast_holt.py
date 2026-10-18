"""Unit tests for Holt damped linear trend model (FC-UNIT-01..09)."""

from __future__ import annotations

import math
import pytest

from sdv_governor.forecast.conformal import compute_conformal_band
from sdv_governor.forecast.holt import (
    HoltParams,
    HoltState,
    create_initial_holt_state,
    holt_forecast,
    holt_update,
)


def test_fc_unit_01_constant_series():
    """FC-UNIT-01: Holt with constant series: after warm-up, |z_hat - 1| < 1e-9 and b -> 0."""
    params = HoltParams(alpha=0.3, beta=0.05, phi=0.9, warmup_s=20)
    st = None
    for t in range(25):
        st = holt_update(st, t=t, z=1.0, mode="NOMINAL", params=params)

    assert st.status == "OK"
    assert abs(st.trend) < 1e-9
    assert abs(st.level - 1.0) < 1e-9

    fc = holt_forecast(st, horizons=[10, 20, 30, 45, 60], params=params)
    assert fc is not None
    for h, z_hat in fc.items():
        assert abs(z_hat - 1.0) < 1e-9


def test_fc_unit_02_linear_ramp_phi_1_limit():
    """FC-UNIT-02: Holt with linear ramp, no noise, phi=1: forecast at h=30 has error < 0.5%."""
    params = HoltParams(alpha=0.3, beta=0.1, phi=1.0, warmup_s=20)
    slope = 0.005
    st = None
    # Feed ramp z_t = 1.0 + slope * t for 50 steps
    for t in range(50):
        z_t = 1.0 + slope * t
        st = holt_update(st, t=t, z=z_t, mode="NOMINAL", params=params)

    assert st.status == "OK"
    fc = holt_forecast(st, horizons=[30], params=params)
    assert fc is not None
    z_hat_30 = fc[30]
    z_true_30 = 1.0 + slope * (49 + 30)
    rel_error = abs(z_hat_30 - z_true_30) / z_true_30
    assert rel_error < 0.005, f"Relative error {rel_error:.6f} exceeded 0.5%"


def test_fc_unit_03_damping_bounded_and_monotone():
    """FC-UNIT-03: Damping: with phi < 1, z_hat(h) is monotone and bounded by l + b*phi/(1-phi)."""
    params = HoltParams(alpha=0.3, beta=0.05, phi=0.90)
    st = None
    for t in range(25):
        st = holt_update(st, t=t, z=1.0 + 0.02 * t, mode="NOMINAL", params=params)

    assert st.trend > 0
    horizons = [5, 10, 20, 30, 45, 60, 100, 200]
    fc = holt_forecast(st, horizons=horizons, params=params)
    assert fc is not None

    # Verify monotone increasing
    prev = fc[horizons[0]]
    for h in horizons[1:]:
        assert fc[h] > prev
        prev = fc[h]

    # Verify strictly bounded by theoretical asymptote
    asymptote = st.level + st.trend * (params.phi / (1.0 - params.phi))
    for h in horizons:
        assert fc[h] < asymptote


def test_fc_unit_04_exact_equations():
    """FC-UNIT-04: Exact equations: values calculated by hand in 5 steps match to 1e-12."""
    params = HoltParams(alpha=0.3, beta=0.1, phi=0.9, warmup_s=20)
    # Hand-derived step trace:
    # Step 0: z_0 = 1.0 -> l_0 = 1.0, b_0 = 0.0
    # Step 1: z_1 = 1.2
    #   l_pred = 1.0 + 0.9 * 0.0 = 1.0
    #   l_1 = 0.3 * 1.2 + 0.7 * 1.0 = 1.06
    #   b_1 = 0.1 * (1.06 - 1.0) + 0.9 * 0.0 = 0.006
    # Step 2: z_2 = 1.1
    #   l_pred = 1.06 + 0.9 * 0.006 = 1.0654
    #   l_2 = 0.3 * 1.1 + 0.7 * 1.0654 = 1.07578
    #   b_2 = 0.1 * (1.07578 - 1.06) + 0.81 * 0.006 = 0.006438
    # Step 3: z_3 = 1.3
    #   l_pred = 1.07578 + 0.9 * 0.006438 = 1.0815742
    #   l_3 = 0.3 * 1.3 + 0.7 * 1.0815742 = 1.14710194
    #   b_3 = 0.1 * (1.14710194 - 1.07578) + 0.81 * 0.006438 = 0.012346974
    # Step 4: z_4 = 1.5
    #   l_pred = 1.14710194 + 0.9 * 0.012346974 = 1.1582142166
    #   l_4 = 0.3 * 1.5 + 0.7 * 1.1582142166 = 1.26074995162
    #   b_4 = 0.1 * (1.26074995162 - 1.14710194) + 0.81 * 0.012346974 = 0.021365850102

    expected_trace = [
        (1.0, 0.0),
        (1.06, 0.006),
        (1.07578, 0.006438),
        (1.14710194, 0.012346974),
        (1.26074995162, 0.021365850102),
    ]

    z_vals = [1.0, 1.2, 1.1, 1.3, 1.5]
    st = None
    for t, z in enumerate(z_vals):
        st = holt_update(st, t=t, z=z, mode="NOMINAL", params=params)
        exp_l, exp_b = expected_trace[t]
        assert abs(st.level - exp_l) < 1e-12, f"Step {t} level mismatch: {st.level} != {exp_l}"
        assert abs(st.trend - exp_b) < 1e-12, f"Step {t} trend mismatch: {st.trend} != {exp_b}"


def test_fc_unit_05_mode_change_reset():
    """FC-UNIT-05: Mode change reset: b=0, status=WARMUP, warmup for 20 samples."""
    params = HoltParams(alpha=0.3, beta=0.05, phi=0.9, warmup_s=20)
    st = None
    for t in range(25):
        st = holt_update(st, t=t, z=1.0 + 0.01 * t, mode="NOMINAL", params=params)

    assert st.status == "OK"
    assert st.trend > 0

    # Mode switch at t=25
    st = holt_update(st, t=25, z=0.5, mode="ECO", params=params)
    assert st.mode == "ECO"
    assert st.trend == 0.0
    assert abs(st.level - 0.5) < 1e-9
    assert st.status == "WARMUP"

    # Next 18 samples remain in WARMUP (total 19 samples)
    for t in range(26, 44):
        st = holt_update(st, t=t, z=0.5, mode="ECO", params=params)
        assert st.status == "WARMUP"

    # On the 20th sample after reset, status becomes OK
    st = holt_update(st, t=44, z=0.5, mode="ECO", params=params)
    assert st.status == "OK"


def test_fc_unit_06_stale_gap_4s():
    """FC-UNIT-06: Gap of 4s: STALE, band widened 1.5x."""
    params = HoltParams(alpha=0.3, beta=0.05, phi=0.9, warmup_s=20)
    st = None
    for t in range(25):
        st = holt_update(st, t=t, z=1.0, mode="NOMINAL", params=params)

    assert st.status == "OK"
    # Gap of 4 seconds: last_t=24, now t=28
    st = holt_update(st, t=28, z=None, mode="NOMINAL", params=params)
    assert st.status == "STALE"

    fc = holt_forecast(st, horizons=[30], params=params)
    assert fc is not None

    band_ok = compute_conformal_band(fc[30], declared_w=1000.0, q_lo=-0.05, q_hi=0.05, status="OK")
    band_stale = compute_conformal_band(fc[30], declared_w=1000.0, q_lo=-0.05, q_hi=0.05, status=st.status)
    assert abs((band_stale[1] - fc[30]) - 1.5 * (band_ok[1] - fc[30])) < 1e-9


def test_fc_unit_07_unavailable_gap_11s():
    """FC-UNIT-07: Gap of 11s: UNAVAILABLE, forecast == None."""
    params = HoltParams(alpha=0.3, beta=0.05, phi=0.9, warmup_s=20)
    st = None
    for t in range(25):
        st = holt_update(st, t=t, z=1.0, mode="NOMINAL", params=params)

    assert st.status == "OK"
    # Gap of 11 seconds: last_t=24, now t=35
    st = holt_update(st, t=35, z=None, mode="NOMINAL", params=params)
    assert st.status == "UNAVAILABLE"

    fc = holt_forecast(st, horizons=[30], params=params)
    assert fc is None


def test_fc_unit_08_regime_break():
    """FC-UNIT-08: Regime break: unannounced step of +30% triggers REGIME_BREAK in <= 3 samples."""
    params = HoltParams(alpha=0.3, beta=0.05, phi=0.9, warmup_s=20, regime_k=4.0)
    st = None
    # 30 samples of steady state at z=1.0
    for t in range(30):
        st = holt_update(st, t=t, z=1.0, mode="NOMINAL", params=params)
    assert st.status == "OK"

    # Sudden step to z=1.30 (+30%)
    regime_break_seen = False
    for step in range(1, 4):
        st = holt_update(st, t=30 + step, z=1.30, mode="NOMINAL", params=params)
        if st.status == "REGIME_BREAK":
            regime_break_seen = True
            break

    assert regime_break_seen, f"Regime break not triggered within 3 samples (status: {st.status})"


def test_fc_unit_09_invalid_inputs():
    """FC-UNIT-09: Invalid inputs: NaN, -5, inf counted in rejected, state preserved."""
    params = HoltParams(alpha=0.3, beta=0.05, phi=0.9, warmup_s=20)
    st = None
    for t in range(25):
        st = holt_update(st, t=t, z=1.0, mode="NOMINAL", params=params)
    assert st.status == "OK"
    assert st.rejected == 0

    # NaN
    st = holt_update(st, t=25, z=float("nan"), mode="NOMINAL", params=params)
    assert st.rejected == 1
    assert math.isfinite(st.level)
    assert math.isfinite(st.trend)

    # Negative value
    st = holt_update(st, t=26, z=-5.0, mode="NOMINAL", params=params)
    assert st.rejected == 2
    assert math.isfinite(st.level)
    assert math.isfinite(st.trend)

    # Inf
    st = holt_update(st, t=27, z=float("inf"), mode="NOMINAL", params=params)
    assert st.rejected == 3
    assert math.isfinite(st.level)
    assert math.isfinite(st.trend)
