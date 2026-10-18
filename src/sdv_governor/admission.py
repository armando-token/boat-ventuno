"""Scenario A (D1) Admission, Placement, Mission-Envelope A.10, and Trade-off Solver (B4, ONT-01..02, ADM-01..08, H01, H07, H08, H13)."""

from __future__ import annotations

from itertools import combinations
import time
from typing import Any, Dict, List, Optional, Tuple

from .acoustic import guests_requiring_acoustic_command_link
from .budgets import evaluate_eight_class_budgets
from .explanations import build_decision_rationale, logical_reconfiguration_sentence
from .reconfiguration import propose_logical_reconfiguration
from .models import (
    StrictValidationError,
    BindingConstraintWitness,
    GuestManifest,
    PriorityProfile,
    canonical_json_hash,
)
from .capability_effects import effective_capability_retention
from .placement import D2_REPORT_KEYS, compute_guest_capability_score, find_feasible_placement
from .scheduling import evaluate_a_prime_window, evaluate_roof_drone_clearance_safe
from .ship_state import InstalledGuestState, ShipOperationalState


def check_dependency_constraints(
    candidate: GuestManifest,
    state: ShipOperationalState,
    catalog: Dict[str, GuestManifest],
    *,
    candidate_mode: Optional[str] = None,
) -> Tuple[List[BindingConstraintWitness], List[str]]:
    """Verify bidirectional `requires_guests`, `excludes_guests`, and `conditional_excludes` (ONT-01, ONT-02, H08).

    Returns `(hard_witnesses, conditional_conflict_notes)`.
    """
    witnesses: List[BindingConstraintWitness] = []
    cond_notes: List[str] = []
    installed_ids = set(state.installed_guests.keys())
    cand_mode = candidate_mode or candidate.default_mode

    # 1. requires_guests: every required guest must be currently installed
    for req_gid in candidate.dependencies.requires_guests:
        if req_gid not in installed_ids:
            witnesses.append(
                BindingConstraintWitness(
                    resource_class="dependencies",
                    constraint_id=f"requires_guest_{req_gid}",
                    state_label=f"T+{state.sim_time_s}s:DEPENDENCY_CHECK",
                    demand=1,
                    capacity=0,
                    margin=-1,
                    unit="guest",
                    provenance="DERIVED",
                    detail=f"Candidate '{candidate.guest_id}' requires guest '{req_gid}' which is not installed",
                )
            )

    # 2. Bidirectional excludes_guests: neither candidate nor any installed guest may exclude the other
    for exc_gid in candidate.dependencies.excludes_guests:
        if exc_gid in installed_ids:
            witnesses.append(
                BindingConstraintWitness(
                    resource_class="dependencies",
                    constraint_id=f"excludes_guest_{exc_gid}",
                    state_label=f"T+{state.sim_time_s}s:EXCLUSION_CHECK",
                    demand=1,
                    capacity=0,
                    margin=-1,
                    unit="conflict",
                    provenance="DERIVED",
                    detail=f"Candidate '{candidate.guest_id}' explicitly excludes installed guest '{exc_gid}'",
                )
            )

    for inst_gid in sorted(installed_ids):
        if inst_gid in catalog:
            inst_man = catalog[inst_gid]
            if candidate.guest_id in inst_man.dependencies.excludes_guests:
                witnesses.append(
                    BindingConstraintWitness(
                        resource_class="dependencies",
                        constraint_id=f"excluded_by_installed_{inst_gid}",
                        state_label=f"T+{state.sim_time_s}s:EXCLUSION_CHECK",
                        demand=1,
                        capacity=0,
                        margin=-1,
                        unit="conflict",
                        provenance="DERIVED",
                        detail=f"Installed guest '{inst_gid}' explicitly excludes candidate '{candidate.guest_id}'",
                    )
                )

    # 3. Bidirectional conditional_excludes (H08): check active/default mode conflicts in both directions
    def _has_non_conflicting_fallback(g_man: GuestManifest, forbidden_modes: List[str]) -> Optional[str]:
        for m_name in g_man.modes:
            if m_name not in forbidden_modes:
                return m_name
        return None

    for rule in candidate.dependencies.conditional_excludes:
        other_id = str(rule.get("other_guest_id", ""))
        self_modes = list(rule.get("when_self_modes", []))
        other_modes = list(rule.get("when_other_modes", []))
        reason = str(rule.get("reason", "conditional exclusion"))
        if other_id in state.installed_guests and state.installed_guests[other_id].powered_on:
            other_active = state.installed_guests[other_id].active_mode
            if (not self_modes or cand_mode in self_modes) and (not other_modes or other_active in other_modes):
                alt_self = _has_non_conflicting_fallback(candidate, self_modes) if self_modes else None
                alt_other = _has_non_conflicting_fallback(catalog[other_id], other_modes) if (other_id in catalog and other_modes) else None
                if alt_self is None and alt_other is None:
                    witnesses.append(
                        BindingConstraintWitness(
                            resource_class="dependencies",
                            constraint_id=f"conditional_excludes_{candidate.guest_id}_{other_id}",
                            state_label=f"T+{state.sim_time_s}s:CONDITIONAL_EXCLUSION",
                            demand=1,
                            capacity=0,
                            margin=-1,
                            unit="mode_conflict",
                            provenance="DERIVED",
                            detail=f"Unresolvable conditional exclusion between {candidate.guest_id}({cand_mode}) and {other_id}({other_active}): {reason}",
                        )
                    )
                else:
                    cond_notes.append(
                        f"Time-share / mode lock required between {candidate.guest_id}({cand_mode}) and {other_id}({other_active}): {reason}"
                    )

    for inst_gid in sorted(installed_ids):
        if inst_gid not in catalog or not state.installed_guests[inst_gid].powered_on:
            continue
        inst_man = catalog[inst_gid]
        inst_active = state.installed_guests[inst_gid].active_mode
        for rule in inst_man.dependencies.conditional_excludes:
            other_id = str(rule.get("other_guest_id", ""))
            if other_id != candidate.guest_id:
                continue
            self_modes = list(rule.get("when_self_modes", []))
            other_modes = list(rule.get("when_other_modes", []))
            reason = str(rule.get("reason", "conditional exclusion"))
            if (not self_modes or inst_active in self_modes) and (not other_modes or cand_mode in other_modes):
                alt_inst = _has_non_conflicting_fallback(inst_man, self_modes) if self_modes else None
                alt_cand = _has_non_conflicting_fallback(candidate, other_modes) if other_modes else None
                if alt_inst is None and alt_cand is None:
                    witnesses.append(
                        BindingConstraintWitness(
                            resource_class="dependencies",
                            constraint_id=f"conditional_excluded_by_{inst_gid}_{candidate.guest_id}",
                            state_label=f"T+{state.sim_time_s}s:CONDITIONAL_EXCLUSION",
                            demand=1,
                            capacity=0,
                            margin=-1,
                            unit="mode_conflict",
                            provenance="DERIVED",
                            detail=f"Unresolvable conditional exclusion by {inst_gid}({inst_active}) against {candidate.guest_id}({cand_mode}): {reason}",
                        )
                    )
                else:
                    note = f"Time-share / mode lock required between {inst_gid}({inst_active}) and {candidate.guest_id}({cand_mode}): {reason}"
                    if note not in cond_notes:
                        cond_notes.append(note)

    return witnesses, cond_notes


SINGLE_GENSET_LOSS = "SINGLE_GENSET_LOSS"


def _genset_down_sentence(report: Dict[str, Any]) -> str:
    head = (
        f"With one genset down ({report['power_reserve_w'] / 1000.0:g} kW reserve, "
        f"{report['seawater_temp_c']:g} \u00b0C water) "
    )
    if report["holds"] == "YES":
        return head + "the verdict holds without changes."
    if report["holds"] == "YES_WITH_STEP_DOWN":
        steps = ", ".join(
            f"{sd['guest_id']} steps down to {sd['to_mode']}" for sd in report["required_step_downs"]
        )
        return head + f"the verdict holds only if {steps}."
    return head + f"the verdict does NOT hold (binding class: {report['binding_class']})."


def _modes_step_down_diff(
    before: ShipOperationalState, after: ShipOperationalState, aug_catalog: Dict[str, GuestManifest]
) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    for gid in sorted(before.installed_guests):
        m_from = before.installed_guests[gid].active_mode
        m_to = after.installed_guests[gid].active_mode
        if m_from == m_to:
            continue
        g_man = aug_catalog[gid]
        r_from = g_man.modes[m_from].capability_retention_ratio
        r_to = g_man.modes[m_to].capability_retention_ratio
        caps = ", ".join(sorted(g_man.mission_capabilities.keys())) or "none"
        steps.append(
            {
                "guest_id": gid,
                "from_mode": m_from,
                "to_mode": m_to,
                "capability_lost": f"{caps} (retention {r_from:.2f} -> {r_to:.2f})",
            }
        )
    return steps


def _minimize_step_downs(
    before: ShipOperationalState,
    stepped: ShipOperationalState,
    ship_profile: Dict[str, Any],
    aug_catalog: Dict[str, GuestManifest],
    hangar_roof_clearance_verified: bool,
) -> ShipOperationalState:
    """Greedily restore original modes (largest capability loss first, then guest_id) while the window stays feasible."""
    work = stepped.clone()

    def _loss(gid: str) -> float:
        g_man = aug_catalog[gid]
        return (
            g_man.modes[before.installed_guests[gid].active_mode].capability_retention_ratio
            - g_man.modes[stepped.installed_guests[gid].active_mode].capability_retention_ratio
        )

    changed = [
        gid
        for gid in before.installed_guests
        if before.installed_guests[gid].active_mode != stepped.installed_guests[gid].active_mode
    ]
    for gid in sorted(changed, key=lambda g: (-_loss(g), g)):
        trial = work.clone()
        trial.installed_guests[gid].active_mode = before.installed_guests[gid].active_mode
        snap = evaluate_eight_class_budgets(
            ship_profile, aug_catalog, trial, hangar_roof_clearance_verified=hangar_roof_clearance_verified
        )
        if snap["feasible"]:
            work = trial
    return work


def evaluate_mission_envelope_a10(
    candidate: GuestManifest,
    trial_state: ShipOperationalState,
    ship_profile: Dict[str, Any],
    mission_envelope: Dict[str, Any],
    catalog: Dict[str, GuestManifest],
    *,
    hangar_roof_clearance_verified: bool = True,
) -> Dict[str, Any]:
    """Evaluate `trial_state` (including `candidate`) across the 14-day Mission Envelope A.10 (ADM-03, H01).

    Never approves a guest based solely on comfortable pier conditions (16 °C).
    Distinguishes:
    1. Normal offshore operational windows (`NOMINAL_CRUISE_24C`, `HOT_WATER_SURVEY_30C`):
       - Must be feasible in nominal/operational mode for unconditional `YES`.
       - If nominal mode fails in `HOT_WATER_SURVEY_30C` (e.g. >120 kW cooling), checks if candidate has a lower
         declared mode that fits (`conditioned_mode_required`), which forces `YES_IF` rather than `YES`.
       - If no declared mode of the candidate + installed set fits a required mission window, `envelope_feasible=False`
         and the binding witnesses from that window block admission (`NO` or displacement required).
    2. Heavy-weather / degraded contingency windows (`HIGH_SEA_STATE_5_WIND_28KN`, `DEGRADED_GEN_LOSS_100KW_30C`,
       `DEGRADED_COOLING_LOOP_72KW_30C`):
       - Checks weather/sea-state limits and verifies that the installed + candidate guests can survive the
         degraded window (either in nominal mode or by stepping down non-protected guests to their declared
         degraded/standby modes). If even minimum declared modes exceed a degraded window, `envelope_feasible=False`!
    """
    aug_catalog = dict(catalog)
    aug_catalog[candidate.guest_id] = candidate

    windows = mission_envelope.get("envelope_states", mission_envelope.get("windows", []))
    window_reports: List[Dict[str, Any]] = []
    restricted_windows: List[Dict[str, str]] = []
    envelope_binding_witnesses: List[Dict[str, Any]] = []
    nominal_offshore_mode_conditions: List[str] = []
    nominal_offshore_condition_classes: Dict[str, str] = {}
    envelope_feasible = True
    one_genset_down: Optional[Dict[str, Any]] = None

    for w in windows:
        wid = w.get("state_id", w.get("window_id", "UNKNOWN_WINDOW"))
        if w.get("is_pier_only", False):
            continue

        w_state = trial_state.clone()
        w_state.vessel_mode = str(w.get("vessel_mode", "CRUISE"))
        spd = float(w.get("ship_speed_kn", w.get("speed_kn", 8.0)))
        w_state.ship_speed_kn = spd
        w_state.speed_kn = spd
        w_state.wind_kn = int(w.get("wind_kn", 15))
        w_state.sea_state = int(w.get("sea_state", 3))
        w_state.seawater_temp_c = float(w.get("seawater_temp_c", 24.0))
        w_state.power_reserve_w = int(w.get("power_reserve_w", 200000))
        w_state.cooling_loop_degraded = bool(w.get("cooling_loop_degraded", False))
        w_state.satcom_available_bps = int(w.get("satcom_available_bps", 6000000))
        w_state.emcon_silence = bool(w.get("emcon_silence", False))
        raw_layered = w.get("water_column_layered", False)
        if not isinstance(raw_layered, bool):
            raise StrictValidationError(
                "TYPE_ERROR", "water_column_layered must be a boolean", f"envelope_states.{wid}.water_column_layered"
            )
        w_state.water_column_layered = raw_layered

        is_degraded_or_heavy_window = (
            w_state.cooling_loop_degraded
            or w_state.power_reserve_w < 200000
            or w_state.sea_state >= 5
            or w_state.wind_kn > 25
        )

        # Check weather/sea-state conditioning on candidate
        sc_con = candidate.ship_state_constraints
        if w_state.sea_state > sc_con.max_sea_state_for_operation or w_state.wind_kn > sc_con.max_wind_kn_for_operation:
            standby_mode = "STANDBY" if "STANDBY" in candidate.modes else (
                "ECO_SAMPLE" if "ECO_SAMPLE" in candidate.modes else candidate.default_mode
            )
            restricted_windows.append(
                {
                    "window_id": wid,
                    "restriction": (
                        f"Candidate {candidate.guest_id} active ops suspended in sea_state={w_state.sea_state}/wind={w_state.wind_kn}kn "
                        f"(transitions to {standby_mode})"
                    ),
                }
            )
            w_state.installed_guests[candidate.guest_id].active_mode = standby_mode

        snap = evaluate_eight_class_budgets(
            ship_profile,
            aug_catalog,
            w_state,
            hangar_roof_clearance_verified=hangar_roof_clearance_verified,
        )

        window_passed = snap["feasible"]
        conditioned_for_window = False
        is_genset_window = w.get("represents") == SINGLE_GENSET_LOSS
        genset_binding_class: Optional[str] = None
        if is_genset_window and not window_passed and snap["binding_witnesses"]:
            genset_binding_class = str(snap["binding_witnesses"][0]["resource_class"])
        genset_step_downs: List[Dict[str, Any]] = []

        if not window_passed:
            # Try stepping down candidate (and, in degraded contingency windows, other non-protected guests)
            # to their lowest-power declared operational/standby mode to see if a valid conditioned mode exists
            cond_state = w_state.clone()
            cand_modes_sorted = sorted(
                candidate.modes.values(),
                key=lambda m: (m.sustained_power_w, m.cooling_water_w, m.satcom_bps),
            )
            best_cand_mode = cand_modes_sorted[0].mode_name
            if best_cand_mode != cond_state.installed_guests[candidate.guest_id].active_mode:
                cond_state.installed_guests[candidate.guest_id].active_mode = best_cand_mode

            if is_degraded_or_heavy_window:
                for gid, ginst in sorted(cond_state.installed_guests.items()):
                    g_man = aug_catalog[gid]
                    cur_m = g_man.modes[ginst.active_mode]
                    if g_man.safety_critical or cur_m.safety_critical or cur_m.uninterruptible:
                        continue
                    g_modes_sorted = sorted(
                        g_man.modes.values(),
                        key=lambda m: (m.sustained_power_w, m.cooling_water_w, m.satcom_bps),
                    )
                    ginst.active_mode = g_modes_sorted[0].mode_name

            snap_cond = evaluate_eight_class_budgets(
                ship_profile,
                aug_catalog,
                cond_state,
                hangar_roof_clearance_verified=hangar_roof_clearance_verified,
            )
            if snap_cond["feasible"]:
                window_passed = True
                conditioned_for_window = True
                if is_genset_window:
                    min_state = _minimize_step_downs(
                        w_state, cond_state, ship_profile, aug_catalog, hangar_roof_clearance_verified
                    )
                    genset_step_downs = _modes_step_down_diff(w_state, min_state, aug_catalog)
                cond_mode_used = cond_state.installed_guests[candidate.guest_id].active_mode
                restricted_windows.append(
                    {
                        "window_id": wid,
                        "restriction": f"Requires degraded/step-down mode ({candidate.guest_id}->{cond_mode_used}) to satisfy {wid} budget",
                    }
                )
                if not is_degraded_or_heavy_window:
                    cond_txt = f"Window {wid} requires {candidate.guest_id} mode step-down to {cond_mode_used}"
                    nominal_offshore_mode_conditions.append(cond_txt)
                    failing = sorted({str(bw["resource_class"]) for bw in snap["binding_witnesses"]})
                    nominal_offshore_condition_classes[cond_txt] = "+".join(failing) if failing else "unknown"
                snap = snap_cond
            else:
                envelope_feasible = False
                for bw in snap["binding_witnesses"]:
                    bw_copy = dict(bw)
                    bw_copy["state_label"] = f"ENVELOPE:{wid}"
                    envelope_binding_witnesses.append(bw_copy)

        if is_genset_window:
            one_genset_down = {
                "window_id": wid,
                "power_reserve_w": w_state.power_reserve_w,
                "seawater_temp_c": w_state.seawater_temp_c,
                "holds": (
                    "NO" if not window_passed else ("YES_WITH_STEP_DOWN" if conditioned_for_window else "YES")
                ),
                "required_step_downs": genset_step_downs,
                "binding_class": genset_binding_class,
            }
            one_genset_down["sentence"] = _genset_down_sentence(one_genset_down)

        window_report = {
            "window_id": wid,
            "seawater_temp_c": w_state.seawater_temp_c,
            "sea_state": w_state.sea_state,
            "wind_kn": w_state.wind_kn,
            "feasible": window_passed,
            "conditioned": conditioned_for_window,
            "power_free_w": snap["classes"]["power"]["free"],
            "cooling_free_w": snap["classes"]["cooling"]["free"],
            "binding_witnesses": snap["binding_witnesses"],
        }
        if w_state.water_column_layered and guests_requiring_acoustic_command_link(w_state, aug_catalog):
            # Informational only (A.5): the verdict is unchanged; the guest needs its autonomous lost-link behaviour.
            window_report["acoustic_link_note"] = (
                "LAYERED_WATER: acoustic command link unavailable; acoustic-commanded guests rely on autonomous timeout hold (ASSUMED)"
            )
        window_reports.append(window_report)

    return {
        "envelope_id": mission_envelope.get("envelope_id", "A10_14DAY_OFFSHORE_ENVELOPE"),
        "envelope_feasible": envelope_feasible,
        "nominal_offshore_mode_conditions": nominal_offshore_mode_conditions,
        "nominal_offshore_condition_classes": nominal_offshore_condition_classes,
        "envelope_binding_witnesses": envelope_binding_witnesses,
        "conditioned": len(restricted_windows) > 0,
        "restricted_windows": restricted_windows,
        "window_evaluations": window_reports,
        "one_genset_down": one_genset_down,
    }


def evaluate_admission(
    candidate: GuestManifest,
    state: ShipOperationalState,
    ship_profile: Dict[str, Any],
    mission_envelope: Dict[str, Any],
    priority_profile: PriorityProfile,
    catalog: Dict[str, GuestManifest],
    *,
    at_port: bool = True,
    allow_displacement: bool = True,
    defer_if_temporary_operational_block: bool = False,
    next_window: Optional[Dict[str, Any]] = None,
    hangar_roof_clearance_verified: bool = False,
) -> Dict[str, Any]:
    """Evaluate Scenario A (D1) admission for `candidate` and return one of `YES`, `YES_IF`, `NO`, `NOT_NOW` (<=30 s).

    Strictly propagates 14-day Mission Envelope A.10 feasibility into the final verdict (`H01`),
    enforces local bay/hangar/ramp/roof constraints (`H07`), and checks bidirectional dependencies (`H08`).
    """
    t0_ns = time.perf_counter_ns()
    aug_catalog = dict(catalog)
    aug_catalog[candidate.guest_id] = candidate

    # 1. Check at-sea vs port-only / A' installation constraints first (can yield NOT_NOW or NO)
    if not at_port:
        if state.crew_count == 0 or state.crew_regime == "UNCREWED_0":
            witness = BindingConstraintWitness(
                resource_class="positions",
                constraint_id="uncrewed_0_forbids_physical_install_at_sea",
                state_label=f"T+{state.sim_time_s}s:AT_SEA_CREW_0",
                demand=max(1, candidate.installation.min_crew_required),
                capacity=0,
                margin=-max(1, candidate.installation.min_crew_required),
                unit="crew",
                provenance="DERIVED",
                detail="Scenario C rule: 0 crew at sea forbids any physical guest installation or manual swap",
            )
            elapsed_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 3)
            rationale = build_decision_rationale("NO", candidate.guest_id, [witness], {}, [], [])
            logical = propose_logical_reconfiguration(candidate, state, ship_profile, catalog, priority_profile)
            rationale += " " + logical_reconfiguration_sentence(candidate.guest_id, logical)
            decision = _format_admission_decision(
                verdict="NO",
                candidate=candidate,
                placement={},
                binding_constraints=[witness],
                ranked_displacements=[],
                capability_lost=[],
                conditioned_availability={"conditioned": False, "restricted_windows": []},
                next_window=None,
                rationale=rationale,
                elapsed_ms=elapsed_ms,
                ship_profile=ship_profile,
                mission_envelope=mission_envelope,
                priority_profile=priority_profile,
            )
            decision["alternative_action"] = "LOGICAL_RECONFIGURATION"
            decision["logical_reconfiguration"] = logical
            return decision

        a_prime_check = evaluate_a_prime_window(candidate, "AT_SEA_INSTALL", state, aug_catalog)
        if not a_prime_check["permitted"]:
            witness = BindingConstraintWitness(
                resource_class="time",
                constraint_id="a_prime_install_window_blocked",
                state_label=f"T+{state.sim_time_s}s:AT_SEA_WINDOW",
                demand=1,
                capacity=0,
                margin=-1,
                unit="window",
                provenance="DERIVED",
                detail="; ".join(a_prime_check["blocking_reasons"]),
            )
            verdict = "NOT_NOW" if defer_if_temporary_operational_block else "NO"
            elapsed_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 3)
            rationale = build_decision_rationale(
                verdict, candidate.guest_id, [witness], {}, [], [], next_window=next_window
            )
            return _format_admission_decision(
                verdict=verdict,
                candidate=candidate,
                placement={},
                binding_constraints=[witness],
                ranked_displacements=[],
                capability_lost=[],
                conditioned_availability={"conditioned": False, "restricted_windows": []},
                next_window=next_window if verdict == "NOT_NOW" else None,
                rationale=rationale,
                elapsed_ms=elapsed_ms,
                ship_profile=ship_profile,
                mission_envelope=mission_envelope,
                priority_profile=priority_profile,
            )

    # 2. Check bidirectional ontology / dependency constraints (requires_guests, excludes_guests, conditional_excludes)
    dep_witnesses, cond_excl_notes = check_dependency_constraints(candidate, state, aug_catalog)
    missing_req = [w for w in dep_witnesses if w.constraint_id.startswith("requires_guest_")]
    if missing_req:
        elapsed_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 3)
        rationale = build_decision_rationale("NO", candidate.guest_id, missing_req, {}, [], [])
        return _format_admission_decision(
            verdict="NO",
            candidate=candidate,
            placement={},
            binding_constraints=missing_req,
            ranked_displacements=[],
            capability_lost=[],
            conditioned_availability={"conditioned": False, "restricted_windows": []},
            next_window=None,
            rationale=rationale,
            elapsed_ms=elapsed_ms,
            ship_profile=ship_profile,
            mission_envelope=mission_envelope,
            priority_profile=priority_profile,
        )

    # 3. Try direct placement without displacing any currently installed guest
    # Note: we allow placement search to consider hangar_roof (hangar_roof_clearance_verified=True for geometry search),
    # and if the selected placement uses hangar_roof while G4 is present and hangar_roof_clearance_verified is False,
    # it produces a conditional YES_IF requiring drone clearance verification (Annex B G5 card, H07).
    base_placement = find_feasible_placement(
        candidate,
        state,
        ship_profile,
        aug_catalog,
        hangar_roof_clearance_verified=True,
    )

    if not dep_witnesses and base_placement["feasible"]:
        trial_state = state.clone()
        trial_state.installed_guests[candidate.guest_id] = InstalledGuestState(
            guest_id=candidate.guest_id,
            active_mode=base_placement["initial_mode"],
            powered_on=True,
            placements=base_placement["subcomponent_positions"],
            compute_node=base_placement["compute_node"],
        )
        eff_roof_ver = bool(
            hangar_roof_clearance_verified or getattr(state, "hangar_roof_clearance_verified", False)
        )
        roof_eval = evaluate_roof_drone_clearance_safe(
            trial_state,
            ship_profile,
            aug_catalog,
            hangar_roof_clearance_verified=eff_roof_ver,
        )
        env_eval = evaluate_mission_envelope_a10(
            candidate,
            trial_state,
            ship_profile,
            mission_envelope,
            aug_catalog,
            hangar_roof_clearance_verified=True,
        )

        if roof_eval.get("status") == "CONFLICT":
            raw_rw = roof_eval.get("witness") or {
                "resource_class": "positions",
                "constraint_id": "hangar_roof_drone_clearance",
                "state_label": f"T+{state.sim_time_s}s:ROOF_CLEARANCE_CONFLICT",
                "demand": 1,
                "capacity": 0,
                "margin": -1,
                "unit": "clearance",
                "provenance": "DERIVED",
                "detail": "hangar_roof antenna geometry conflicts with G4 VTOL recovery envelope",
            }
            all_binding_witnesses = [BindingConstraintWitness(**raw_rw)]
        elif env_eval["envelope_feasible"]:
            required_conditions: List[str] = []
            if (
                base_placement.get("requires_hangar_roof_clearance_condition") and not eff_roof_ver
            ) or not roof_eval.get("satisfied", True):
                req_cond = roof_eval.get("required_condition") or (
                    "Verify G4 VTOL drone rotor clearance over hangar_roof antenna (stability factor x1.2)"
                )
                required_conditions.append(req_cond)
            if env_eval.get("nominal_offshore_mode_conditions"):
                required_conditions.extend(env_eval["nominal_offshore_mode_conditions"])
            if cond_excl_notes:
                required_conditions.extend(cond_excl_notes)

            elapsed_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 3)
            if required_conditions:
                cond_classes = env_eval.get("nominal_offshore_condition_classes", {})

                def _condition_class(c: str) -> str:
                    if "hangar_roof" in c:
                        return "positions"
                    if c in cond_classes:
                        return cond_classes[c]
                    if c in cond_excl_notes:
                        return "dependencies"
                    return "unknown"

                cond_witnesses = [
                    BindingConstraintWitness(
                        resource_class=_condition_class(c),
                        constraint_id=(
                            "hangar_roof_drone_clearance"
                            if "hangar_roof" in c
                            else "conditional_admission_requirement"
                        ),
                        state_label=f"T+{state.sim_time_s}s:CONDITIONED_ADMISSION",
                        demand=1,
                        capacity=1,
                        margin=0,
                        unit="condition",
                        provenance="DERIVED",
                        detail=c,
                    )
                    for c in required_conditions
                ]
                rationale = build_decision_rationale(
                    "YES_IF",
                    candidate.guest_id,
                    cond_witnesses,
                    base_placement,
                    [],
                    [],
                    conditioned_availability=env_eval,
                    required_conditions=required_conditions,
                )
                return _format_admission_decision(
                    verdict="YES_IF",
                    candidate=candidate,
                    placement=base_placement,
                    binding_constraints=cond_witnesses,
                    ranked_displacements=[],
                    capability_lost=[],
                    conditioned_availability=env_eval,
                    next_window=None,
                    rationale=rationale,
                    elapsed_ms=elapsed_ms,
                    ship_profile=ship_profile,
                    mission_envelope=mission_envelope,
                    priority_profile=priority_profile,
                    required_conditions=required_conditions,
                )

            rationale = build_decision_rationale(
                "YES",
                candidate.guest_id,
                [],
                base_placement,
                [],
                [],
                conditioned_availability=env_eval,
            )
            return _format_admission_decision(
                verdict="YES",
                candidate=candidate,
                placement=base_placement,
                binding_constraints=[],
                ranked_displacements=[],
                capability_lost=[],
                conditioned_availability=env_eval,
                next_window=None,
                rationale=rationale,
                elapsed_ms=elapsed_ms,
                ship_profile=ship_profile,
                mission_envelope=mission_envelope,
                priority_profile=priority_profile,
            )
        else:
            # Mission Envelope A.10 failed even though pier placement succeeded (H01)!
            all_binding_witnesses = [
                BindingConstraintWitness(**w) for w in env_eval["envelope_binding_witnesses"]
            ]
    else:
        all_binding_witnesses = list(dep_witnesses) + [
            BindingConstraintWitness(**w) for w in base_placement["binding_witnesses"]
        ]

    # 4. Direct placement or Mission Envelope failed -> if allow_displacement=True, search for valid displacements (YES_IF)
    ranked_displacements: List[Dict[str, Any]] = []
    d2_by_drop: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    installed_ids = sorted(state.installed_guests.keys())
    cand_score = compute_guest_capability_score(candidate, candidate.default_mode, priority_profile)

    if allow_displacement and installed_ids:
        for k in range(1, min(3, len(installed_ids)) + 1):
            for drop_tuple in combinations(installed_ids, k):
                # Never displace a safety-critical guest or break a remaining guest's requires_guests
                if any(aug_catalog[gid].safety_critical for gid in drop_tuple):
                    continue
                remaining_ids = set(installed_ids) - set(drop_tuple)
                if any(req_g not in remaining_ids for req_g in candidate.dependencies.requires_guests):
                    continue
                if any(
                    any(req_g not in (remaining_ids | {candidate.guest_id}) for req_g in aug_catalog[rem_g].dependencies.requires_guests)
                    for rem_g in remaining_ids
                ):
                    continue

                sub_state = state.clone()
                for gid in drop_tuple:
                    sub_state.installed_guests.pop(gid, None)

                sub_dep_witnesses, _ = check_dependency_constraints(candidate, sub_state, aug_catalog)
                if sub_dep_witnesses:
                    continue

                sub_place = find_feasible_placement(
                    candidate,
                    sub_state,
                    ship_profile,
                    aug_catalog,
                    hangar_roof_clearance_verified=True,
                )
                if not sub_place["feasible"]:
                    continue
                d2_by_drop[tuple(drop_tuple)] = {k: sub_place[k] for k in D2_REPORT_KEYS if k in sub_place}

                # Also verify the 14-day Mission Envelope A.10 on the post-displacement state (H01)!
                env_trial = sub_state.clone()
                env_trial.installed_guests[candidate.guest_id] = InstalledGuestState(
                    guest_id=candidate.guest_id,
                    active_mode=sub_place["initial_mode"],
                    powered_on=True,
                    placements=sub_place["subcomponent_positions"],
                    compute_node=sub_place["compute_node"],
                )
                sub_env = evaluate_mission_envelope_a10(
                    candidate,
                    env_trial,
                    ship_profile,
                    mission_envelope,
                    aug_catalog,
                    hangar_roof_clearance_verified=True,
                )
                if not sub_env["envelope_feasible"]:
                    continue

                cap_lost_items: List[Dict[str, Any]] = []
                total_score_lost = 0.0
                cand_ret, _ = effective_capability_retention(
                    candidate,
                    env_trial.installed_guests[candidate.guest_id],
                    env_trial,
                    ship_profile,
                    mode_name=candidate.default_mode,
                )
                cand_score = compute_guest_capability_score(
                    candidate, candidate.default_mode, priority_profile, cand_ret
                )
                for gid in drop_tuple:
                    g_man = aug_catalog[gid]
                    orig_mode = state.installed_guests[gid].active_mode
                    eff_ret, _ = effective_capability_retention(
                        g_man, state.installed_guests[gid], state, ship_profile
                    )
                    sc_lost = compute_guest_capability_score(g_man, orig_mode, priority_profile, eff_ret)
                    total_score_lost += sc_lost
                    cap_lost_items.append(
                        {
                            "guest_id": gid,
                            "displaced_from_mode": orig_mode,
                            "score_lost": sc_lost,
                            "mass_freed_kg": g_man.total_mass_kg,
                            "capability_lost_description": f"{g_man.name} ({', '.join(sorted(g_man.mission_capabilities.keys()))})",
                        }
                    )

                ranked_displacements.append(
                    {
                        "action_type": "REMOVE_AT_PORT" if at_port else "DEACTIVATE_AND_REPLACE",
                        "displace_guest_ids": list(drop_tuple),
                        "displaced_count": len(drop_tuple),
                        "total_score_lost": round(total_score_lost, 4),
                        "candidate_score_gained": cand_score,
                        "net_capability_delta": round(cand_score - total_score_lost, 4),
                        "resulting_placement": {
                            "initial_mode": sub_place["initial_mode"],
                            "subcomponent_positions": sub_place["subcomponent_positions"],
                            "compute_node": sub_place["compute_node"],
                        },
                        "conditioned_availability": sub_env,
                        "capability_lost_breakdown": cap_lost_items,
                    }
                )

    ranked_displacements.sort(
        key=lambda d: (d["total_score_lost"], d["displaced_count"], d["displace_guest_ids"])
    )

    elapsed_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 3)
    if ranked_displacements:
        top_disp = ranked_displacements[0]
        cap_lost = top_disp["capability_lost_breakdown"]
        placement_out = {
            **top_disp["resulting_placement"],
            **d2_by_drop.get(tuple(top_disp["displace_guest_ids"]), {}),
        }
        cond_avail = top_disp.get("conditioned_availability", {"conditioned": False, "restricted_windows": []})
        rationale = build_decision_rationale(
            "YES_IF",
            candidate.guest_id,
            all_binding_witnesses,
            placement_out,
            ranked_displacements,
            cap_lost,
            conditioned_availability=cond_avail,
        )
        return _format_admission_decision(
            verdict="YES_IF",
            candidate=candidate,
            placement=placement_out,
            binding_constraints=all_binding_witnesses,
            ranked_displacements=ranked_displacements,
            capability_lost=cap_lost,
            conditioned_availability=cond_avail,
            next_window=None,
            rationale=rationale,
            elapsed_ms=elapsed_ms,
            ship_profile=ship_profile,
            mission_envelope=mission_envelope,
            priority_profile=priority_profile,
        )

    rationale = build_decision_rationale(
        "NO",
        candidate.guest_id,
        all_binding_witnesses,
        {},
        [],
        [],
    )
    return _format_admission_decision(
        verdict="NO",
        candidate=candidate,
        placement={},
        binding_constraints=all_binding_witnesses,
        ranked_displacements=[],
        capability_lost=[],
        conditioned_availability={"conditioned": False, "restricted_windows": []},
        next_window=None,
        rationale=rationale,
        elapsed_ms=elapsed_ms,
        ship_profile=ship_profile,
        mission_envelope=mission_envelope,
        priority_profile=priority_profile,
    )


def build_baseline_state_with_installed_guests(
    installed_ids: List[str],
    state: ShipOperationalState,
    ship_profile: Dict[str, Any],
    catalog: Dict[str, GuestManifest],
) -> Tuple[ShipOperationalState, List[Dict[str, Any]]]:
    """Install requested baseline guests into `state` without ever silently dropping a guest (H13).

    If an installed guest fits cleanly via `find_feasible_placement`, uses that placement;
    if the user explicitly requested a conflicting baseline (e.g., two guests that conflict on mast or mass),
    installs the guest at its preferred position so the baseline state preserves the exact requested set
    and records any baseline conflict explicitly.
    """
    work_state = state.clone()
    work_state.installed_guests.clear()
    baseline_conflicts: List[Dict[str, Any]] = []
    node_ids = sorted(ship_profile["compute_rack"]["nodes"].keys())

    for idx, gid in enumerate(installed_ids):
        if gid not in catalog:
            from .models import StrictValidationError
            raise StrictValidationError("UNKNOWN_GUEST", f"Baseline guest '{gid}' not in catalog", f"installed_guests.{gid}")
        g_man = catalog[gid]
        place = find_feasible_placement(
            g_man,
            work_state,
            ship_profile,
            catalog,
            hangar_roof_clearance_verified=False,
        )
        if place["feasible"]:
            work_state.installed_guests[gid] = InstalledGuestState(
                guest_id=gid,
                active_mode=place["initial_mode"],
                powered_on=True,
                placements=place["subcomponent_positions"],
                compute_node=place["compute_node"],
            )
        else:
            fallback_positions = {sc.subcomponent_id: sc.preferred_positions[0] for sc in g_man.subcomponents}
            fallback_node = node_ids[idx % len(node_ids)]
            work_state.installed_guests[gid] = InstalledGuestState(
                guest_id=gid,
                active_mode=g_man.default_mode,
                powered_on=True,
                placements=fallback_positions,
                compute_node=fallback_node,
            )
            baseline_conflicts.append(
                {
                    "guest_id": gid,
                    "binding_witnesses": place["binding_witnesses"],
                }
            )
    return work_state, baseline_conflicts


def evaluate_four_guest_combinations(
    catalog: Dict[str, GuestManifest],
    ship_profile: Dict[str, Any],
    mission_envelope: Dict[str, Any],
    priority_profile: PriorityProfile,
) -> Dict[str, Any]:
    """Evaluate all 5 combinations of 4 official guests (G1..G5) for Scenario A & Errata verification (DAT-03, ALIGN-T03)."""
    official_ids = ["G1", "G2", "G3", "G4", "G5"]
    combo_reports: List[Dict[str, Any]] = []
    exceed_10t_count = 0

    for combo in combinations(official_ids, 4):
        total_mass = sum(catalog[g].total_mass_kg for g in combo)
        exceeds_10t = total_mass > 10000
        if exceeds_10t:
            exceed_10t_count += 1

        state = ShipOperationalState(
            sim_time_s=0,
            vessel_mode="PORT_OPS",
            ship_speed_kn=0.0,
            wind_kn=8,
            sea_state=1,
            seawater_temp_c=16.0,
            power_reserve_w=200000,
        )
        step_verdicts: List[Dict[str, Any]] = []
        for gid in combo[:3]:
            d = evaluate_admission(
                catalog[gid], state, ship_profile, mission_envelope, priority_profile, catalog,
                at_port=True, allow_displacement=False, hangar_roof_clearance_verified=True,
            )
            step_verdicts.append({"guest_id": gid, "verdict": d["verdict"]})
            if d["verdict"] in ("YES", "YES_IF") and d.get("placement"):
                p = d["placement"]
                state.installed_guests[gid] = InstalledGuestState(
                    guest_id=gid,
                    active_mode=p["initial_mode"],
                    powered_on=True,
                    placements=p["subcomponent_positions"],
                    compute_node=p["compute_node"],
                )

        fourth_gid = combo[3]
        fourth_decision = evaluate_admission(
            catalog[fourth_gid],
            state,
            ship_profile,
            mission_envelope,
            priority_profile,
            catalog,
            at_port=True,
            allow_displacement=True,
            hangar_roof_clearance_verified=True,
        )
        fourth_decision_unverified = evaluate_admission(
            catalog[fourth_gid],
            state,
            ship_profile,
            mission_envelope,
            priority_profile,
            catalog,
            at_port=True,
            allow_displacement=True,
            hangar_roof_clearance_verified=False,
        )

        # Compute 4-guest physical stability equivalent and slot feasibility
        full_state, baseline_conflicts = build_baseline_state_with_installed_guests(
            list(combo),
            ShipOperationalState(
                sim_time_s=0,
                vessel_mode="PORT_OPS",
                ship_speed_kn=0.0,
                wind_kn=8,
                sea_state=1,
                seawater_temp_c=16.0,
                power_reserve_w=200000,
                hangar_roof_clearance_verified=True,
            ),
            ship_profile,
            catalog,
        )
        full_snap = evaluate_eight_class_budgets(
            ship_profile,
            catalog,
            full_state,
            hangar_roof_clearance_verified=True,
        )
        total_stab_eq_kg = float(
            full_snap["classes"]["mass_stability"]["local_limits"]["stability_equivalent_kg"]["actual_kg_eq"]
        )
        max_stab_eq_kg = float(
            full_snap["classes"]["mass_stability"]["local_limits"]["stability_equivalent_kg"]["max_kg_eq"]
        )
        bay_slot_feasible = bool(full_snap["classes"]["positions"]["feasible"])
        mast_slot_feasible = bool(full_snap["classes"]["mast"]["feasible"])
        simultaneously_installable = bool(
            not exceeds_10t
            and total_stab_eq_kg <= max_stab_eq_kg
            and bay_slot_feasible
            and mast_slot_feasible
            and len(baseline_conflicts) == 0
        )

        combo_reports.append(
            {
                "combination": list(combo),
                "total_mass_kg": total_mass,
                "exceeds_10000_kg": exceeds_10t,
                "mass_margin_kg": 10000 - total_mass,
                "total_stability_eq_kg": total_stab_eq_kg,
                "stability_margin_kg_eq": round(max_stab_eq_kg - total_stab_eq_kg, 3),
                "bay_slot_feasible": bay_slot_feasible,
                "mast_slot_feasible": mast_slot_feasible,
                "simultaneously_installable": simultaneously_installable,
                "fourth_guest_id": fourth_gid,
                "fourth_guest_verdict": fourth_decision["verdict"],
                "fourth_guest_verdict_code": fourth_decision["verdict_code"],
                "fourth_guest_verdict_unverified_roof": fourth_decision_unverified["verdict"],
                "fourth_guest_one_genset_down_holds": (fourth_decision.get("one_genset_down") or {}).get("holds"),
                "fourth_guest_binding_constraints": fourth_decision["binding_constraints"],
                "fourth_guest_ranked_displacements": fourth_decision["ranked_displacements"],
                "fourth_guest_required_conditions": fourth_decision.get("required_conditions", []),
                "unverified_roof_required_conditions": fourth_decision_unverified.get("required_conditions", []),
            }
        )

    g3_g4_g5_mass = sum(catalog[g].total_mass_kg for g in ("G3", "G4", "G5") if g in catalog)
    return {
        "total_combinations": len(combo_reports),
        "exceeding_10000_kg_count": exceed_10t_count,
        "errata_three_of_five_verified": exceed_10t_count == 3,
        "combinations": combo_reports,
        "reference_three_guest_sets": {
            "G3_G4_G5": {
                "guests": ["G3", "G4", "G5"],
                "total_mass_kg": g3_g4_g5_mass,
                "total_mass_t_approx": round(g3_g4_g5_mass / 1000.0, 2),
                "pdf_superseded_claim_t": 9.0,
                "mass_margin_kg": 10000 - g3_g4_g5_mass,
            }
        },
    }


VERDICT_CODE_MAP: Dict[str, str] = {
    "YES": "FEASIBLE",
    "YES_IF": "CONDITIONAL",
    "NOT_NOW": "BLOCKED",
    "NO": "DENIED",
}


def _format_admission_decision(
    *,
    verdict: str,
    candidate: GuestManifest,
    placement: Dict[str, Any],
    binding_constraints: List[BindingConstraintWitness],
    ranked_displacements: List[Dict[str, Any]],
    capability_lost: List[Dict[str, Any]],
    conditioned_availability: Dict[str, Any],
    next_window: Optional[Dict[str, Any]],
    rationale: str,
    elapsed_ms: float,
    ship_profile: Dict[str, Any],
    mission_envelope: Dict[str, Any],
    priority_profile: PriorityProfile,
    required_conditions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    clean_placement = {}
    if placement:
        clean_placement = {
            "initial_mode": placement.get("initial_mode"),
            "subcomponent_positions": placement.get("subcomponent_positions"),
            "compute_node": placement.get("compute_node"),
        }
        for key in ("objective_id", "sort_key_fields", "chosen_key", "evaluated_count"):
            if key in placement:
                clean_placement[key] = placement[key]
    return {
        "decision_type": "D1_ADMISSION",
        "candidate_guest_id": candidate.guest_id,
        "verdict": verdict,
        "verdict_code": VERDICT_CODE_MAP.get(verdict, "DENIED"),
        "binding_constraints": [w.to_dict() for w in binding_constraints],
        "placement": clean_placement,
        "placement_alternatives": list(placement.get("placement_alternatives", [])) if placement else [],
        "rejected_placements": list(placement.get("rejected_placements", [])) if placement else [],
        "ranked_displacements": ranked_displacements,
        "capability_lost": capability_lost,
        "required_conditions": required_conditions or [],
        "conditioned_availability": conditioned_availability,
        "one_genset_down": conditioned_availability.get("one_genset_down"),
        "next_window": next_window,
        "rationale": rationale,
        "calculation_elapsed_ms": elapsed_ms,
        "within_30s_deadline": elapsed_ms <= 30000.0,
        "guest_manifest_sha256": candidate.source_sha256,
        "ship_profile_hash": canonical_json_hash(ship_profile),
        "mission_envelope_hash": canonical_json_hash(mission_envelope),
        "priority_profile_sha256": priority_profile.source_sha256,
    }


def apply_admission_decision_atomically(
    decision: Dict[str, Any],
    candidate: GuestManifest,
    base_state: ShipOperationalState,
    ship_profile: Dict[str, Any],
    mission_envelope: Dict[str, Any],
    catalog: Dict[str, GuestManifest],
    *,
    at_port: bool = True,
    apply_if_admitted: bool = True,
    apply_yes_if: bool = False,
    hangar_roof_clearance_verified: bool = False,
) -> Dict[str, Any]:
    """Atomically validate and apply a YES or conditional YES_IF admission plan (S6-I02).

    Guarantees that:
    - YES_IF is never applied unless `apply_yes_if=True` AND a concrete conditional plan is valid,
    - all physical displacements (`displace_guest_ids`) and reservation cleanups are executed,
    - external conditions (such as `hangar_roof_clearance_verified` when `hangar_roof` is occupied with G4)
      are verified rather than inferred from `apply_yes_if=True`,
    - required mode step-downs are applied if needed by the current state or conditional exclusions, and
    - the resulting state passes `check_dependency_constraints`, `evaluate_eight_class_budgets`, and
      `evaluate_mission_envelope_a10` before committing.
    """
    if not apply_if_admitted:
        return {
            "applied": False,
            "reason": "APPLY_DISABLED",
            "applied_plan": None,
            "displaced_guest_ids": [],
            "state": base_state,
        }

    verdict = str(decision.get("verdict", "NO"))
    if verdict == "YES":
        placement = dict(decision.get("placement") or {})
        displaced_ids: List[str] = []
        action_type = "INSTALL_DIRECT"
        cap_lost: List[Dict[str, Any]] = []
        total_score_lost = 0.0
        net_capability_delta = 0.0
    elif verdict == "YES_IF" and apply_yes_if:
        if decision.get("ranked_displacements"):
            top_disp = decision["ranked_displacements"][0]
            displaced_ids = [str(gid) for gid in top_disp.get("displace_guest_ids", [])]
            action_type = str(top_disp.get("action_type", "REMOVE_AT_PORT"))
            placement = dict(top_disp.get("resulting_placement") or decision.get("placement") or {})
            cap_lost = list(top_disp.get("capability_lost_breakdown", decision.get("capability_lost", [])))
            total_score_lost = float(top_disp.get("total_score_lost", 0.0))
            net_capability_delta = float(top_disp.get("net_capability_delta", 0.0))
        elif decision.get("placement"):
            displaced_ids = []
            action_type = "INSTALL_CONDITIONED"
            placement = dict(decision.get("placement") or {})
            cap_lost = list(decision.get("capability_lost", []))
            total_score_lost = 0.0
            net_capability_delta = 0.0
        else:
            return {
                "applied": False,
                "reason": "NO_EXECUTABLE_PLAN",
                "applied_plan": None,
                "displaced_guest_ids": [],
                "state": base_state,
            }
    else:
        return {
            "applied": False,
            "reason": f"VERDICT_{verdict}_NOT_APPLIED",
            "applied_plan": None,
            "displaced_guest_ids": [],
            "state": base_state,
        }

    sub_positions = dict(placement.get("subcomponent_positions") or {})
    if not sub_positions:
        return {
            "applied": False,
            "reason": "MISSING_PLACEMENT_POSITIONS",
            "applied_plan": None,
            "displaced_guest_ids": [],
            "state": base_state,
        }

    aug_catalog = dict(catalog)
    aug_catalog[candidate.guest_id] = candidate
    work_state = base_state.clone()
    work_state.hangar_roof_clearance_verified = bool(hangar_roof_clearance_verified)

    if displaced_ids:
        if not at_port or action_type != "REMOVE_AT_PORT":
            return {
                "applied": False,
                "reason": "PHYSICAL_DISPLACEMENT_REQUIRES_PORT",
                "applied_plan": None,
                "displaced_guest_ids": [],
                "state": base_state,
            }
        for dgid in displaced_ids:
            if dgid not in work_state.installed_guests:
                return {
                    "applied": False,
                    "reason": f"DISPLACED_GUEST_NOT_INSTALLED:{dgid}",
                    "applied_plan": None,
                    "displaced_guest_ids": [],
                    "state": base_state,
                }
            if dgid in aug_catalog and aug_catalog[dgid].safety_critical:
                return {
                    "applied": False,
                    "reason": f"CANNOT_DISPLACE_SAFETY_CRITICAL:{dgid}",
                    "applied_plan": None,
                    "displaced_guest_ids": [],
                    "state": base_state,
                }
            work_state.installed_guests.pop(dgid, None)
            work_state.pending_commands.pop(dgid, None)
        displaced_set = set(displaced_ids)
        work_state.firm_reserved_loads = [
            fl for fl in work_state.firm_reserved_loads if fl.guest_id not in displaced_set
        ]

    initial_mode = str(placement.get("initial_mode") or candidate.default_mode)
    compute_node = str(placement.get("compute_node") or "node_1")
    work_state.installed_guests[candidate.guest_id] = InstalledGuestState(
        guest_id=candidate.guest_id,
        active_mode=initial_mode,
        powered_on=True,
        placements=sub_positions,
        compute_node=compute_node,
    )

    # External condition gate: verify hangar_roof clearance via evaluate_roof_drone_clearance_safe
    uses_hangar_roof = any(
        "hangar_roof" in ginst.placements.values()
        for ginst in work_state.installed_guests.values()
    )
    clearance_eval = evaluate_roof_drone_clearance_safe(
        work_state,
        ship_profile,
        aug_catalog,
        hangar_roof_clearance_verified=hangar_roof_clearance_verified,
    )
    if (
        (uses_hangar_roof and "G4" in work_state.installed_guests and not hangar_roof_clearance_verified)
        or not clearance_eval.get("satisfied", True)
    ):
        return {
            "applied": False,
            "reason": "UNVERIFIED_EXTERNAL_CONDITION:hangar_roof_clearance_verified",
            "constraint_id": "hangar_roof_drone_clearance",
            "clearance_evaluation": clearance_eval,
            "applied_plan": None,
            "displaced_guest_ids": [],
            "state": base_state,
        }
    if uses_hangar_roof and "G4" in work_state.installed_guests and hasattr(work_state, "stamp_roof_clearance_verification"):
        work_state.stamp_roof_clearance_verification(ship_profile)

    mode_adjustments: Dict[str, str] = {}
    dep_witnesses, cond_notes = check_dependency_constraints(
        candidate,
        work_state,
        aug_catalog,
        candidate_mode=work_state.installed_guests[candidate.guest_id].active_mode,
    )
    if dep_witnesses:
        return {
            "applied": False,
            "reason": "DEPENDENCY_CONSTRAINT_VIOLATION",
            "applied_plan": None,
            "displaced_guest_ids": [],
            "state": base_state,
        }

    # If conditional_excludes or current window budget requires a mode step-down on candidate under YES_IF, try stepping down
    snap_now = evaluate_eight_class_budgets(
        ship_profile,
        aug_catalog,
        work_state,
        hangar_roof_clearance_verified=hangar_roof_clearance_verified,
    )
    if (cond_notes or not snap_now["feasible"]) and verdict == "YES_IF":
        ordered_modes = sorted(
            candidate.modes.values(),
            key=lambda m: (-m.capability_retention_ratio, m.sustained_power_w, m.mode_name),
        )
        for m_spec in ordered_modes:
            work_state.installed_guests[candidate.guest_id].active_mode = m_spec.mode_name
            dw, cn = check_dependency_constraints(
                candidate,
                work_state,
                aug_catalog,
                candidate_mode=m_spec.mode_name,
            )
            sn = evaluate_eight_class_budgets(
                ship_profile,
                aug_catalog,
                work_state,
                hangar_roof_clearance_verified=hangar_roof_clearance_verified,
            )
            if not dw and not cn and sn["feasible"]:
                if m_spec.mode_name != initial_mode:
                    mode_adjustments[candidate.guest_id] = m_spec.mode_name
                dep_witnesses = dw
                cond_notes = cn
                snap_now = sn
                break

    if dep_witnesses or cond_notes or not snap_now["feasible"]:
        return {
            "applied": False,
            "reason": "POST_VALIDATION_INFEASIBLE",
            "applied_plan": None,
            "displaced_guest_ids": [],
            "state": base_state,
        }

    env_now = evaluate_mission_envelope_a10(
        candidate,
        work_state,
        ship_profile,
        mission_envelope,
        aug_catalog,
        hangar_roof_clearance_verified=hangar_roof_clearance_verified,
    )
    if not env_now["envelope_feasible"]:
        return {
            "applied": False,
            "reason": "MISSION_ENVELOPE_INFEASIBLE",
            "applied_plan": None,
            "displaced_guest_ids": [],
            "state": base_state,
        }

    applied_plan = {
        "action_type": action_type,
        "displace_guest_ids": displaced_ids,
        "mode_adjustments": mode_adjustments,
        "resulting_placement": {
            "initial_mode": work_state.installed_guests[candidate.guest_id].active_mode,
            "subcomponent_positions": dict(work_state.installed_guests[candidate.guest_id].placements),
            "compute_node": work_state.installed_guests[candidate.guest_id].compute_node,
        },
        "capability_lost": cap_lost,
        "total_score_lost": total_score_lost,
        "net_capability_delta": net_capability_delta,
    }
    return {
        "applied": True,
        "reason": "APPLIED_VALIDATED_PLAN",
        "applied_plan": applied_plan,
        "displaced_guest_ids": displaced_ids,
        "state": work_state,
    }


