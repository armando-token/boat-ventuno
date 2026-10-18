"""Deterministic, parameterized decision rationale builder (B4, B5, B6, ADM-08)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from .models import BindingConstraintWitness


def build_decision_rationale(
    verdict: str,
    candidate_guest_id: str,
    binding_constraints: List[BindingConstraintWitness],
    placement: Dict[str, Any],
    ranked_displacements: List[Dict[str, Any]],
    capability_lost: List[Dict[str, Any]],
    conditioned_availability: Optional[Dict[str, Any]] = None,
    next_window: Optional[Dict[str, Any]] = None,
    required_conditions: Optional[List[str]] = None,
) -> str:
    """Build a parameterized human-readable operator rationale directly from solver witnesses and state.

    Every value in the text is derived from the decision's mathematical witnesses so that if a
    binding constraint changes, the explanation text changes automatically (ADM-08).
    """
    parts: List[str] = [f"Verdict={verdict} for candidate={candidate_guest_id}."]
    genset_down = (conditioned_availability or {}).get("one_genset_down")
    if genset_down:
        parts.append(str(genset_down["sentence"]))

    if verdict == "YES":
        pos_map = placement.get("subcomponent_positions", {})
        node = placement.get("compute_node", "unassigned")
        pos_str = ", ".join(f"{k}->{v}" for k, v in sorted(pos_map.items()))
        parts.append(f"Feasible placement found ({pos_str}; compute_node={node}) across 14-day mission envelope.")
    elif verdict == "YES_IF":
        if required_conditions:
            parts.append("Requires operational condition(s): " + "; ".join(required_conditions) + ".")
        if ranked_displacements:
            top = ranked_displacements[0]
            disp_ids = ", ".join(top.get("displace_guest_ids", []))
            action = top.get("action_type", "DISPLACE")
            parts.append(
                f"Requires conditional displacement ({action}) of [{disp_ids}] "
                f"(net_score_delta={top.get('net_capability_delta', 0.0):.2f})."
            )
        if capability_lost:
            lost_summary = ", ".join(
                f"{item['guest_id']}:{item['capability_lost_description']} (score_lost={item['score_lost']:.2f})"
                for item in capability_lost
            )
            parts.append(f"Capability trade-off: {lost_summary}.")
    elif verdict == "NOT_NOW":
        if next_window is not None:
            if next_window.get("start_s") is None:
                reason = str(next_window.get("reason", "no valid operational window within the 48 h forecast"))
                if not reason.endswith("."):
                    reason = reason + "."
                if "no valid operational window" in reason or "no window" in reason:
                    parts.append(f"Temporarily deferred; {reason}")
                else:
                    parts.append(f"Temporarily deferred; no valid operational window within the 48 h forecast ({reason})")
            elif "start_utc" in next_window and next_window["start_utc"]:
                start_utc = str(next_window["start_utc"])
                clean_utc = start_utc.replace("T", " ").rstrip("Z") + "Z" if "T" in start_utc and not start_utc.endswith("Z") else start_utc
                dur_prefix = ""
                if "duration_s" in next_window and next_window["duration_s"] is not None:
                    dur_h = next_window["duration_s"] // 3600
                    dur_prefix = f", {dur_h} h long: " if dur_h > 0 else f", {next_window['duration_s']}s long: "
                reason = str(next_window.get("reason", "scheduled window"))
                if clean_utc in reason:
                    details = reason
                else:
                    details = f"{clean_utc}{dur_prefix}{reason}"
                if "source" in next_window and next_window["source"] and str(next_window["source"]) not in details:
                    details += f"; source {next_window['source']}"
                parts.append(
                    f"Temporarily deferred; next valid operational window starts at "
                    f"T+{next_window.get('start_s')}s ({details})."
                )
            else:
                parts.append(
                    f"Temporarily deferred; next valid operational window starts at "
                    f"T+{next_window.get('start_s')}s ({next_window.get('reason', 'scheduled window')})."
                )
        else:
            parts.append("Temporarily deferred; next_window=null (future operational window not yet known).")
    elif verdict == "NO":
        parts.append("Hard physical/operational constraint prevents admission without valid displacement.")

    if binding_constraints:
        witness_strs = [
            f"[{w.resource_class}@{w.state_label}: demand={w.demand}{w.unit} vs capacity={w.capacity}{w.unit} ({w.detail})]"
            for w in binding_constraints
        ]
        parts.append("Binding constraints: " + "; ".join(witness_strs) + ".")
    else:
        parts.append("Binding constraints: none violated.")

    if conditioned_availability and conditioned_availability.get("restricted_windows"):
        rw = conditioned_availability["restricted_windows"]
        rw_strs = [f"{w['window_id']}({w['restriction']})" for w in rw]
        parts.append("Conditioned availability restrictions: " + ", ".join(rw_strs) + ".")

    return " ".join(parts)


# D2 (C4): fixed phrases for the first key component where an alternative loses to the chosen placement.
D2_WHY_NOT_CHOSEN: Dict[str, str] = {
    "roof_cond_rank": "needs the conditional hangar_roof clearance (rank {alt} vs {chosen})",
    "pref_penalty": "less preferred position (preference penalty {alt} vs {chosen})",
    "-retention_milli": "lower delivered capability after environmental effects ({alt_pct}% vs {chosen_pct}%)",
    "scarcity_cost": "consumes scarcer slots (scarcity {alt} vs {chosen}; the free mast is the scarcest resource aboard)",
    "-min_margin_bucket": "leaves a smaller minimum margin across the 8 classes ({alt_pct}% vs {chosen_pct}%)",
    "stab_eq": "higher stability-equivalent mass ({alt} kg-eq vs {chosen} kg-eq)",
    "node_used_cores": "unbalances the compute rack (node already uses {alt} cores vs {chosen})",
    "nid": "ties on every criterion; lexicographic node tie-break ({alt} after {chosen})",
}


def d2_why_not_chosen(fields: List[str], chosen_key: List[Any], alt_key: List[Any]) -> str:
    """Phrase for the first key component where `alt_key` is worse than `chosen_key`."""
    for i, name in enumerate(fields):
        if alt_key[i] == chosen_key[i]:
            continue
        a, c = alt_key[i], chosen_key[i]
        if name == "-retention_milli":
            return D2_WHY_NOT_CHOSEN[name].format(alt_pct=round(-a / 10, 1), chosen_pct=round(-c / 10, 1))
        if name == "-min_margin_bucket":
            return D2_WHY_NOT_CHOSEN[name].format(alt_pct=round(-a / 2, 1), chosen_pct=round(-c / 2, 1))
        return D2_WHY_NOT_CHOSEN[name].format(alt=a, chosen=c)
    return "ties on every criterion; the position earlier in the preference order was kept"


def d2_why_rejected(witness: Dict[str, Any], occupants: Dict[str, str]) -> str:
    """One sentence for the first binding witness of a rejected placement."""
    base = (
        f"{witness['resource_class']}/{witness['constraint_id']}: demand {witness['demand']}{witness['unit']} "
        f"vs capacity {witness['capacity']}{witness['unit']}"
    )
    if occupants:
        base += "; " + ", ".join(f"{pos} already occupied by {gid}" for pos, gid in sorted(occupants.items()))
    return base


def logical_reconfiguration_sentence(candidate_id: str, logical: Dict[str, Any]) -> str:
    """Causal sentence for the 0-crew-at-sea NO: why the install is impossible and what software-only action is proposed."""
    head = f"0 crew at sea: physical install of {candidate_id} impossible; "
    if not logical.get("options"):
        return head + "no logical reconfiguration available (" + logical.get("reason", "none feasible") + ")."
    return head + "proposed logical reconfiguration: " + logical["options"][0]["summary"] + "."


_CLASS_LABEL: Dict[str, str] = {
    "power": "electrical power",
    "cooling": "chilled-water cooling",
    "positions": "deck/mast positions",
    "mass": "mass/stability",
    "mass_stability": "mass/stability",
    "data": "data links",
    "compute": "compute",
    "time": "timing",
    "emissions": "emission state",
}


def _fmt_qty(value: Any, unit: str) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return f"{value} {unit}".strip()
    u = (unit or "").strip()
    if u == "W":
        return f"{v / 1000.0:g} kW"
    if u in ("bps", "bit/s"):
        return f"{v / 1e6:g} Mbit/s"
    if u == "kg":
        return f"{v:,.0f} kg"
    return f"{v:g} {u}".strip()


def plain_headline(
    verdict: str,
    candidate_guest_id: str,
    binding_constraints: List[Any],
    placement: Optional[Dict[str, Any]] = None,
    ranked_displacements: Optional[List[Dict[str, Any]]] = None,
    next_window: Optional[Dict[str, Any]] = None,
) -> str:
    """One plain-language sentence for the operator, naming the binding constraint (brief: 'Refused: ...')."""
    w = binding_constraints[0] if binding_constraints else None
    if w is not None and not isinstance(w, dict):
        w = w.to_dict()
    bind = ""
    if w:
        label = _CLASS_LABEL.get(str(w.get("resource_class")), str(w.get("resource_class")))
        detail = str(w.get("detail") or "").split(";")[0].strip()
        if "=" in detail or "_" in detail.split(" ")[0]:
            detail = ""
        nums = ""
        if w.get("unit") not in ("window", "port_call", "flag", "condition", "") and w.get("capacity") is not None:
            nums = f" (needs {_fmt_qty(w.get('demand'), w.get('unit', ''))}, {_fmt_qty(w.get('capacity'), w.get('unit', ''))} available)"
        bind = f"{label} binds{nums}" + (f" - {detail}" if detail else "")
    g = candidate_guest_id
    if verdict == "YES":
        pos = ", ".join(f"{v}" for _, v in sorted(((placement or {}).get("subcomponent_positions") or {}).items()))
        return f"Admitted: {g} fits" + (f" at {pos}" if pos else "") + " with every resource class inside the 14-day mission envelope."
    if verdict == "YES_IF":
        top = (ranked_displacements or [{}])[0] if ranked_displacements else {}
        ids = ", ".join(top.get("displace_guest_ids", []) or [])
        act = str(top.get("action_type", "DISPLACE"))
        verb = {
            "REMOVE_AT_PORT": "{ids} is removed at port",
            "DEACTIVATE_AND_REPLACE": "{ids} is deactivated",
            "STEP_DOWN": "{ids} is throttled",
        }.get(act, act.lower().replace("_", " ") + " {ids}")
        if top.get("step_downs"):
            cond = ", ".join(f"{sd['guest_id']} is throttled to {sd['to_mode']}" for sd in top["step_downs"])
        elif ids:
            cond = verb.format(ids=ids)
        else:
            return f"Yes if conditions are met: {bind or 'operational conditions apply'}" + (
                f" (+{len(binding_constraints) - 1} more condition(s))." if len(binding_constraints) > 1 else "."
            )
        return f"Yes if {cond}: otherwise {bind or 'a hard constraint binds'}."
    if verdict == "NOT_NOW":
        when = ""
        if next_window and next_window.get("start_utc"):
            when = f"; next window {next_window['start_utc']}"
        elif next_window and next_window.get("start_s") is not None:
            when = f"; next window T+{next_window['start_s']}s"
        else:
            when = "; no valid window in the forecast horizon"
        return f"Not now: {g} needs an operational window that is closed ({bind or 'conditions outside limits'}){when}."
    return f"Refused: {g} - {bind or 'hard constraint prevents admission'}."
