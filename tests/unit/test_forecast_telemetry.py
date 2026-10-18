"""Unit tests for synthetic telemetry generator (FC-UNIT-18, FC-UNIT-19, FC-UNIT-20, FC-UNIT-21)."""

from __future__ import annotations

import ast
from pathlib import Path
import pytest

from sdv_governor.forecast.telemetry import (
    SPLIT_SEEDS,
    TARGET_GUESTS,
    VALID_PROFILE_KINDS,
    TelemetryProfile,
    compute_truth_cross_s,
    generate_episode,
    get_dataset_split,
    verify_disjoint_seeds,
)


def test_fc_unit_18_determinism() -> None:
    """FC-UNIT-18: Same seed gives identical output; different seed differs."""
    nominal_w = 45000.0
    profile = TelemetryProfile(kind="steady")

    ep_a1 = generate_episode("G5", nominal_w, profile, duration_s=100, seed=1001)
    ep_a2 = generate_episode("G5", nominal_w, profile, duration_s=100, seed=1001)
    ep_b = generate_episode("G5", nominal_w, profile, duration_s=100, seed=1002)

    # Identical seed -> identical output
    assert ep_a1 == ep_a2
    assert ep_a1["t"] == ep_a2["t"]
    assert ep_a1["y_obs"] == ep_a2["y_obs"]
    assert ep_a1["y_true"] == ep_a2["y_true"]
    assert ep_a1["mode"] == ep_a2["mode"]
    assert ep_a1["declared_w"] == ep_a2["declared_w"]
    assert ep_a1["truth_cross_s"] == ep_a2["truth_cross_s"]
    assert ep_a1["meta"] == ep_a2["meta"]

    # Different seed -> observations differ
    assert ep_a1["y_obs"] != ep_b["y_obs"]

    # Check determinism across all 8 profile kinds
    for kind in VALID_PROFILE_KINDS:
        p = TelemetryProfile(kind=kind)
        run1 = generate_episode("G5", nominal_w, p, duration_s=60, seed=777)
        run2 = generate_episode("G5", nominal_w, p, duration_s=60, seed=777)
        run3 = generate_episode("G5", nominal_w, p, duration_s=60, seed=778)
        assert run1 == run2, f"Failed determinism for kind {kind}"
        if kind != "quantized":
            assert run1["y_obs"] != run3["y_obs"], f"Different seeds should differ for {kind}"


def test_fc_unit_19_g5_ramp_truth_cross_s() -> None:
    """FC-UNIT-19: G5 45 kW to 52.65 kW ramp over 300s starting at T=120s crosses 49.5 kW at T=297s."""
    nominal_w = 45000.0
    # Slope = (52650 - 45000) / 300 = 25.5 W/s
    # y_true(t) = 45000 + 25.5 * (t - 120)
    # y_true(296) = 49488.0 <= 49500.0
    # y_true(297) = 49513.5 > 49500.0 (and sustained at 298, 299)
    profile = TelemetryProfile(
        kind="ramp",
        params={
            "t0": 120.0,
            "ramp_duration_s": 300.0,
            "initial_w": 45000.0,
            "target_w": 52650.0,
        },
    )
    ep = generate_episode(
        guest_id="G5",
        nominal_w=nominal_w,
        profile=profile,
        duration_s=600,
        seed=1234,
    )

    assert ep["truth_cross_s"] == 297
    assert ep["declared_w"][297] == 45000.0
    threshold = 1.10 * nominal_w  # 49500.0

    assert ep["y_true"][296] <= threshold
    assert ep["y_true"][297] > threshold
    assert ep["y_true"][298] > threshold
    assert ep["y_true"][299] > threshold

    # Also verify with slope_w_per_s parameter
    profile_slope = TelemetryProfile(
        kind="ramp",
        params={
            "t0": 120.0,
            "ramp_duration_s": 300.0,
            "slope_w_per_s": 25.5,
            "initial_w": 45000.0,
        },
    )
    ep_slope = generate_episode("G5", nominal_w, profile_slope, duration_s=600, seed=1234)
    assert ep_slope["truth_cross_s"] == 297


def test_fc_unit_20_stdlib_only() -> None:
    """FC-UNIT-20: AST import analysis verifies only standard library imports."""
    telemetry_file = Path(__file__).resolve().parents[2] / "src" / "sdv_governor" / "forecast" / "telemetry.py"
    tree = ast.parse(telemetry_file.read_text(encoding="utf-8"))

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.add(node.module.split(".")[0])

    allowed_modules = {"__future__", "dataclasses", "math", "random", "typing", "sdv_governor"}
    disallowed = imported_modules - allowed_modules
    assert not disallowed, f"Disallowed imports found in telemetry.py: {disallowed}"


def test_fc_unit_21_disjoint_splits() -> None:
    """FC-UNIT-21: Disjoint splits: intersection of seeds across splits is empty."""
    assert verify_disjoint_seeds() is True

    expected_splits = {"train", "calib", "test", "ood", "control_steady_1h"}
    assert set(SPLIT_SEEDS.keys()) == expected_splits

    split_seed_sets = {
        name: set(range(start, end + 1))
        for name, (start, end) in SPLIT_SEEDS.items()
    }

    # Verify counts per §6.3
    assert len(split_seed_sets["train"]) == 240
    assert len(split_seed_sets["calib"]) == 80
    assert len(split_seed_sets["test"]) == 120
    assert len(split_seed_sets["ood"]) == 120
    assert len(split_seed_sets["control_steady_1h"]) == 6

    # Verify pairwise disjointness
    split_names = list(SPLIT_SEEDS.keys())
    for i in range(len(split_names)):
        for j in range(i + 1, len(split_names)):
            s1 = split_names[i]
            s2 = split_names[j]
            intersection = split_seed_sets[s1].intersection(split_seed_sets[s2])
            assert not intersection, f"Overlap between {s1} and {s2}: {intersection}"


def test_dataset_split_generation_and_mix() -> None:
    """Verify get_dataset_split generates required number of episodes, guests, and profile mix."""
    train_episodes = get_dataset_split("train")
    assert len(train_episodes) == 240

    guest_counts: dict[str, int] = {}
    profile_counts: dict[str, int] = {}

    for ep in train_episodes:
        g = ep["meta"]["guest_id"]
        p = ep["meta"]["profile_kind"]
        guest_counts[g] = guest_counts.get(g, 0) + 1
        profile_counts[p] = profile_counts.get(p, 0) + 1
        assert len(ep["t"]) == 600
        assert len(ep["y_obs"]) == 600
        assert len(ep["y_true"]) == 600
        assert len(ep["mode"]) == 600
        assert len(ep["declared_w"]) == 600

    # Guests G1, G3, G4, G5 in equal proportions (25% each = 60)
    for g in TARGET_GUESTS:
        assert guest_counts[g] == 60

    # Profiles: 30% steady (72), 30% ramp (72), 10% spike (24), 10% mode_change (24),
    # 10% lag/dropout/quantized (24: 8 lag, 8 dropout, 8 quantized), 10% card_modified (24)
    assert profile_counts["steady"] == 72
    assert profile_counts["ramp"] == 72
    assert profile_counts["spike"] == 24
    assert profile_counts["mode_change"] == 24
    assert profile_counts["lag"] == 8
    assert profile_counts["dropout"] == 8
    assert profile_counts["quantized"] == 8
    assert profile_counts["card_modified"] == 24

    # Control 1h: 6 episodes of 3600s, 100% steady
    ctrl_episodes = get_dataset_split("control_steady_1h")
    assert len(ctrl_episodes) == 6
    for ep in ctrl_episodes:
        assert len(ep["t"]) == 3600
        assert ep["meta"]["profile_kind"] == "steady"


def test_invalid_profile_kind_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown telemetry profile kind"):
        TelemetryProfile(kind="invalid_kind")
