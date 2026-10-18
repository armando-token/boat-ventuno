"""Strict domain data models and fail-closed type/value validators for SDV Resource Governor."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
import hashlib
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


class StrictValidationError(ValueError):
    """Raised when any input manifest, ship profile, scenario, or telemetry value violates strict validation."""

    def __init__(self, code: str, message: str, field_path: str = "root") -> None:
        super().__init__(f"[{code}] {field_path}: {message}")
        self.code = code
        self.message = message
        self.field_path = field_path


VALID_PROVENANCE_LABELS: Set[str] = {
    "MEASURED",
    "SIMULATED",
    "DERIVED",
    "ASSUMED",
    "UNAVAILABLE",
}

VALID_VALUE_ORIGINS: Set[str] = {
    "OFFICIAL_BRIEF",
    "ORGANIZER_ERRATA",
    "ASSUMED",
}

VALID_POSITIONS: Set[str] = {
    "bay1",
    "bay1_a",
    "bay1_b",
    "bay2",
    "hangar",
    "hangar_roof",
    "flight_deck",
    "stern_ramp",
    "mast_1",
    "mast_2",
    "mast_3",
    "rack",
}

VALID_COOLING_ROUTES: Set[str] = {
    "CHILLED_WATER_LOOP",
    "MAST_AIR_2KW",
    "PASSIVE_NO_COOLING",
}

VALID_VERDICTS: Set[str] = {"YES", "YES_IF", "NO", "NOT_NOW"}

VALID_SCENARIO_CLASSES: Set[str] = {
    "OFFICIAL_CHALLENGE",
    "DECLARED_VARIANT",
    "STRESS_TEST",
    "STRESS_VARIANT",
    "UNIT_FIXTURE",
    "HISTORICAL",
    "PUBLIC_DATA_REPLAY",
}

ROOF_DRONE_CLEARANCE_CONSTRAINT_ID = "hangar_roof_drone_clearance"


def is_bool(val: Any) -> bool:
    return isinstance(val, bool)


def require_bool(val: Any, field_path: str) -> bool:
    if not isinstance(val, bool):
        raise StrictValidationError(
            "TYPE_ERROR",
            f"Expected strict boolean at '{field_path}', got {type(val).__name__} ({val!r})",
            field_path,
        )
    return val


def require_int(
    val: Any,
    field_path: str,
    *,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
    allow_zero: bool = True,
) -> int:
    if isinstance(val, bool) or not isinstance(val, int):
        raise StrictValidationError(
            "TYPE_ERROR",
            f"Expected strict integer (bool forbidden) at '{field_path}', got {type(val).__name__} ({val!r})",
            field_path,
        )
    if not allow_zero and val == 0:
        raise StrictValidationError(
            "ZERO_FORBIDDEN",
            f"Zero value is forbidden at '{field_path}'",
            field_path,
        )
    if min_value is not None and val < min_value:
        raise StrictValidationError(
            "NEGATIVE_VALUE" if min_value == 0 and val < 0 else "OUT_OF_RANGE",
            f"Value {val} at '{field_path}' is below minimum {min_value}",
            field_path,
        )
    if max_value is not None and val > max_value:
        raise StrictValidationError(
            "OUT_OF_RANGE",
            f"Value {val} at '{field_path}' exceeds maximum {max_value}",
            field_path,
        )
    return val


def require_finite_number(
    val: Any,
    field_path: str,
    *,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    allow_zero: bool = True,
) -> float:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise StrictValidationError(
            "TYPE_ERROR",
            f"Expected finite number (bool/str forbidden) at '{field_path}', got {type(val).__name__} ({val!r})",
            field_path,
        )
    fval = float(val)
    if math.isnan(fval) or math.isinf(fval):
        raise StrictValidationError(
            "NON_FINITE",
            f"NaN or Inf is forbidden at '{field_path}' ({val!r})",
            field_path,
        )
    if not allow_zero and fval == 0.0:
        raise StrictValidationError(
            "ZERO_FORBIDDEN",
            f"Zero value is forbidden at '{field_path}'",
            field_path,
        )
    if min_value is not None and fval < min_value:
        raise StrictValidationError(
            "NEGATIVE_VALUE" if min_value == 0.0 and fval < 0.0 else "OUT_OF_RANGE",
            f"Value {fval} at '{field_path}' is below minimum {min_value}",
            field_path,
        )
    if max_value is not None and fval > max_value:
        raise StrictValidationError(
            "OUT_OF_RANGE",
            f"Value {fval} at '{field_path}' exceeds maximum {max_value}",
            field_path,
        )
    return fval


def require_str(
    val: Any,
    field_path: str,
    *,
    allowed_enum: Optional[Set[str]] = None,
    allow_empty: bool = False,
) -> str:
    if not isinstance(val, str):
        raise StrictValidationError(
            "TYPE_ERROR",
            f"Expected string at '{field_path}', got {type(val).__name__} ({val!r})",
            field_path,
        )
    s = val.strip()
    if not allow_empty and not s:
        raise StrictValidationError(
            "EMPTY_STRING",
            f"Empty string is forbidden at '{field_path}'",
            field_path,
        )
    if s.lower() in {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "-infinity"}:
        raise StrictValidationError(
            "NON_FINITE_STRING",
            f"String representation of NaN/Inf is forbidden at '{field_path}' ({val!r})",
            field_path,
        )
    if allowed_enum is not None and s not in allowed_enum:
        raise StrictValidationError(
            "UNKNOWN_ENUM",
            f"Value '{s}' at '{field_path}' not in allowed set {sorted(allowed_enum)}",
            field_path,
        )
    return s


def require_dict(
    val: Any,
    field_path: str,
    *,
    required_keys: Optional[Set[str]] = None,
    allowed_keys: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    if not isinstance(val, dict):
        raise StrictValidationError(
            "TYPE_ERROR",
            f"Expected mapping/dict at '{field_path}', got {type(val).__name__}",
            field_path,
        )
    if required_keys is not None:
        missing = sorted(required_keys - set(val.keys()))
        if missing:
            raise StrictValidationError(
                "MISSING_KEY",
                f"Missing required keys {missing} at '{field_path}'",
                field_path,
            )
    if allowed_keys is not None:
        extra = sorted(set(val.keys()) - allowed_keys)
        if extra:
            raise StrictValidationError(
                "UNKNOWN_FIELD",
                f"Unknown/unexpected fields {extra} at '{field_path}'",
                field_path,
            )
    return val


def require_no_unknown_keys(
    val: Any,
    allowed_keys: Set[str],
    field_path: str = "root",
) -> Dict[str, Any]:
    """Fail-closed validator rejecting any unexpected keys in `val` (V2-F08)."""
    return require_dict(val, field_path, allowed_keys=allowed_keys)


def canonical_json_bytes(obj: Any) -> bytes:
    """Serialize an object deterministically (sorted keys, UTF-8, no NaN/Inf allowed)."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_hash(obj: Any) -> str:
    """Return hex SHA-256 of canonical JSON representation."""
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


@dataclass(frozen=True)
class ComponentAllocationSpec:
    subcomponent_id: str
    nominal_power_w: int
    sustained_power_w: int
    peak_power_w: int
    cooling_water_w: int
    cooling_air_w: int
    source_kind: str = "ASSUMED"
    assumption_ref: Optional[str] = None


@dataclass(frozen=True)
class SubcomponentSpec:
    subcomponent_id: str
    part_of: str
    name: str
    mass_kg: int
    footprint_m2: float
    wind_area_m2: float
    preferred_positions: Tuple[str, ...]
    allowed_positions: Tuple[str, ...]
    cooling_route: str
    requires_water_manifold: bool
    value_origin: str
    field_origins: Dict[str, str] = field(default_factory=dict)
    provenance_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GuestModeSpec:
    mode_name: str
    description: str
    min_power_w: int
    nominal_power_w: int
    sustained_power_w: int
    sustained_duration_s: int
    peak_power_w: int
    peak_duration_s: int
    cooling_water_w: int
    cooling_air_mast_w: int
    compute_cores: int
    compute_ram_mib: int
    compute_accelerators: int
    shared_storage_gb: int
    backbone_lan_bps: int
    satcom_bps: int
    safety_critical: bool
    uninterruptible: bool
    min_done_duration_s: int
    min_cooldown_s: int
    forbids_emcon_silence: bool
    requires_deck_clear: bool
    requires_quiet_acoustic_state: bool
    capability_retention_ratio: float
    value_origin: str
    field_origins: Dict[str, str] = field(default_factory=dict)
    component_allocations: Dict[str, ComponentAllocationSpec] = field(default_factory=dict)


@dataclass(frozen=True)
class InstallationConstraints:
    port_only: bool
    min_crew_required: int
    install_time_min: int
    remove_time_min: int
    sea_install_max_wind_kn: int
    sea_install_min_speed_kn: float
    sea_install_max_speed_kn: float
    antenna_min_crew_required: int = 0
    antenna_install_time_min: int = 0


@dataclass(frozen=True)
class VehicleReplacementConstraints:
    supported_at_sea: bool
    min_crew_required: int
    swap_duration_min: int
    min_speed_kn: float
    max_speed_kn: float
    max_wind_kn: int
    max_sea_state: int


@dataclass(frozen=True)
class ShipStateConstraints:
    min_speed_kn_for_operation: float
    max_speed_kn_for_operation: float
    max_wind_kn_for_operation: int
    max_sea_state_for_operation: int
    requires_heading_into_wind_for_launch_recovery: bool
    forbids_deck_machinery_while_active: bool
    uses_deck_machinery: bool
    requires_stern_ramp_free: bool
    acoustic_hold_handshake_min_s: int
    acoustic_hold_handshake_max_s: int
    acoustic_hold_default_s: int
    low_battery_threshold_pct: Optional[float]
    emergency_landing_deadline_s: Optional[int]
    max_flight_endurance_min: Optional[int]
    radar_tx_degradation_ratio: float = 0.0
    launch_recovery_min_speed_kn: Optional[float] = None
    launch_recovery_max_speed_kn: Optional[float] = None


@dataclass(frozen=True)
class DependencySpec:
    requires_guests: Tuple[str, ...]
    excludes_guests: Tuple[str, ...]
    conditional_excludes: Tuple[Dict[str, Any], ...]


@dataclass(frozen=True)
class GuestManifest:
    schema_version: str
    guest_id: str
    name: str
    category: str
    safety_critical: bool
    default_mode: str
    subcomponents: Tuple[SubcomponentSpec, ...]
    modes: Dict[str, GuestModeSpec]
    installation: InstallationConstraints
    vehicle_replacement: VehicleReplacementConstraints
    ship_state_constraints: ShipStateConstraints
    dependencies: DependencySpec
    mission_capabilities: Dict[str, float]
    can_buffer_offline: bool
    source_sha256: str
    derivation_metadata: Dict[str, Any] = field(default_factory=dict)
    logical_workload: Optional[Dict[str, Any]] = None

    @property
    def total_mass_kg(self) -> int:
        return sum(sc.mass_kg for sc in self.subcomponents)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PriorityProfile:
    schema_version: str
    priority_profile_id: str
    name: str
    description: str
    capability_weights: Dict[str, float]
    guest_priority_rank: Dict[str, int]
    survey_vs_recovery_strategy: str
    pre_reserve_recovery_power: bool
    max_acceptable_survey_pause_min: int
    tie_break_order: str
    prefer_mode_downgrade_before_shed: bool
    protect_uninterruptible_tasks: bool
    shed_order_lowest_priority_first: Tuple[str, ...]
    source_sha256: str


@dataclass(frozen=True)
class BindingConstraintWitness:
    resource_class: str
    constraint_id: str
    state_label: str
    demand: float | int
    capacity: float | int
    margin: float | int
    unit: str
    provenance: str
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
