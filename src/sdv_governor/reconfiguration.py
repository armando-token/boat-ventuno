"""C3: logical (software-only) reconfiguration proposed when a physical install is impossible (0 crew at sea).

Actions are limited to MODE_CHANGE of installed guests and COMPUTE_PLACEMENT of the candidate's logical workload:
no deck operation, no vehicle launch/recovery, no protected mode is touched. Deterministic and read-only.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from .budgets import evaluate_eight_class_budgets, is_mode_protected
from .capability_effects import effective_capability_retention
from .degradation import DEPLOYED_VEHICLE_MODES
from .models import GuestManifest
from .placement import _min_margin_bucket, compute_guest_capability_score
from .ship_state import ShipOperationalState

LOGICAL_RECONFIG_OBJECTIVE = "LOGICAL_RECONFIG_V1: coverage desc, capability_lost asc, min_margin desc, option_id asc"
MIN_COVERAGE = 0.10  # ASSUMPTIONS.yaml: LOGICAL_RECONFIG_MIN_COVERAGE
MAX_OPTIONS_DEFAULT = 3
OPTION_KINDS = ("RETASK_MODE", "REALLOCATE_COMPUTE", "RETASK_PLUS_COMPUTE")
ACTION_TYPES = ("MODE_CHANGE", "COMPUTE_PLACEMENT")


def _provided(
    catalog: Dict[str, GuestManifest],
    state: ShipOperationalState,
    ship_profile: Dict[str, Any],
    mode_by_guest: Dict[str, str],
    caps: List[str],
) -> Dict[str, float]:
    out = {c: 0.0 for c in caps}
    for gid in sorted(state.installed_guests):
        inst = state.installed_guests[gid]
        if not inst.powered_on or gid not in catalog:
            continue
        mode = mode_by_guest.get(gid, inst.active_mode)
        ret, _ = effective_capability_retention(catalog[gid], inst, state, ship_profile, mode_name=mode)
        for c in caps:
            out[c] += catalog[gid].mission_capabilities.get(c, 0.0) * ret
    return out


def _best_node(snap: Dict[str, Any], workload: Dict[str, Any]) -> Optional[Tuple[str, int]]:
    """Node with the most free cores left after the workload (ties: lowest node id) and its margin bucket."""
    comp = workload["compute"]
    need_ram = comp["memory_gib"] * 1024
    local = snap["classes"]["compute"]["local_limits"]
    best: Optional[Tuple[int, str, int]] = None
    for nid in sorted(local["nodes"]):
        n = local["nodes"][nid]
        if (
            n["free_cores"] < comp["cores"]
            or n["free_ram_mib"] < need_ram
            or n["free_accelerators"] < comp["accelerators"]
        ):
            continue
        if comp["accelerators"] > local["cluster_accelerators"]["free"]:
            continue
        left = n["free_cores"] - comp["cores"]
        if best is None or left > best[0]:
            best = (left, nid, int(200 * left / n["allocatable_cores"]))
    if best is None:
        return None
    return best[1], best[2]


def _satcom_ok(snap: Dict[str, Any], workload: Dict[str, Any]) -> bool:
    sat = snap["classes"]["data"]["local_limits"]["satcom_bps"]
    return workload["satcom_bps"] <= sat["free_bps"]


def propose_logical_reconfiguration(
    candidate: GuestManifest,
    state: ShipOperationalState,
    ship_profile: Dict[str, Any],
    catalog: Dict[str, GuestManifest],
    profile: Any,
    *,
    max_options: int = MAX_OPTIONS_DEFAULT,
) -> Dict[str, Any]:
    """Rank up to `max_options` software-only ways to recover (part of) the capability the candidate would add."""
    t0 = time.perf_counter_ns()
    caps = sorted(candidate.mission_capabilities)
    total_w = sum(candidate.mission_capabilities.values()) or 1.0
    base_modes = {gid: i.active_mode for gid, i in state.installed_guests.items()}
    before = _provided(catalog, state, ship_profile, base_modes, caps)
    workload = candidate.logical_workload
    evaluated = 0

    base_snap = evaluate_eight_class_budgets(ship_profile, catalog, state)

    def coverage(after: Dict[str, float], extra: Dict[str, float]) -> Tuple[float, Dict[str, float]]:
        per_cap = {}
        for c in caps:
            gain = max(0.0, after[c] - before[c]) + extra.get(c, 0.0)
            per_cap[c] = round(min(candidate.mission_capabilities[c], gain), 4)
        return round(sum(per_cap.values()) / total_w, 4), per_cap

    compute_extra = {}
    if workload:
        frac = workload["capability_fraction"]
        compute_extra = {c: candidate.mission_capabilities[c] * frac for c in caps}

    retasks: List[Dict[str, Any]] = []
    for gid in sorted(state.installed_guests):
        inst = state.installed_guests[gid]
        if not inst.powered_on or gid not in catalog:
            continue
        man = catalog[gid]
        cur_spec = man.modes[inst.active_mode]
        is_prot, _ = is_mode_protected(man, cur_spec, inst.mode_elapsed_s, inst.battery_pct)
        if is_prot or inst.active_mode in DEPLOYED_VEHICLE_MODES.get(gid, ()):
            continue
        for mode in sorted(man.modes):
            spec = man.modes[mode]
            if mode == inst.active_mode or spec.requires_deck_clear or mode in DEPLOYED_VEHICLE_MODES.get(gid, ()):
                continue
            evaluated += 1
            trial = state.clone()
            trial.installed_guests[gid].active_mode = mode
            snap = evaluate_eight_class_budgets(ship_profile, catalog, trial)
            if not snap["feasible"]:
                continue
            after = _provided(catalog, trial, ship_profile, {**base_modes, gid: mode}, caps)
            lost = max(
                0.0,
                compute_guest_capability_score(man, inst.active_mode, profile)
                - compute_guest_capability_score(
                    man, mode, profile,
                    retention_override=effective_capability_retention(man, inst, trial, ship_profile, mode_name=mode)[0],
                ),
            )
            retasks.append({"gid": gid, "mode": mode, "from": inst.active_mode, "trial": trial, "snap": snap,
                            "after": after, "lost": round(lost, 4)})

    options: List[Dict[str, Any]] = []

    def add(kind, actions, cov, per_cap, lost, margin, text):
        options.append({
            "option_id": f"{kind}:" + "+".join(a["target"] + "=" + a["to"] for a in actions),
            "kind": kind,
            "actions": actions,
            "coverage": cov,
            "coverage_by_capability": per_cap,
            "capability_lost_elsewhere": lost,
            "min_margin_bucket": margin,
            "summary": text,
        })

    def retask_action(r):
        return {"action_type": "MODE_CHANGE", "target": r["gid"], "from": r["from"], "to": r["mode"]}

    def retask_text(r, cov):
        return f"re-task {r['gid']} to {r['mode']} (+{round(cov * 100)} % coverage)"

    for r in retasks:
        cov, per_cap = coverage(r["after"], {})
        if cov >= MIN_COVERAGE:
            add("RETASK_MODE", [retask_action(r)], cov, per_cap, r["lost"], _min_margin_bucket(r["snap"]),
                retask_text(r, cov))

    if workload:
        for tag, snap, after, lost, pre in [("", base_snap, before, 0.0, None)] + [
            ("R", r["snap"], r["after"], r["lost"], r) for r in retasks
        ]:
            evaluated += 1
            node = _best_node(snap, workload)
            if node is None or not _satcom_ok(snap, workload):
                continue
            nid, node_bucket = node
            cov, per_cap = coverage(after, compute_extra)
            if cov < MIN_COVERAGE:
                continue
            actions = [{"action_type": "COMPUTE_PLACEMENT", "target": candidate.guest_id, "from": "UNPLACED", "to": nid}]
            margin = min(_min_margin_bucket(snap), node_bucket)
            if pre is None:
                add("REALLOCATE_COMPUTE", actions, cov, per_cap, 0.0, margin,
                    f"run {candidate.guest_id} analytics on {nid} (+{round(cov * 100)} % coverage)")
            else:
                add("RETASK_PLUS_COMPUTE", [retask_action(pre)] + actions, cov, per_cap, lost, margin,
                    f"{retask_text(pre, cov)} and run {candidate.guest_id} analytics on {nid}")

    options.sort(key=lambda o: (-o["coverage"], o["capability_lost_elsewhere"], -o["min_margin_bucket"], o["option_id"]))
    options = options[:max_options]
    elapsed_ms = round((time.perf_counter_ns() - t0) / 1_000_000.0, 3)
    status = "OPTIONS_AVAILABLE" if options else "NONE_FEASIBLE"
    out: Dict[str, Any] = {
        "status": status,
        "options": options,
        "objective": LOGICAL_RECONFIG_OBJECTIVE,
        "min_coverage": MIN_COVERAGE,
        "evaluated_count": evaluated,
        "elapsed_ms": elapsed_ms,
    }
    if not options:
        out["reason"] = (
            f"no software-only action recovers at least {int(MIN_COVERAGE * 100)} % of the capabilities of "
            f"{candidate.guest_id} without touching protected modes, vehicles or deck operations"
        )
    return out
