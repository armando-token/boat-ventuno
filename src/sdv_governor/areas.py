"""Data-driven launch/recovery area exclusivity (Annex A.3: flight deck and stern ramp are mutually exclusive).

The exclusive pairs are read from the ship baseline flags `mutually_exclusive_with_<area>`; nothing here
hard-codes guest ids, so a new card declaring `ship_state_constraints.launch_recovery_area` inherits the rule.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Iterable, Tuple

from .models import StrictValidationError

LAUNCH_RECOVERY_AREAS: Tuple[str, ...] = ("flight_deck", "stern_ramp", "none")
LAUNCH_RECOVERY_ACTIVE_MODES: FrozenSet[str] = frozenset({"RECOVERY_APPROACH", "LAUNCH_RECOVERY_OPTICS"})
_FLAG_PREFIX = "mutually_exclusive_with_"
# Annex A.3 (OFFICIAL_BRIEF) default used only when the ship profile carries no positions block.
_DEFAULT_PAIRS: FrozenSet[FrozenSet[str]] = frozenset({frozenset({"flight_deck", "stern_ramp"})})


def exclusive_area_pairs(ship_profile: Dict[str, Any] | None) -> FrozenSet[FrozenSet[str]]:
    """Return the set of mutually exclusive area pairs; raise (fail-closed) on asymmetric declarations."""
    areas = (((ship_profile or {}).get("positions") or {}).get("hangar_and_flight_deck")) or {}
    if not isinstance(areas, dict) or not areas:
        return _DEFAULT_PAIRS
    declared: set[Tuple[str, str]] = set()
    for area, spec in areas.items():
        if not isinstance(spec, dict):
            continue
        for key, val in spec.items():
            if key.startswith(_FLAG_PREFIX) and val is True:
                declared.add((str(area), key[len(_FLAG_PREFIX):]))
    for a, b in sorted(declared):
        if (b, a) not in declared:
            raise StrictValidationError(
                "ASYMMETRIC_AREA_EXCLUSION",
                f"area '{a}' is mutually exclusive with '{b}' but '{b}' does not declare the reverse",
                f"ship_profile.positions.hangar_and_flight_deck.{a}.{_FLAG_PREFIX}{b}",
            )
    return frozenset(frozenset(p) for p in declared) if declared else _DEFAULT_PAIRS


def exclusive_areas(ship_profile: Dict[str, Any] | None) -> FrozenSet[str]:
    return frozenset(a for pair in exclusive_area_pairs(ship_profile) for a in pair)


def partner_areas(area: str, ship_profile: Dict[str, Any] | None) -> Tuple[str, ...]:
    return tuple(sorted({b for pair in exclusive_area_pairs(ship_profile) if area in pair for b in pair if b != area}))


def guest_area(manifest: Any) -> str:
    return getattr(manifest.ship_state_constraints, "launch_recovery_area", "none")


def area_conflicts(occupied: Iterable[str], ship_profile: Dict[str, Any] | None) -> Tuple[Tuple[str, str], ...]:
    occ = set(occupied)
    return tuple(sorted(tuple(sorted(p)) for p in exclusive_area_pairs(ship_profile) if p <= occ))  # type: ignore[misc]


def active_area_operations(state: Any, catalog: Dict[str, Any]) -> Dict[str, str]:
    """Map area -> guest_id for launch/recovery operations in progress (IN_PROGRESS transition or active recovery mode)."""
    ops: Dict[str, str] = {}
    for tr in getattr(state, "pending_transitions", []) or []:
        if getattr(tr, "status", "") != "IN_PROGRESS":
            continue
        tag = f"{getattr(tr, 'transition_id', '')} {getattr(tr, 'resource_or_state_variable', '')}".upper()
        if "LAUNCH_RECOVERY" not in tag:
            continue
        man = catalog.get(getattr(tr, "owner_id", ""))
        if man is not None and guest_area(man) != "none":
            ops.setdefault(guest_area(man), man.guest_id)
    for gid, inst in sorted((getattr(state, "installed_guests", {}) or {}).items()):
        man = catalog.get(gid)
        if man is not None and inst.powered_on and inst.active_mode in LAUNCH_RECOVERY_ACTIVE_MODES and guest_area(man) != "none":
            ops.setdefault(guest_area(man), gid)
    return dict(sorted(ops.items()))
