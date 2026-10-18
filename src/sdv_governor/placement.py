"""Combinatorial physical position and compute rack node placement solver (B4, PHY-05..10, ADM-01..04, H07)."""

from __future__ import annotations

from itertools import product
from typing import Any, Dict, List, Optional, Tuple

from .budgets import evaluate_eight_class_budgets, evaluate_roof_drone_clearance
from .models import (
    GuestManifest,
    PriorityProfile,
    ROOF_DRONE_CLEARANCE_CONSTRAINT_ID,
    StrictValidationError,
)
from .ship_state import InstalledGuestState, ShipOperationalState


def compute_guest_capability_score(
    guest: GuestManifest,
    mode_name: str,
    priority_profile: PriorityProfile,
    retention_override: Optional[float] = None,
) -> float:
    """Compute weighted mission capability score for `guest` operating in `mode_name` under `priority_profile`.

    `retention_override` carries the retention after environmental effects (C6) when the caller knows the placement.
    """
    retention = guest.modes[mode_name].capability_retention_ratio if retention_override is None else retention_override
    raw_score = 0.0
    for cap_key, cap_val in guest.mission_capabilities.items():
        weight = priority_profile.capability_weights.get(cap_key, 10.0)
        raw_score += cap_val * (weight / 100.0)
    return round(raw_score * retention, 4)


def find_feasible_placement(
    candidate: GuestManifest,
    base_state: ShipOperationalState,
    ship_profile: Dict[str, Any],
    catalog: Dict[str, GuestManifest],
    *,
    mode_override: Optional[str] = None,
    hangar_roof_clearance_verified: bool = True,
) -> Dict[str, Any]:
    """Search all valid subcomponent position combinations and rack compute nodes for `candidate`.

    Prioritizes `preferred_positions` before fallback `allowed_positions`.
    Returns the best feasible placement (minimizing stability penalty and balancing rack node cores),
    or the least-violating placement with its binding constraint witnesses if none is feasible.
    """
    target_mode = mode_override or candidate.default_mode
    aug_catalog = dict(catalog)
    aug_catalog[candidate.guest_id] = candidate

    # Order position choices per subcomponent: preferred_positions first, then remaining allowed_positions
    ordered_pos_choices: List[List[str]] = []
    for sc in candidate.subcomponents:
        prefs = list(sc.preferred_positions)
        rest = [p for p in sc.allowed_positions if p not in prefs]
        ordered_pos_choices.append(prefs + rest)

    node_ids = sorted(ship_profile["compute_rack"]["nodes"].keys())

    best_feasible: Optional[Dict[str, Any]] = None
    best_feasible_key: Optional[Tuple[int, int, float, int, str]] = None

    best_infeasible: Optional[Dict[str, Any]] = None
    best_infeasible_key: Optional[Tuple[int, int]] = None

    for pos_tuple in product(*ordered_pos_choices):
        placements_map = {
            sc.subcomponent_id: pos_tuple[idx]
            for idx, sc in enumerate(candidate.subcomponents)
        }
        # Preference penalty: 0 if all subcomponents are in their first preferred position
        pref_penalty = sum(
            0 if pos_tuple[idx] == sc.preferred_positions[0] else (1 if pos_tuple[idx] in sc.preferred_positions else 2)
            for idx, sc in enumerate(candidate.subcomponents)
        )

        for nid in node_ids:
            trial_state = base_state.clone()
            trial_state.installed_guests[candidate.guest_id] = InstalledGuestState(
                guest_id=candidate.guest_id,
                active_mode=target_mode,
                powered_on=True,
                placements=placements_map,
                compute_node=nid,
            )
            roof_eval = evaluate_roof_drone_clearance(
                trial_state,
                ship_profile,
                aug_catalog,
                hangar_roof_clearance_verified=hangar_roof_clearance_verified,
            )
            requires_roof_clearance_condition = bool(roof_eval["requires_clearance"])
            snap = evaluate_eight_class_budgets(
                ship_profile,
                aug_catalog,
                trial_state,
                hangar_roof_clearance_verified=hangar_roof_clearance_verified,
            )
            roof_eval_snap = snap.get("roof_clearance_evaluation", roof_eval)
            if snap["feasible"]:
                stab_eq = float(
                    snap["classes"]["mass_stability"]["local_limits"]["stability_equivalent_kg"]["actual_kg_eq"]
                )
                node_used_cores = int(
                    snap["classes"]["compute"]["local_limits"]["nodes"][nid]["used_cores"]
                )
                # Prefer placements that do not require conditional hangar_roof clearance over those that do
                roof_cond_rank = 1 if requires_roof_clearance_condition else 0
                sort_key = (roof_cond_rank, pref_penalty, stab_eq, node_used_cores, nid)
                if best_feasible_key is None or sort_key < best_feasible_key:
                    best_feasible_key = sort_key
                    best_feasible = {
                        "feasible": True,
                        "guest_id": candidate.guest_id,
                        "initial_mode": target_mode,
                        "subcomponent_positions": placements_map,
                        "compute_node": nid,
                        "requires_hangar_roof_clearance_condition": requires_roof_clearance_condition,
                        "roof_clearance_constraint_id": ROOF_DRONE_CLEARANCE_CONSTRAINT_ID,
                        "roof_clearance_evaluation": roof_eval_snap,
                        "budget_snapshot": snap,
                        "binding_witnesses": [],
                    }
            else:
                w_count = len(snap["binding_witnesses"])
                infeasible_key = (w_count, pref_penalty)
                if best_infeasible is None or best_infeasible_key is None or infeasible_key < best_infeasible_key:
                    best_infeasible_key = infeasible_key
                    best_infeasible = {
                        "feasible": False,
                        "guest_id": candidate.guest_id,
                        "initial_mode": target_mode,
                        "subcomponent_positions": placements_map,
                        "compute_node": nid,
                        "requires_hangar_roof_clearance_condition": requires_roof_clearance_condition,
                        "roof_clearance_constraint_id": ROOF_DRONE_CLEARANCE_CONSTRAINT_ID,
                        "roof_clearance_evaluation": roof_eval_snap,
                        "budget_snapshot": snap,
                        "binding_witnesses": snap["binding_witnesses"],
                    }

    if best_feasible is not None:
        return best_feasible
    assert best_infeasible is not None
    return best_infeasible


def evaluate_antenna_relocation_port(
    state: ShipOperationalState,
    ship_profile: Dict[str, Any],
    catalog: Dict[str, GuestManifest],
    target_position: str = "mast_3",
    *,
    guest_id: str = "G5",
    subcomponent_id: str = "G5_MAST_ANTENNA",
    at_port: bool = True,
    hangar_roof_clearance_verified: Optional[bool] = None,
) -> Dict[str, Any]:
    """Evaluate relocating a mast/roof antenna (e.g. `G5_MAST_ANTENNA`) between `hangar_roof` and `mast_3` at port (ALIGN-T08).

    Simultaneously updates:
    - Subcomponent stability equivalent (`30.0 kg-eq` on `hangar_roof` at `1.2x` <-> `75.0 kg-eq` on `mast_3` at `3.0x`)
    - Local feeder line (`LINE_B_AFT` for `hangar_roof` <-> `LINE_A_FWD` for `mast_3`)
    - Air cooling domain (`hangar_roof` <-> `mast_3`)
    - Roof drone clearance status (`VERIFIED_COMPATIBLE` / `PENDING_VERIFICATION` <-> `NOT_APPLICABLE`)
    """
    if guest_id not in catalog:
        raise StrictValidationError("UNKNOWN_GUEST", f"Guest '{guest_id}' not found in catalog", f"relocation.{guest_id}")
    if guest_id not in state.installed_guests:
        raise StrictValidationError("GUEST_NOT_INSTALLED", f"Guest '{guest_id}' is not installed", f"relocation.{guest_id}")

    manifest = catalog[guest_id]
    sc_spec = next((s for s in manifest.subcomponents if s.subcomponent_id == subcomponent_id), None)
    if sc_spec is None:
        raise StrictValidationError(
            "UNKNOWN_SUBCOMPONENT",
            f"Subcomponent '{subcomponent_id}' not found in guest '{guest_id}'",
            f"relocation.{guest_id}.{subcomponent_id}",
        )
    if target_position not in sc_spec.allowed_positions:
        raise StrictValidationError(
            "INVALID_POSITION",
            f"Target position '{target_position}' not in allowed_positions {sc_spec.allowed_positions} for '{subcomponent_id}'",
            f"relocation.{guest_id}.{subcomponent_id}",
        )

    before_snap = evaluate_eight_class_budgets(
        ship_profile,
        catalog,
        state,
        hangar_roof_clearance_verified=hangar_roof_clearance_verified,
    )
    from_position = state.installed_guests[guest_id].placements.get(
        subcomponent_id, sc_spec.preferred_positions[0]
    )

    after_state = state.clone()
    after_state.installed_guests[guest_id].placements[subcomponent_id] = target_position
    if target_position == "hangar_roof" and hangar_roof_clearance_verified:
        after_state.stamp_roof_clearance_verification(ship_profile)

    after_snap = evaluate_eight_class_budgets(
        ship_profile,
        catalog,
        after_state,
        hangar_roof_clearance_verified=hangar_roof_clearance_verified,
    )

    crew_req = manifest.installation.antenna_min_crew_required or manifest.installation.min_crew_required
    dur_min = manifest.installation.antenna_install_time_min or manifest.installation.install_time_min
    permitted = bool(at_port and after_snap["feasible"] and state.available_crew >= crew_req)
    if not at_port:
        verdict = "NOT_NOW"
    elif permitted:
        verdict = "YES"
    else:
        verdict = "NO"

    before_comp = before_snap["component_breakdown"].get(subcomponent_id, {})
    after_comp = after_snap["component_breakdown"].get(subcomponent_id, {})

    return {
        "feasible": bool(after_snap["feasible"]),
        "permitted": permitted,
        "verdict": verdict,
        "at_port": at_port,
        "guest_id": guest_id,
        "subcomponent_id": subcomponent_id,
        "from_position": from_position,
        "to_position": target_position,
        "crew_required": crew_req,
        "duration_min": dur_min,
        "before_component": before_comp,
        "after_component": after_comp,
        "before_stability_eq_kg": before_comp.get("stability_eq_kg"),
        "after_stability_eq_kg": after_comp.get("stability_eq_kg"),
        "before_total_stability_eq_kg": before_snap["classes"]["mass_stability"]["local_limits"]["stability_equivalent_kg"]["actual_kg_eq"],
        "after_total_stability_eq_kg": after_snap["classes"]["mass_stability"]["local_limits"]["stability_equivalent_kg"]["actual_kg_eq"],
        "before_feeder_line": before_comp.get("feeder_line"),
        "after_feeder_line": after_comp.get("feeder_line"),
        "before_air_cooling_domain": before_comp.get("air_cooling_domain"),
        "after_air_cooling_domain": after_comp.get("air_cooling_domain"),
        "before_roof_clearance_status": before_snap["roof_clearance_evaluation"]["status"],
        "after_roof_clearance_status": after_snap["roof_clearance_evaluation"]["status"],
        "before_snapshot": before_snap,
        "after_snapshot": after_snap,
        "binding_witnesses": after_snap["binding_witnesses"],
        "resulting_state": after_state,
    }
