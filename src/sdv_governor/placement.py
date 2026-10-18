"""Combinatorial physical position and compute rack node placement solver (B4, PHY-05..10, ADM-01..04, H07)."""

from __future__ import annotations

from itertools import product
from typing import Any, Dict, List, Optional, Tuple

from .budgets import evaluate_eight_class_budgets, evaluate_roof_drone_clearance
from .capability_effects import effective_capability_retention
from .explanations import d2_why_not_chosen, d2_why_rejected
from .models import (
    GuestManifest,
    PriorityProfile,
    ROOF_DRONE_CLEARANCE_CONSTRAINT_ID,
    StrictValidationError,
)
from .ship_state import InstalledGuestState, ShipOperationalState

D2_OBJECTIVE_ID = "D2_LEXICOGRAPHIC_V2"
# Plan B of C4 (owner gate F6-G unavailable): scarcity sits after preference and retention so it only breaks ties
# and official placements stay intact. Order is part of the public contract (README D2 row).
D2_SORT_KEY_FIELDS: Tuple[str, ...] = (
    "roof_cond_rank",
    "pref_penalty",
    "-retention_milli",
    "scarcity_cost",
    "-min_margin_bucket",
    "stab_eq",
    "node_used_cores",
    "nid",
)
# ASSUMED (ASSUMPTIONS.yaml::D2_SCARCITY_WEIGHTS); hierarchy from brief A.3 ("the mast is the scarcest resource aboard")
D2_SCARCITY_WEIGHTS: Dict[str, int] = {
    "mast_1": 3,
    "mast_2": 3,
    "mast_3": 3,
    "hangar_roof": 2,
    "bay1": 2,
    "bay2": 2,
    "bay1_a": 1,
    "bay1_b": 1,
    "flight_deck": 2,
    "stern_ramp": 2,
    "hangar": 1,
    "rack": 0,
}
D2_MAX_ALTERNATIVES = 5
D2_MAX_REJECTED = 5
D2_REPORT_KEYS: Tuple[str, ...] = (
    "objective_id",
    "sort_key_fields",
    "chosen_key",
    "evaluated_count",
    "placement_alternatives",
    "rejected_placements",
)


def _scarcity_cost(positions: Tuple[str, ...]) -> int:
    return sum(D2_SCARCITY_WEIGHTS.get(p, 0) for p in set(positions))


def _min_margin_bucket(snap: Dict[str, Any]) -> int:
    """floor(200 x min_k free_k / capacity_k) over the budget classes: 0.5% buckets against float noise."""
    ratios = [
        c["free"] / c["degraded_capacity"] for c in snap["classes"].values() if c.get("degraded_capacity")
    ]
    return int(min(ratios) * 200) if ratios else 0


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
    best_feasible_key: Optional[Tuple[Any, ...]] = None

    best_infeasible: Optional[Dict[str, Any]] = None
    best_infeasible_key: Optional[Tuple[int, int]] = None

    # Lightweight records only (no snapshots) for the D2 alternatives report
    feasible_records: List[Tuple[Tuple[Any, ...], int, Tuple[str, ...], str]] = []
    rejected_records: List[Tuple[int, int, Tuple[str, ...], str, Dict[str, Any]]] = []

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
                retention, _ = effective_capability_retention(
                    candidate, trial_state.installed_guests[candidate.guest_id], trial_state, ship_profile
                )
                sort_key = (
                    roof_cond_rank,
                    pref_penalty,
                    -int(round(retention * 1000)),
                    _scarcity_cost(pos_tuple),
                    -_min_margin_bucket(snap),
                    stab_eq,
                    node_used_cores,
                    nid,
                )
                feasible_records.append((sort_key, len(feasible_records), pos_tuple, nid))
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
                rejected_records.append((w_count, pref_penalty, pos_tuple, nid, snap["binding_witnesses"][0]))
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

    sc_ids = [sc.subcomponent_id for sc in candidate.subcomponents]
    report = _d2_report(
        feasible_records,
        rejected_records,
        sc_ids,
        base_state,
        evaluated_count=len(feasible_records) + len(rejected_records),
    )
    if best_feasible is not None:
        best_feasible.update(report)
        return best_feasible
    assert best_infeasible is not None
    best_infeasible.update(report)
    return best_infeasible


def _occupant(position: str, occupied: Dict[str, str]) -> Optional[str]:
    """Guest holding `position`, counting a whole bay1 and its halves as overlapping slots."""
    if position in occupied:
        return occupied[position]
    if position == "bay1":
        return occupied.get("bay1_a") or occupied.get("bay1_b")
    if position in ("bay1_a", "bay1_b"):
        return occupied.get("bay1")
    return None


def _d2_report(
    feasible_records: List[Tuple[Tuple[Any, ...], int, Tuple[str, ...], str]],
    rejected_records: List[Tuple[int, int, Tuple[str, ...], str, Dict[str, Any]]],
    sc_ids: List[str],
    base_state: ShipOperationalState,
    *,
    evaluated_count: int,
) -> Dict[str, Any]:
    """D2 objective metadata, ranked feasible alternatives and rejected placements with their first witness."""
    fields = list(D2_SORT_KEY_FIELDS)
    out: Dict[str, Any] = {
        "objective_id": D2_OBJECTIVE_ID,
        "sort_key_fields": fields,
        "evaluated_count": evaluated_count,
        "placement_alternatives": [],
        "rejected_placements": [],
    }
    if feasible_records:
        # evaluation order (preferred positions first) breaks exact key ties, as the original strict '<' scan did
        ranked = sorted(feasible_records, key=lambda r: (r[0], r[1]))
        chosen_key = list(ranked[0][0])
        out["chosen_key"] = chosen_key
        seen = {ranked[0][2]}
        node_variants: Dict[Tuple[str, ...], int] = {}
        for _, _idx, pos, _nid in ranked:
            node_variants[pos] = node_variants.get(pos, 0) + 1
        alts: List[Tuple[Tuple[Any, ...], Tuple[str, ...], str]] = []
        for key, _idx, pos, nid in ranked[1:]:
            if pos in seen:
                continue
            seen.add(pos)
            alts.append((key, pos, nid))
        if not alts:
            # a single feasible position set: the alternatives are the other compute nodes for it
            alts = [(key, pos, nid) for key, _idx, pos, nid in ranked[1:]]
        for key, pos, nid in alts[:D2_MAX_ALTERNATIVES]:
            out["placement_alternatives"].append(
                {
                    "rank": len(out["placement_alternatives"]) + 2,
                    "subcomponent_positions": dict(zip(sc_ids, pos)),
                    "compute_node": nid,
                    "node_variants": node_variants[pos],
                    "key": list(key),
                    "why_not_chosen": d2_why_not_chosen(fields, chosen_key, list(key)),
                }
            )
    feasible_sets = {r[2] for r in feasible_records}
    occupied: Dict[str, str] = {}
    for gid, inst in sorted(base_state.installed_guests.items()):
        for pos in inst.placements.values():
            occupied.setdefault(pos, gid)
    best_by_pos: Dict[Tuple[str, ...], Tuple[int, int, str, Dict[str, Any]]] = {}
    for w_count, pref, pos, nid, witness in rejected_records:
        if pos in feasible_sets:
            continue
        cur = best_by_pos.get(pos)
        if cur is None or (w_count, nid) < (cur[0], cur[2]):
            best_by_pos[pos] = (w_count, pref, nid, witness)
    ranked_rej = sorted(best_by_pos.items(), key=lambda kv: (kv[1][0], kv[1][1], kv[0], kv[1][2]))
    for pos, (w_count, _pref, nid, witness) in ranked_rej[:D2_MAX_REJECTED]:
        occupants = {p: _occupant(p, occupied) for p in sorted(set(pos)) if _occupant(p, occupied)}
        out["rejected_placements"].append(
            {
                "subcomponent_positions": dict(zip(sc_ids, pos)),
                "compute_node": nid,
                "binding": {
                    "resource_class": witness["resource_class"],
                    "constraint_id": witness["constraint_id"],
                    "margin": witness["margin"],
                },
                "witness_count": w_count,
                "why_rejected": d2_why_rejected(witness, occupants),
            }
        )
    return out


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
