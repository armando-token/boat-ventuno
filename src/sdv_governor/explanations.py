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
