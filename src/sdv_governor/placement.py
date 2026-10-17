"""Combinatorial physical position and compute rack node placement solver (B4, PHY-05..10, ADM-01..04, H07)."""

from __future__ import annotations

from itertools import product
from typing import Any, Dict, List, Optional, Tuple

from .budgets import evaluate_eight_class_budgets
from .models import GuestManifest, PriorityProfile
from .ship_state import InstalledGuestState, ShipOperationalState


def compute_guest_capability_score(
    guest: GuestManifest,
    mode_name: str,
    priority_profile: PriorityProfile,
) -> float:
    """Compute weighted mission capability score for `guest` operating in `mode_name` under `priority_profile`."""
    retention = guest.modes[mode_name].capability_retention_ratio
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
    best_infeasible_witness_count: int = 999999

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
        uses_hangar_roof = "hangar_roof" in pos_tuple
        g4_present = "G4" in base_state.installed_guests or candidate.guest_id == "G4"
        requires_roof_clearance_condition = uses_hangar_roof and g4_present

        for nid in node_ids:
            trial_state = base_state.clone()
            trial_state.installed_guests[candidate.guest_id] = InstalledGuestState(
                guest_id=candidate.guest_id,
                active_mode=target_mode,
                powered_on=True,
                placements=placements_map,
                compute_node=nid,
            )
            snap = evaluate_eight_class_budgets(
                ship_profile,
                aug_catalog,
                trial_state,
                hangar_roof_clearance_verified=hangar_roof_clearance_verified,
            )
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
                        "budget_snapshot": snap,
                        "binding_witnesses": [],
                    }
            else:
                w_count = len(snap["binding_witnesses"])
                if best_infeasible is None or w_count < best_infeasible_witness_count:
                    best_infeasible_witness_count = w_count
                    best_infeasible = {
                        "feasible": False,
                        "guest_id": candidate.guest_id,
                        "initial_mode": target_mode,
                        "subcomponent_positions": placements_map,
                        "compute_node": nid,
                        "requires_hangar_roof_clearance_condition": requires_roof_clearance_condition,
                        "budget_snapshot": snap,
                        "binding_witnesses": snap["binding_witnesses"],
                    }

    if best_feasible is not None:
        return best_feasible
    assert best_infeasible is not None
    return best_infeasible
