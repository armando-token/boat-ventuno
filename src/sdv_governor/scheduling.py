"""Scenario C Crew Regime Scheduler (14/6/0), A' Operational Windows, and Lost-Feed Resilience (B7, ROB-03)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .budgets import evaluate_eight_class_budgets
from .models import GuestManifest, StrictValidationError
from .ship_state import ShipOperationalState

CREW_REGIMES: Dict[str, Dict[str, Any]] = {
    "FULL_CREW_14": {
        "crew_total": 14,
        "watch_bridge_engine": 5,
        "available_for_guest_ops": 9,
        "max_concurrent_manual_deck_ops": 2,
        "manual_launch_recovery_permitted": True,
        "at_sea_vehicle_replacement_permitted": True,
        "fault_intervention_permitted": True,
    },
    "REDUCED_CREW_6": {
        "crew_total": 6,
        "watch_bridge_engine": 4,
        "available_for_guest_ops": 2,
        "max_concurrent_manual_deck_ops": 1,
        "manual_launch_recovery_permitted": True,
        "at_sea_vehicle_replacement_permitted": False,
        "fault_intervention_permitted": True,
    },
    "UNCREWED_0": {
        "crew_total": 0,
        "watch_bridge_engine": 0,
        "available_for_guest_ops": 0,
        "max_concurrent_manual_deck_ops": 0,
        "manual_launch_recovery_permitted": False,
        "at_sea_vehicle_replacement_permitted": False,
        "fault_intervention_permitted": False,
    },
}


def evaluate_a_prime_window(
    guest: GuestManifest,
    operation_type: str,
    state: ShipOperationalState,
    catalog: Dict[str, GuestManifest],
) -> Dict[str, Any]:
    """Evaluate whether an operation ('LAUNCH_RECOVERY', 'VEHICLE_REPLACEMENT_AT_SEA', 'AT_SEA_INSTALL') is permitted.

    Explicitly separates normal flight launch/recovery window (5-12 kn, wind <= 25 kn) from the
    stricter A' at-sea vehicle replacement / installation window (4-8 kn, wind <= 20 kn, sea_state <= 3),
    enforces flight deck clearance (`state.deck_clear`), stern ramp exclusivity, at-sea package mass
    limit (`<= 250 kg`), port-only bay/mast installation rules (Annex A.4, A.5), and the acoustic
    survey deck-machinery lockout (`G3` `forbids_deck_machinery_while_active`).
    """
    reasons: List[str] = []
    sc = guest.ship_state_constraints

    # 1. Flight deck / working deck clearance check (Annex A.5)
    if operation_type in ("LAUNCH_RECOVERY", "VEHICLE_REPLACEMENT_AT_SEA", "AT_SEA_INSTALL"):
        if not state.deck_clear:
            reasons.append("flight_deck_not_clear: state.deck_clear is False (Annex A.5)")

    # 2. Stern ramp & flight deck exclusivity check (Annex A.5)
    if sc.requires_stern_ramp_free or guest.guest_id in ("G3", "G4"):
        for other_gid, other_inst in state.installed_guests.items():
            if other_gid == guest.guest_id:
                continue
            for _, pos in other_inst.placements.items():
                if pos == "stern_ramp":
                    reasons.append(f"stern_ramp occupied by {other_gid} (Annex A.5 exclusivity)")

    # 3. Check if any active guest forbids deck machinery (e.g. G3 in SURVEY_QUIET)
    for gid, ginst in state.installed_guests.items():
        if gid == guest.guest_id or not ginst.powered_on:
            continue
        other_man = catalog.get(gid)
        if other_man and other_man.ship_state_constraints.forbids_deck_machinery_while_active:
            if ginst.active_mode in ("SURVEY_QUIET", "QUIET_SURVEY_ACTIVE"):
                reasons.append(f"Active guest {gid} ({ginst.active_mode}) forbids deck machinery due to acoustic survey")

    if operation_type == "LAUNCH_RECOVERY":
        min_spd = sc.min_speed_kn_for_operation
        max_spd = sc.max_speed_kn_for_operation
        max_wind = sc.max_wind_kn_for_operation
        max_ss = sc.max_sea_state_for_operation
        req_crew = 2 if guest.guest_id in ("G2", "G4") else 0

        if not (min_spd <= state.speed_kn <= max_spd):
            reasons.append(f"speed {state.speed_kn} kn outside normal op window [{min_spd}, {max_spd}] kn")
        if state.wind_kn > max_wind:
            reasons.append(f"wind {state.wind_kn} kn > max {max_wind} kn")
        if state.sea_state > max_ss:
            reasons.append(f"sea_state {state.sea_state} > max {max_ss}")
        if sc.requires_heading_into_wind_for_launch_recovery and abs(state.heading_deg - state.wind_deg) > 15.0:
            reasons.append(f"vessel heading {state.heading_deg} deg not aligned into wind {state.wind_deg} deg")
        if state.available_crew < req_crew:
            reasons.append(f"available_crew {state.available_crew} < required {req_crew} for manual launch/recovery")

    elif operation_type == "VEHICLE_REPLACEMENT_AT_SEA":
        vr = guest.vehicle_replacement
        if not vr.supported_at_sea:
            reasons.append(f"guest {guest.guest_id} does not support at-sea vehicle replacement")
        else:
            if not (vr.min_speed_kn <= state.speed_kn <= vr.max_speed_kn):
                reasons.append(f"speed {state.speed_kn} kn outside A' replacement window [{vr.min_speed_kn}, {vr.max_speed_kn}] kn")
            if state.wind_kn > vr.max_wind_kn:
                reasons.append(f"wind {state.wind_kn} kn > A' replacement max {vr.max_wind_kn} kn")
            if state.sea_state > vr.max_sea_state:
                reasons.append(f"sea_state {state.sea_state} > A' replacement max {vr.max_sea_state}")
            if state.available_crew < vr.min_crew_required:
                reasons.append(f"available_crew {state.available_crew} < A' replacement required {vr.min_crew_required}")

    elif operation_type == "AT_SEA_INSTALL":
        inst = guest.installation
        port_only_positions = {"bay1", "bay1_a", "bay1_b", "bay2", "mast_1", "mast_2", "mast_3"}
        if inst.port_only:
            reasons.append(f"guest {guest.guest_id} is port_only=True")
        for subc in guest.subcomponents:
            if all(p in port_only_positions for p in subc.allowed_positions):
                reasons.append(
                    f"subcomponent {subc.subcomponent_id} requires bay/mast position {subc.allowed_positions} which is port-only (Annex A.5)"
                )
        if guest.total_mass_kg > 250:
            reasons.append(
                f"guest {guest.guest_id} total_mass_kg {guest.total_mass_kg} kg > 250 kg at-sea package install limit (Annex A.4)"
            )
        if state.sea_state > 3:
            reasons.append(f"sea_state {state.sea_state} > 3 A' at-sea install limit (Annex A.4)")
        eff_max_wind = min(20, inst.sea_install_max_wind_kn) if inst.sea_install_max_wind_kn > 0 else 20
        if state.wind_kn > eff_max_wind:
            reasons.append(f"wind {state.wind_kn} kn > A' install max {eff_max_wind} kn (Annex A.4)")
        if not (4.0 <= state.speed_kn <= 8.0):
            reasons.append(f"speed {state.speed_kn} kn outside A' at-sea install window [4.0, 8.0] kn (Annex A.4)")
        if not inst.port_only and not (inst.sea_install_min_speed_kn <= state.speed_kn <= inst.sea_install_max_speed_kn):
            reasons.append(
                f"speed {state.speed_kn} kn outside guest install window [{inst.sea_install_min_speed_kn}, {inst.sea_install_max_speed_kn}] kn"
            )
        if state.crew_count <= 0 or state.available_crew < max(1, inst.min_crew_required):
            reasons.append(f"available_crew {state.available_crew} < required {max(1, inst.min_crew_required)}")
    else:
        raise StrictValidationError("UNKNOWN_ENUM", f"Unknown operation_type '{operation_type}'", "operation_type")

    return {
        "guest_id": guest.guest_id,
        "operation_type": operation_type,
        "permitted": len(reasons) == 0,
        "blocking_reasons": reasons,
        "vessel_speed_kn": state.speed_kn,
        "wind_kn": state.wind_kn,
        "sea_state": state.sea_state,
        "available_crew": state.available_crew,
        "deck_clear": state.deck_clear,
    }


def evaluate_crew_regimes_comparison(
    catalog: Dict[str, GuestManifest],
    base_state: ShipOperationalState,
) -> Dict[str, Any]:
    """Compare operational capability, concurrency, and blocked tasks across 14, 6, and 0 crew regimes (Scenario C)."""
    regime_reports: Dict[str, Any] = {}

    for regime_id, rspec in CREW_REGIMES.items():
        st = base_state.clone()
        st.crew_count = int(rspec["crew_total"])
        st.crew_regime = regime_id
        st.available_crew = int(rspec["available_for_guest_ops"])

        # Test key operations under this regime in calm A' speed (6 kn, wind 15 kn, heading into wind)
        st.speed_kn = 6.0
        st.ship_speed_kn = 6.0
        st.wind_kn = 15
        st.heading_deg = st.wind_deg
        st.sea_state = 2
        st.deck_clear = True
        # Assume G3 paused so we isolate crew effects alongside acoustic rules
        if "G3" in st.installed_guests:
            st.installed_guests["G3"].active_mode = "HOLD_ACOUSTIC"

        g4_launch = evaluate_a_prime_window(catalog["G4"], "LAUNCH_RECOVERY", st, catalog)
        g4_replace = evaluate_a_prime_window(catalog["G4"], "VEHICLE_REPLACEMENT_AT_SEA", st, catalog)
        g3_replace = evaluate_a_prime_window(catalog["G3"], "VEHICLE_REPLACEMENT_AT_SEA", st, catalog)
        g6_sea_install = evaluate_a_prime_window(catalog["G6"], "AT_SEA_INSTALL", st, catalog)

        # At 3 kn (ROV window), G2 launch is evaluated separately from 6 kn G4 flight window
        st_rov = st.clone()
        st_rov.speed_kn = 3.0
        st_rov.ship_speed_kn = 3.0
        g2_launch = evaluate_a_prime_window(catalog["G2"], "LAUNCH_RECOVERY", st_rov, catalog)
        g2_replace = evaluate_a_prime_window(catalog["G2"], "VEHICLE_REPLACEMENT_AT_SEA", st, catalog)

        # Under Annex A.5, G4 launch/recovery requires flight_deck clear of other ongoing deck operations,
        # and G2 ([0, 5] kn) vs G4 ([5, 12] kn into wind) have disjoint operational envelopes.
        regime_reports[regime_id] = {
            "crew_total": rspec["crew_total"],
            "watch_bridge_engine": rspec["watch_bridge_engine"],
            "available_for_guest_ops": rspec["available_for_guest_ops"],
            "max_concurrent_manual_deck_ops": rspec["max_concurrent_manual_deck_ops"],
            "g4_uav_launch_recovery_permitted": g4_launch["permitted"],
            "g4_uav_at_sea_replacement_permitted": g4_replace["permitted"],
            "g3_auv_at_sea_replacement_permitted": g3_replace["permitted"],
            "g2_rov_launch_recovery_permitted": g2_launch["permitted"],
            "g2_rov_at_sea_replacement_permitted": g2_replace["permitted"],
            "g6_at_sea_install_permitted": g6_sea_install["permitted"],
            "simultaneous_g2_and_g4_deck_ops_permitted": False,
            "simultaneous_g2_g4_block_reason": (
                "Annex A.5 requires flight deck clear of other ongoing deck operations during G4 launch/recovery, "
                "and G2 winch window (0-5 kn) conflicts with G4 flight recovery window (5-12 kn into wind)."
            ),
            "autonomous_guests_always_operational": ["G1", "G3", "G5", "G6"],
            "blocking_diagnostics": {
                "G4_launch": g4_launch["blocking_reasons"],
                "G4_replacement": g4_replace["blocking_reasons"],
                "G3_replacement": g3_replace["blocking_reasons"],
                "G2_launch": g2_launch["blocking_reasons"],
                "G2_replacement": g2_replace["blocking_reasons"],
                "G6_at_sea_install": g6_sea_install["blocking_reasons"],
            },
        }

    return {
        "scenario_id": "SCENARIO_C_CREW_REGIMES",
        "regimes": regime_reports,
    }


def handle_lost_or_stale_feed(
    state: ShipOperationalState,
    ship_profile: Dict[str, Any],
    catalog: Dict[str, GuestManifest],
    *,
    feed_name: str,
    age_ms: int,
    stale_threshold_ms: int = 5000,
) -> Dict[str, Any]:
    """Handle a stale or unavailable telemetry feed fail-safe without crashing (ROB-03)."""
    work_state = state.clone()
    status_label = "UNAVAILABLE" if age_ms < 0 or age_ms >= 30000 else ("STALE" if age_ms >= stale_threshold_ms else "MEASURED")
    fallback_applied = "NONE"

    if feed_name == "seawater_temp_c":
        if status_label in ("STALE", "UNAVAILABLE"):
            work_state.seawater_temp_c = None
            work_state.seawater_temp_is_stale = True
            fallback_applied = "CONSERVATIVE_30C_COOLING_CURVE_120KW"
    elif feed_name.startswith("guest_power:"):
        gid = feed_name.split(":", 1)[1]
        if gid in work_state.installed_guests and status_label in ("STALE", "UNAVAILABLE"):
            ginst = work_state.installed_guests[gid]
            mode_spec = catalog[gid].modes[ginst.active_mode]
            # Apply conservative sustained power + 10% safety margin when power telemetry is lost
            conservative_w = int(round(max(mode_spec.nominal_power_w, mode_spec.sustained_power_w) * 1.10))
            ginst.actual_power_override_w = conservative_w
            fallback_applied = f"CONSERVATIVE_POWER_RESERVE_{conservative_w}W"
    elif feed_name == "satcom_link":
        if status_label in ("STALE", "UNAVAILABLE"):
            work_state.satcom_available_bps = int(ship_profile["data_links"]["satcom"]["min_degraded_bps"])
            fallback_applied = f"CONSERVATIVE_MIN_SATCOM_{work_state.satcom_available_bps}BPS"
    else:
        raise StrictValidationError("UNKNOWN_ENUM", f"Unknown telemetry feed '{feed_name}'", "feed_name")

    snap = evaluate_eight_class_budgets(ship_profile, catalog, work_state)
    return {
        "feed_name": feed_name,
        "age_ms": age_ms,
        "feed_status": status_label,
        "fallback_policy_applied": fallback_applied,
        "budget_snapshot": snap,
        "resulting_state": work_state,
    }
