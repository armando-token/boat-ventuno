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
