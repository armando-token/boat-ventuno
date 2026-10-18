"""Environmental effects on delivered guest capability (C6): own-radar transmission degrades capable guests (Annex B G1).

Effects change only the capability a guest delivers; they never change power, heat or mode (a physical effect is not an order).
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Optional, Tuple

from .models import BindingConstraintWitness, GuestManifest, StrictValidationError
from .ship_state import InstalledGuestState, ShipOperationalState

OWN_RADAR_POLICIES: Tuple[str, ...] = ("QUIET_RADIO", "TRANSMIT")
OWN_RADAR_TX_CAUSE = "OWN_RADAR_TX_NEARBY"
# ASSUMED (ASSUMPTIONS.yaml::QUIET_RADIO_MIN_CREW): bridge watch with visual lookout and AIS needs people aboard
QUIET_RADIO_MIN_CREW = 6

_REGIME_CREW: Dict[str, int] = {"FULL_CREW_14": 14, "REDUCED_CREW_6": 6, "UNCREWED_0": 0}


def crew_aboard(state: ShipOperationalState) -> int:
    regime = str(state.crew_regime)
    if int(state.crew_count) == 0 or regime == "UNCREWED_0":
        return 0
    return _REGIME_CREW.get(regime, int(state.crew_count))


def is_at_port(state: ShipOperationalState) -> bool:
    return str(state.vessel_mode).startswith("PORT")


def quiet_radio_effective(state: ShipOperationalState) -> bool:
    """QUIET_RADIO is honoured only with enough crew aboard; otherwise the radar is treated as transmitting (fail-safe)."""
    return state.own_radar_policy == "QUIET_RADIO" and crew_aboard(state) >= QUIET_RADIO_MIN_CREW


def own_radar_transmitting(state: ShipOperationalState) -> bool:
    if is_at_port(state) or state.emcon_silence:
        return False
    return not quiet_radio_effective(state)


def own_radar_exposed_positions(ship_profile: Dict[str, Any]) -> FrozenSet[str]:
    spec = ship_profile.get("own_radar") or {}
    return frozenset(str(p) for p in spec.get("exposed_positions", []))


def check_own_radar_policy(policy: str, state: ShipOperationalState) -> Optional[BindingConstraintWitness]:
    """Return a witness if `policy` cannot be accepted in `state`, else None. Unknown policies raise."""
    if policy not in OWN_RADAR_POLICIES:
        raise StrictValidationError("UNKNOWN_ENUM", f"own radar policy must be one of {OWN_RADAR_POLICIES}", "value")
    crew = crew_aboard(state)
    if policy == "QUIET_RADIO" and crew < QUIET_RADIO_MIN_CREW:
        return BindingConstraintWitness(
            resource_class="crew",
            constraint_id="quiet_radio_requires_bridge_watch",
            state_label=f"T+{state.sim_time_s}s:OWN_RADAR_POLICY",
            demand=QUIET_RADIO_MIN_CREW,
            capacity=crew,
            margin=crew - QUIET_RADIO_MIN_CREW,
            unit="crew",
            provenance="ASSUMED",
            detail=(
                f"Quiet radio needs a bridge watch with visual lookout and AIS (>= {QUIET_RADIO_MIN_CREW} crew); "
                f"with {crew} crew aboard the radar must stay on for collision avoidance"
            ),
        )
    return None


def effective_capability_retention(
    manifest: GuestManifest,
    inst: InstalledGuestState,
    state: ShipOperationalState,
    ship_profile: Dict[str, Any],
    *,
    mode_name: Optional[str] = None,
    placements: Optional[Dict[str, str]] = None,
) -> Tuple[float, Tuple[str, ...]]:
    """Capability retention of `inst` after environmental effects, with the causes that applied."""
    r = manifest.modes[mode_name or inst.active_mode].capability_retention_ratio
    causes = []
    loss = manifest.ship_state_constraints.radar_tx_degradation_ratio
    if loss and own_radar_transmitting(state):
        exposed = own_radar_exposed_positions(ship_profile)
        if any(pos in exposed for pos in (placements if placements is not None else inst.placements).values()):
            r = round(r * (1.0 - loss), 4)
            causes.append(OWN_RADAR_TX_CAUSE)
    return r, tuple(causes)
