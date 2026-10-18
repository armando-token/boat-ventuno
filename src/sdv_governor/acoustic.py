"""Acoustic command link to the underwater vehicle (Annex A.5): water-column layering, own-ship manoeuvre, deck machinery.

Layering is a scenario/operator input (ASSUMED, no public vertical profile); the other two causes are derived from state.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

from .models import StrictValidationError

# Causes A.5 lists for the acoustic link; MODEM_LINK_LOSS is the operator/scenario outage event and is always honoured.
KNOWN_CAUSES: Tuple[str, ...] = ("LAYERED_WATER", "OWN_SHIP_MANOEUVRE", "DECK_MACHINERY_ACTIVE")
_OFFICIAL_CAUSES = KNOWN_CAUSES
_MANOEUVRE_VARIABLES = frozenset({"heading_deg", "speed_kn"})
_MANOEUVRE_TAGS = ("TURN", "COURSE", "SPEED", "HEADING")

DEFAULT_ACOUSTIC_LINK_POLICY: Dict[str, Any] = {
    "policy_id": "ACOUSTIC_LINK_POLICY_V1",
    "breaks_on": list(_OFFICIAL_CAUSES),
    "nominal_rate_bps": {"min": 1000, "max": 5000, "provenance": "OFFICIAL_BRIEF"},
    "latency_s": {"min": 10, "max": 60, "provenance": "OFFICIAL_BRIEF"},
    "layered_water_default": {"value": False, "provenance": "ASSUMED"},
    "g3_lost_link_behaviour": {"timeout_s": 120, "action": "AUTONOMOUS_HOLD_LOITER", "provenance": "ASSUMED"},
}


@dataclass(frozen=True)
class AcousticLinkPolicy:
    policy_id: str
    breaks_on: Tuple[str, ...]
    rate_bps_min: int
    rate_bps_max: int
    latency_min_s: int
    latency_max_s: int
    lost_link_timeout_s: int
    lost_link_action: str


@dataclass(frozen=True)
class AcousticLinkStatus:
    available: bool
    causes: Tuple[str, ...]
    rate_bps_max: int
    policy_id: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "causes": list(self.causes),
            "rate_bps_max": self.rate_bps_max,
            "policy_id": self.policy_id,
        }


def parse_acoustic_link_policy(raw: Dict[str, Any]) -> AcousticLinkPolicy:
    """Strictly validate a policy dict (fail-closed on unknown causes or non-positive timeout)."""
    if not isinstance(raw, dict):
        raise StrictValidationError("TYPE_ERROR", "acoustic link policy must be a mapping", "acoustic_link_policy")
    breaks = raw.get("breaks_on")
    if not isinstance(breaks, list) or not all(isinstance(c, str) for c in breaks):
        raise StrictValidationError("TYPE_ERROR", "breaks_on must be a list of strings", "acoustic_link_policy.breaks_on")
    unknown = sorted(set(breaks) - set(KNOWN_CAUSES))
    if unknown:
        raise StrictValidationError("UNKNOWN_ENUM", f"unknown causes {unknown}", "acoustic_link_policy.breaks_on")
    lost = raw.get("g3_lost_link_behaviour") or {}
    timeout = lost.get("timeout_s")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise StrictValidationError("TYPE_ERROR", "timeout_s must be a positive integer", "acoustic_link_policy.g3_lost_link_behaviour.timeout_s")
    rate = raw.get("nominal_rate_bps") or {}
    lat = raw.get("latency_s") or {}
    return AcousticLinkPolicy(
        policy_id=str(raw.get("policy_id", "ACOUSTIC_LINK_POLICY_V1")),
        breaks_on=tuple(c for c in KNOWN_CAUSES if c in breaks),
        rate_bps_min=int(rate.get("min", 1000)),
        rate_bps_max=int(rate.get("max", 5000)),
        latency_min_s=int(lat.get("min", 10)),
        latency_max_s=int(lat.get("max", 60)),
        lost_link_timeout_s=timeout,
        lost_link_action=str(lost.get("action", "AUTONOMOUS_HOLD_LOITER")),
    )


def load_acoustic_link_policy(repo_root: Optional[Path | str] = None) -> AcousticLinkPolicy:
    """Load `data/ship/acoustic_link_policy.yaml`, falling back to the built-in default."""
    raw = copy.deepcopy(DEFAULT_ACOUSTIC_LINK_POLICY)
    candidates = []
    if repo_root is not None:
        candidates.append(Path(repo_root) / "data" / "ship" / "acoustic_link_policy.yaml")
    candidates.append(Path(__file__).resolve().parents[2] / "data" / "ship" / "acoustic_link_policy.yaml")
    candidates.append(Path(__file__).resolve().parent / "_bundled" / "data" / "ship" / "acoustic_link_policy.yaml")
    for p in candidates:
        if p.exists():
            loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw.update(loaded)
            break
    return parse_acoustic_link_policy(raw)


_DEFAULT_POLICY: Optional[AcousticLinkPolicy] = None


def default_acoustic_link_policy() -> AcousticLinkPolicy:
    global _DEFAULT_POLICY
    if _DEFAULT_POLICY is None:
        _DEFAULT_POLICY = load_acoustic_link_policy()
    return _DEFAULT_POLICY


def _own_ship_manoeuvring(state: Any) -> bool:
    for tr in getattr(state, "pending_transitions", []) or []:
        if getattr(tr, "status", "") != "IN_PROGRESS":
            continue
        if getattr(tr, "resource_or_state_variable", "") in _MANOEUVRE_VARIABLES:
            return True
        if any(tag in str(getattr(tr, "transition_id", "")).upper() for tag in _MANOEUVRE_TAGS):
            return True
    return False


def _deck_machinery_active(state: Any, catalog: Dict[str, Any]) -> bool:
    """Deck machinery in use: the explicit flag, a winch-type mode (e.g. G2 inspection), or a launch/recovery in progress.

    A launch/recovery guest merely sitting in its optics mode (no IN_PROGRESS transition) is not an operation, so it
    does not break the link; this keeps pre-armed fixtures such as the cooling-limited scenario unchanged.
    """
    if getattr(state, "deck_machinery_active", False):
        return True
    in_progress_owners = {
        getattr(tr, "owner_id", "")
        for tr in (getattr(state, "pending_transitions", []) or [])
        if getattr(tr, "status", "") == "IN_PROGRESS"
    }
    for gid, inst in sorted((getattr(state, "installed_guests", {}) or {}).items()):
        man = catalog.get(gid)
        if man is None or not inst.powered_on or not man.ship_state_constraints.uses_deck_machinery:
            continue
        mode = man.modes.get(inst.active_mode)
        if mode is None or not mode.requires_deck_clear:
            continue
        if man.ship_state_constraints.launch_recovery_area == "none" or gid in in_progress_owners:
            return True
    return False


def evaluate_acoustic_link(
    state: Any,
    catalog: Dict[str, Any],
    policy: Optional[AcousticLinkPolicy] = None,
) -> AcousticLinkStatus:
    """Evaluate the acoustic command link; causes are listed in a fixed order for determinism."""
    pol = policy or default_acoustic_link_policy()
    causes = []
    if "LAYERED_WATER" in pol.breaks_on and getattr(state, "water_column_layered", False):
        causes.append("LAYERED_WATER")
    if "OWN_SHIP_MANOEUVRE" in pol.breaks_on and _own_ship_manoeuvring(state):
        causes.append("OWN_SHIP_MANOEUVRE")
    if "DECK_MACHINERY_ACTIVE" in pol.breaks_on and _deck_machinery_active(state, catalog):
        causes.append("DECK_MACHINERY_ACTIVE")
    if getattr(state, "acoustic_link_forced_down", False):
        causes.append("MODEM_LINK_LOSS")
    return AcousticLinkStatus(not causes, tuple(causes), 0 if causes else pol.rate_bps_max, pol.policy_id)


def guests_requiring_acoustic_command_link(state: Any, catalog: Dict[str, Any]) -> Tuple[str, ...]:
    """Installed, powered-on guests with at least one mode that needs the acoustic command link."""
    out = []
    for gid, inst in sorted((getattr(state, "installed_guests", {}) or {}).items()):
        man = catalog.get(gid)
        if inst.powered_on and man is not None and any(m.requires_acoustic_command_link for m in man.modes.values()):
            out.append(gid)
    return tuple(out)


def g3_requires_acoustic_command_link(catalog: Dict[str, Any]) -> bool:
    g3 = catalog.get("G3")
    return bool(g3 and any(m.requires_acoustic_command_link for m in g3.modes.values()))
