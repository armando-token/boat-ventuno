"""Unit tests for conformal prediction intervals (FC-UNIT-11, FC-UNIT-22)."""

from __future__ import annotations

import pytest
import yaml

from sdv_governor.forecast.conformal import (
    calibrate_conformal_quantiles,
    compute_conformal_band,
    conformal_lower_quantile,
    conformal_quantile_index,
    conformal_upper_quantile,
)
from sdv_governor.forecast.holt import load_forecast_params, save_forecast_params
from sdv_governor.models import canonical_json_hash


def test_fc_unit_11_conformal_finite_sample_index_and_asymmetry():
    """FC-UNIT-11: Conformal quantile with n=19 and alpha=0.1 -> index ceil(20 * 0.9) = 18.
    
    Also tests preservation of distribution asymmetry in calibrated conformal bands.
    """
    # Finite-sample correction formula: ceil((n + 1) * (1 - alpha))
    idx = conformal_quantile_index(n=19, alpha=0.1)
    assert idx == 18, f"Expected index 18, got {idx}"

    # Asymmetry preserved:
    # Construct asymmetric residuals skewed towards positive overrun
    # (e.g. 19 residuals from a ramp where model lags behind)
    residuals = [
        -0.02, -0.01, -0.005, 0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06,
        0.07, 0.08, 0.10, 0.12, 0.15, 0.18, 0.22, 0.26, 0.30,
    ]
    assert len(residuals) == 19

    # Calibrate quantiles for horizon 30
    q_lo, q_hi = calibrate_conformal_quantiles({30: residuals}, alpha_hi=0.05, alpha_lo=0.05)

    assert 30 in q_lo
    assert 30 in q_hi
    assert q_lo[30] < 0.0
    assert q_hi[30] > 0.20
    # Clear asymmetry: upper bound extends much further than lower bound
    assert abs(q_hi[30]) > 5 * abs(q_lo[30])

    # Apply asymmetric conformal band
    y_hat = 45000.0
    declared_w = 45000.0
    band_lo, band_hi = compute_conformal_band(
        y_hat=y_hat,
        declared_w=declared_w,
        q_lo=q_lo[30],
        q_hi=q_hi[30],
        status="OK",
    )
    assert band_lo < y_hat
    assert band_hi > y_hat
    upper_margin = band_hi - y_hat
    lower_margin = y_hat - band_lo
    assert upper_margin > lower_margin * 5, "Asymmetry must be preserved in band output"


def test_fc_unit_22_params_frozen_hash_mismatch(tmp_path):
    """FC-UNIT-22: Tampered holt_params.yaml causes PARAMS_HASH_MISMATCH failure."""
    params_dict = {
        "schema_version": "sdv-forecast-params/1.0",
        "model": "holt_damped",
        "alpha": 0.3,
        "beta": 0.05,
        "phi": 0.9,
        "horizons_s": [10, 20, 30, 45, 60],
        "warmup_s": 20,
        "regime_k": 4.0,
        "conformal_q": {"10": 0.021, "20": 0.034, "30": 0.047, "45": 0.066, "60": 0.085},
        "conformal_q_hi": {"10": 0.025, "20": 0.038, "30": 0.052, "45": 0.072, "60": 0.092},
        "conformal_q_lo": {"10": -0.015, "20": -0.022, "30": -0.031, "45": -0.045, "60": -0.058},
        "fit": {
            "train_seeds": "1000-1239",
            "calib_seeds": "2000-2079",
            "loss": "mean_MAE_z",
            "grid_size": 64,
        },
        "generator_sha256": "a" * 64,
    }

    # Save valid file using save_forecast_params
    valid_file = tmp_path / "valid_holt_params.yaml"
    valid_hash = save_forecast_params(params_dict, valid_file)
    assert len(valid_hash) == 64

    # Verify loading succeeds
    loaded = load_forecast_params(valid_file)
    assert loaded.alpha == 0.3
    assert loaded.beta == 0.05
    assert loaded.phi == 0.9

    # Now tamper with one scalar in the YAML
    tampered_file = tmp_path / "tampered_holt_params.yaml"
    tampered_data = dict(params_dict)
    tampered_data["alpha"] = 0.5  # Tampered value!
    tampered_data["params_sha256"] = valid_hash  # Old hash kept!

    with open(tampered_file, "w") as f:
        yaml.safe_dump(tampered_data, f)

    # Loading tampered params must fail with PARAMS_HASH_MISMATCH
    with pytest.raises(ValueError, match="PARAMS_HASH_MISMATCH"):
        load_forecast_params(tampered_file)


def test_active_holt_params_on_disk():
    """Verify that the generated data/forecast/holt_params.yaml is valid and uncorrupted."""
    from pathlib import Path
    active_path = Path("data/forecast/holt_params.yaml")
    assert active_path.is_file(), "data/forecast/holt_params.yaml must exist on disk"
    params = load_forecast_params(active_path)
    assert params.alpha > 0.0
    assert params.beta > 0.0
    assert params.phi > 0.0
    assert set(params.horizons_s) == {10, 20, 30, 45, 60}
    for h in [10, 20, 30, 45, 60]:
        assert h in params.conformal_q
        assert h in params.conformal_q_hi
        assert h in params.conformal_q_lo
        assert params.conformal_q_hi[h] > 0.0
        assert params.conformal_q_lo[h] < 0.0
