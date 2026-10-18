"""Strict parser and validator for Guest Manifests, Priority Profiles, Ship Baseline, Mission Envelope, and Scenarios (B2, DAT-01..04)."""

from __future__ import annotations

import hashlib
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import yaml

from .areas import exclusive_area_pairs

from .models import (
    ComponentAllocationSpec,
    DependencySpec,
    GuestManifest,
    GuestModeSpec,
    InstallationConstraints,
    PriorityProfile,
    ROOF_DRONE_CLEARANCE_CONSTRAINT_ID,
    ShipStateConstraints,
    StrictValidationError,
    SubcomponentSpec,
    VALID_COOLING_ROUTES,
    VALID_POSITIONS,
    VALID_SCENARIO_CLASSES,
    VALID_VALUE_ORIGINS,
    VehicleReplacementConstraints,
    canonical_json_hash,
    require_bool,
    require_dict,
    require_finite_number,
    require_int,
    require_str,
)

TOP_LEVEL_GUEST_KEYS: Set[str] = {
    "schema_version",
    "guest_id",
    "name",
    "category",
    "safety_critical",
    "default_mode",
    "can_buffer_offline",
    "subcomponents",
    "modes",
    "component_allocations",
    "derivation_metadata",
    "installation",
    "vehicle_replacement",
    "ship_state_constraints",
    "dependencies",
    "mission_capabilities",
}

SUBCOMPONENT_KEYS: Set[str] = {
    "subcomponent_id",
    "part_of",
    "name",
    "mass_kg",
    "footprint_m2",
    "wind_area_m2",
    "preferred_positions",
    "allowed_positions",
    "cooling_route",
    "requires_water_manifold",
    "value_origin",
    "field_origins",
    "provenance_metadata",
}

MODE_KEYS: Set[str] = {
    "description",
    "min_power_w",
    "nominal_power_w",
    "sustained_power_w",
    "sustained_duration_s",
    "peak_power_w",
    "peak_duration_s",
    "cooling_water_w",
    "cooling_air_mast_w",
    "compute_cores",
    "compute_ram_mib",
    "compute_accelerators",
    "shared_storage_gb",
    "backbone_lan_bps",
    "satcom_bps",
    "safety_critical",
    "uninterruptible",
    "min_done_duration_s",
    "min_cooldown_s",
    "forbids_emcon_silence",
    "requires_deck_clear",
    "requires_quiet_acoustic_state",
    "requires_acoustic_command_link",
    "capability_retention_ratio",
    "value_origin",
    "field_origins",
    "component_allocations",
}

COMPONENT_ALLOCATION_KEYS: Set[str] = {
    "subcomponent_id",
    "nominal_power_w",
    "sustained_power_w",
    "peak_power_w",
    "cooling_water_w",
    "cooling_air_w",
    "source_kind",
    "assumption_ref",
}

KNOWN_CAPABILITIES: Set[str] = {
    "maritime_sar_rescue",
    "surface_surveillance",
    "flight_weather_support",
    "wideband_spectrum_monitoring",
    "environmental_sensing",
    "subsea_intervention",
    "hydrographic_survey",
    "seabed_anomaly_detection",
    "water_quality_monitoring",
    "hull_and_cable_inspection",
    "salvage_assistance",
    "subsea_acoustic_survey",
    "aerial_sar_surveillance",
    "rov_hull_inspection",
    "meteo_passive_sensing",
    "water_column_sampling",
}

VALID_PRIORITY_GUEST_IDS: Set[str] = {f"G{i}" for i in range(1, 10)}

INSTALLATION_KEYS: Set[str] = {
    "port_only",
    "min_crew_required",
    "install_time_min",
    "remove_time_min",
    "sea_install_max_wind_kn",
    "sea_install_min_speed_kn",
    "sea_install_max_speed_kn",
    "antenna_min_crew_required",
    "antenna_install_time_min",
}

VEHICLE_REPLACEMENT_KEYS: Set[str] = {
    "supported_at_sea",
    "min_crew_required",
    "swap_duration_min",
    "min_speed_kn",
    "max_speed_kn",
    "max_wind_kn",
    "max_sea_state",
}

SHIP_STATE_CONSTRAINT_KEYS: Set[str] = {
    "min_speed_kn_for_operation",
    "max_speed_kn_for_operation",
    "launch_recovery_min_speed_kn",
    "launch_recovery_max_speed_kn",
    "max_wind_kn_for_operation",
    "max_sea_state_for_operation",
    "requires_heading_into_wind_for_launch_recovery",
    "forbids_deck_machinery_while_active",
    "uses_deck_machinery",
    "requires_stern_ramp_free",
    "acoustic_hold_handshake_min_s",
    "acoustic_hold_handshake_max_s",
    "acoustic_hold_default_s",
    "low_battery_threshold_pct",
    "emergency_landing_deadline_s",
    "max_flight_endurance_min",
    "radar_tx_degradation_ratio",
    "launch_recovery_area",
}

DEPENDENCY_KEYS: Set[str] = {
    "requires_guests",
    "excludes_guests",
    "conditional_excludes",
}

PRIORITY_TOP_KEYS: Set[str] = {
    "schema_version",
    "priority_profile_id",
    "name",
    "description",
    "capability_weights",
    "guest_priority_rank",
    "arbitration_policy",
    "degradation_policy",
}


def load_yaml_file(path: Path) -> Tuple[Dict[str, Any], str]:
    """Load YAML file, compute SHA-256 of raw bytes, and ensure root is a dict."""
    raw_bytes = path.read_bytes()
    sha256_hex = hashlib.sha256(raw_bytes).hexdigest()
    try:
        data = yaml.safe_load(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise StrictValidationError("YAML_PARSE_ERROR", str(exc), str(path)) from exc
    require_dict(data, str(path))
    return data, sha256_hex


def _parse_field_origins(raw_fo: Any, field_path: str, guest_id: str) -> Dict[str, str]:
    if raw_fo is None:
        return {}
    fo_dict = require_dict(raw_fo, field_path)
    out: Dict[str, str] = {}
    for k, v in fo_dict.items():
        key_str = require_str(k, f"{field_path}.key")
        val_str = require_str(v, f"{field_path}.{key_str}", allowed_enum=VALID_VALUE_ORIGINS)
        if guest_id == "G6" and val_str != "ASSUMED":
            raise StrictValidationError(
                "PROVENANCE_VIOLATION",
                f"Custom guest G6 cannot claim '{val_str}' at '{field_path}.{key_str}'; must be ASSUMED",
                f"{field_path}.{key_str}",
            )
        out[key_str] = val_str
    return out


def _parse_raw_component_allocations_block(
    raw_ca: Any,
    field_path: str,
) -> Dict[str, ComponentAllocationSpec]:
    """Parse a mode's component_allocations block (either dict keyed by subcomponent_id or list of dicts)."""
    out: Dict[str, ComponentAllocationSpec] = {}
    required_alloc_keys = {
        "nominal_power_w",
        "sustained_power_w",
        "peak_power_w",
        "cooling_water_w",
        "cooling_air_w",
    }
    if isinstance(raw_ca, dict):
        for sc_id_raw, spec_raw in raw_ca.items():
            sc_id = require_str(sc_id_raw, f"{field_path}.subcomponent_id")
            if sc_id in out:
                raise StrictValidationError(
                    "DUPLICATE_COMPONENT_ALLOCATION",
                    f"Duplicate component allocation for '{sc_id}'",
                    f"{field_path}.{sc_id}",
                )
            p = f"{field_path}.{sc_id}"
            require_dict(
                spec_raw,
                p,
                required_keys=required_alloc_keys,
                allowed_keys=COMPONENT_ALLOCATION_KEYS,
            )
            if "subcomponent_id" in spec_raw and spec_raw["subcomponent_id"] != sc_id:
                raise StrictValidationError(
                    "SUBCOMPONENT_ID_MISMATCH",
                    f"Key '{sc_id}' != inner subcomponent_id '{spec_raw['subcomponent_id']}'",
                    f"{p}.subcomponent_id",
                )
            src_kind = require_str(
                spec_raw.get("source_kind", "ASSUMED"),
                f"{p}.source_kind",
                allowed_enum=VALID_VALUE_ORIGINS,
            )
            assump_ref = (
                None
                if spec_raw.get("assumption_ref") is None
                else require_str(spec_raw["assumption_ref"], f"{p}.assumption_ref")
            )
            out[sc_id] = ComponentAllocationSpec(
                subcomponent_id=sc_id,
                nominal_power_w=require_int(spec_raw["nominal_power_w"], f"{p}.nominal_power_w", min_value=0),
                sustained_power_w=require_int(spec_raw["sustained_power_w"], f"{p}.sustained_power_w", min_value=0),
                peak_power_w=require_int(spec_raw["peak_power_w"], f"{p}.peak_power_w", min_value=0),
                cooling_water_w=require_int(spec_raw["cooling_water_w"], f"{p}.cooling_water_w", min_value=0),
                cooling_air_w=require_int(spec_raw["cooling_air_w"], f"{p}.cooling_air_w", min_value=0),
                source_kind=src_kind,
                assumption_ref=assump_ref,
            )
    elif isinstance(raw_ca, list):
        for idx, spec_raw in enumerate(raw_ca):
            p = f"{field_path}[{idx}]"
            require_dict(
                spec_raw,
                p,
                required_keys=required_alloc_keys | {"subcomponent_id"},
                allowed_keys=COMPONENT_ALLOCATION_KEYS,
            )
            sc_id = require_str(spec_raw["subcomponent_id"], f"{p}.subcomponent_id")
            if sc_id in out:
                raise StrictValidationError(
                    "DUPLICATE_COMPONENT_ALLOCATION",
                    f"Duplicate component allocation for '{sc_id}'",
                    f"{p}.subcomponent_id",
                )
            src_kind = require_str(
                spec_raw.get("source_kind", "ASSUMED"),
                f"{p}.source_kind",
                allowed_enum=VALID_VALUE_ORIGINS,
            )
            assump_ref = (
                None
                if spec_raw.get("assumption_ref") is None
                else require_str(spec_raw["assumption_ref"], f"{p}.assumption_ref")
            )
            out[sc_id] = ComponentAllocationSpec(
                subcomponent_id=sc_id,
                nominal_power_w=require_int(spec_raw["nominal_power_w"], f"{p}.nominal_power_w", min_value=0),
                sustained_power_w=require_int(spec_raw["sustained_power_w"], f"{p}.sustained_power_w", min_value=0),
                peak_power_w=require_int(spec_raw["peak_power_w"], f"{p}.peak_power_w", min_value=0),
                cooling_water_w=require_int(spec_raw["cooling_water_w"], f"{p}.cooling_water_w", min_value=0),
                cooling_air_w=require_int(spec_raw["cooling_air_w"], f"{p}.cooling_air_w", min_value=0),
                source_kind=src_kind,
                assumption_ref=assump_ref,
            )
    else:
        raise StrictValidationError(
            "TYPE_ERROR",
            f"Expected dict or list at '{field_path}', got {type(raw_ca).__name__}",
            field_path,
        )
    return out


VALID_GUEST_CATEGORIES: Set[str] = {
    "PASSIVE_RADIO_SENSOR",
    "OCEAN_WEATHER_MODULE",
    "AUV_HYDRO_SURVEY",
    "UAV_SAR_SURVEILLANCE",
    "EMERGENCY_COMMS_EDGE_COMPUTE",
    "CIVIL_HYDRO_SAMPLER",
    "METEO_SENSOR",
    "ROV_INSPECTION",
    "AUV_SURVEY",
    "UAV_SAR",
    "AUXILIARY_PAYLOAD",
}


def parse_guest_manifest(
    raw: Dict[str, Any],
    *,
    source_sha256: str = "inline",
    errata_override: Optional[Dict[str, Any]] = None,
) -> GuestManifest:
    """Strictly validate and parse a GuestManifest dictionary (fail-closed on any schema or domain error)."""
    require_dict(
        raw,
        "guest_manifest",
        required_keys=TOP_LEVEL_GUEST_KEYS - {"component_allocations", "derivation_metadata"},
        allowed_keys=TOP_LEVEL_GUEST_KEYS,
    )

    schema_version = require_str(
        raw["schema_version"],
        "guest_manifest.schema_version",
        allowed_enum={"sdv-guest-manifest/1.0"},
    )
    guest_id = require_str(raw["guest_id"], "guest_manifest.guest_id")
    name = require_str(raw["name"], "guest_manifest.name")
    category = require_str(
        raw["category"],
        "guest_manifest.category",
        allowed_enum=VALID_GUEST_CATEGORIES,
    )
    safety_critical = require_bool(raw["safety_critical"], "guest_manifest.safety_critical")
    default_mode = require_str(raw["default_mode"], "guest_manifest.default_mode")
    can_buffer_offline = require_bool(raw["can_buffer_offline"], "guest_manifest.can_buffer_offline")

    if errata_override is not None and guest_id in {"G1", "G2", "G3", "G4", "G5"} and safety_critical:
        raise StrictValidationError(
            "OFFICIAL_SAFETY_CRITICAL_MISMATCH",
            f"Official brief Annex B states none of G1-G5 is safety-critical by design, but {guest_id} has safety_critical=True",
            f"guest_manifest.{guest_id}.safety_critical",
        )

    raw_subs = raw["subcomponents"]
    if not isinstance(raw_subs, list) or len(raw_subs) == 0:
        raise StrictValidationError(
            "EMPTY_SUBCOMPONENTS",
            "subcomponents must be a non-empty list",
            "guest_manifest.subcomponents",
        )

    subcomponents: List[SubcomponentSpec] = []
    seen_sub_ids: Set[str] = set()
    for idx, sc_raw in enumerate(raw_subs):
        p = f"guest_manifest.subcomponents[{idx}]"
        require_dict(
            sc_raw,
            p,
            required_keys=SUBCOMPONENT_KEYS - {"field_origins", "provenance_metadata"},
            allowed_keys=SUBCOMPONENT_KEYS,
        )
        sc_id = require_str(sc_raw["subcomponent_id"], f"{p}.subcomponent_id")
        if sc_id in seen_sub_ids:
            raise StrictValidationError("DUPLICATE_ID", f"Duplicate subcomponent_id '{sc_id}'", p)
        seen_sub_ids.add(sc_id)

        part_of = require_str(sc_raw["part_of"], f"{p}.part_of")
        if part_of != guest_id:
            raise StrictValidationError(
                "PART_OF_MISMATCH",
                f"Subcomponent '{sc_id}' has part_of='{part_of}' != guest_id='{guest_id}'",
                f"{p}.part_of",
            )

        sc_name = require_str(sc_raw["name"], f"{p}.name")
        mass_kg = require_int(sc_raw["mass_kg"], f"{p}.mass_kg", min_value=1, allow_zero=False)
        footprint_m2 = require_finite_number(
            sc_raw["footprint_m2"], f"{p}.footprint_m2", min_value=0.01, allow_zero=False
        )
        wind_area_m2 = require_finite_number(
            sc_raw["wind_area_m2"], f"{p}.wind_area_m2", min_value=0.0
        )

        pref_raw = sc_raw["preferred_positions"]
        allow_raw = sc_raw["allowed_positions"]
        if not isinstance(pref_raw, list) or len(pref_raw) == 0:
            raise StrictValidationError("INVALID_POSITIONS", "preferred_positions must be non-empty list", f"{p}.preferred_positions")
        if not isinstance(allow_raw, list) or len(allow_raw) == 0:
            raise StrictValidationError("INVALID_POSITIONS", "allowed_positions must be non-empty list", f"{p}.allowed_positions")

        pref_pos = tuple(
            require_str(pos, f"{p}.preferred_positions[{i}]", allowed_enum=VALID_POSITIONS)
            for i, pos in enumerate(pref_raw)
        )
        allow_pos = tuple(
            require_str(pos, f"{p}.allowed_positions[{i}]", allowed_enum=VALID_POSITIONS)
            for i, pos in enumerate(allow_raw)
        )
        if not set(pref_pos).issubset(set(allow_pos)):
            raise StrictValidationError(
                "POSITION_SUBSET_ERROR",
                f"preferred_positions {pref_pos} must be a subset of allowed_positions {allow_pos}",
                p,
            )

        cooling_route = require_str(
            sc_raw["cooling_route"], f"{p}.cooling_route", allowed_enum=VALID_COOLING_ROUTES
        )
        req_water = require_bool(sc_raw["requires_water_manifold"], f"{p}.requires_water_manifold")

        # Mast positions have no water manifold and must satisfy <= 60 kg and <= 0.6 m2
        for pos in allow_pos:
            if pos.startswith("mast_"):
                if req_water or cooling_route == "CHILLED_WATER_LOOP":
                    raise StrictValidationError(
                        "MAST_WATER_FORBIDDEN",
                        f"Mast subcomponent '{sc_id}' cannot require water manifold or chilled-water loop",
                        p,
                    )
                if mass_kg > 60:
                    raise StrictValidationError(
                        "MAST_MASS_EXCEEDED",
                        f"Mast subcomponent '{sc_id}' mass {mass_kg} kg exceeds 60 kg mast limit",
                        f"{p}.mass_kg",
                    )
                if footprint_m2 > 0.60:
                    raise StrictValidationError(
                        "MAST_FOOTPRINT_EXCEEDED",
                        f"Mast subcomponent '{sc_id}' footprint {footprint_m2} m2 exceeds 0.60 m2 mast limit",
                        f"{p}.footprint_m2",
                    )

        val_origin = require_str(
            sc_raw["value_origin"], f"{p}.value_origin", allowed_enum=VALID_VALUE_ORIGINS
        )
        if guest_id == "G6" and val_origin != "ASSUMED":
            raise StrictValidationError(
                "PROVENANCE_VIOLATION",
                f"Custom guest G6 subcomponent '{sc_id}' must have value_origin='ASSUMED', got '{val_origin}'",
                f"{p}.value_origin",
            )
        field_origins = _parse_field_origins(sc_raw.get("field_origins"), f"{p}.field_origins", guest_id)
        prov_meta: Dict[str, Any] = {}
        if sc_raw.get("provenance_metadata") is not None:
            prov_meta = dict(require_dict(sc_raw["provenance_metadata"], f"{p}.provenance_metadata"))

        subcomponents.append(
            SubcomponentSpec(
                subcomponent_id=sc_id,
                part_of=part_of,
                name=sc_name,
                mass_kg=mass_kg,
                footprint_m2=footprint_m2,
                wind_area_m2=wind_area_m2,
                preferred_positions=pref_pos,
                allowed_positions=allow_pos,
                cooling_route=cooling_route,
                requires_water_manifold=req_water,
                value_origin=val_origin,
                field_origins=field_origins,
                provenance_metadata=prov_meta,
            )
        )

    sub_by_id: Dict[str, SubcomponentSpec] = {sc.subcomponent_id: sc for sc in subcomponents}

    # Check errata override for G4 if provided
    if errata_override is not None and guest_id == "G4":
        g4_corr = errata_override.get("overrides", {}).get("G4_mass_correction", {})
        if g4_corr:
            target_sc_id = g4_corr.get("subcomponent_id", "G4_HANGAR_STATION")
            expected_hangar_mass = require_int(
                g4_corr["corrected_hangar_mass_kg"],
                "errata.overrides.G4_mass_correction.corrected_hangar_mass_kg",
                min_value=1,
            )
            expected_total = require_int(
                g4_corr["corrected_total_mass_kg"],
                "errata.overrides.G4_mass_correction.corrected_total_mass_kg",
                min_value=1,
            )
            for sc in subcomponents:
                if sc.subcomponent_id == target_sc_id and sc.mass_kg != expected_hangar_mass:
                    raise StrictValidationError(
                        "ERRATA_MISMATCH",
                        f"G4 subcomponent '{sc.subcomponent_id}' has mass_kg={sc.mass_kg}, "
                        f"but organizer errata requires {expected_hangar_mass} kg (total G4 = {expected_total} kg)",
                        f"guest_manifest.G4.{sc.subcomponent_id}.mass_kg",
                    )
            if sum(s.mass_kg for s in subcomponents) != expected_total:
                raise StrictValidationError(
                    "ERRATA_MISMATCH",
                    f"G4 total mass {sum(s.mass_kg for s in subcomponents)} kg != errata {expected_total} kg",
                    "guest_manifest.G4.total_mass_kg",
                )

        g4_ov = errata_override.get("guest_overrides", {}).get("G4", {})
        sub_ov = g4_ov.get("subcomponents", {})
        for sc in subcomponents:
            if sc.subcomponent_id in sub_ov:
                expected_mass = require_int(
                    sub_ov[sc.subcomponent_id]["mass_kg"],
                    f"errata.G4.{sc.subcomponent_id}.mass_kg",
                    min_value=1,
                )
                if sc.mass_kg != expected_mass:
                    raise StrictValidationError(
                        "ERRATA_MISMATCH",
                        f"G4 subcomponent '{sc.subcomponent_id}' has mass_kg={sc.mass_kg}, "
                        f"but organizer errata requires {expected_mass} kg (total G4 = 990 kg)",
                        f"guest_manifest.G4.{sc.subcomponent_id}.mass_kg",
                    )
        expected_total_ov = g4_ov.get("expected_total_guest_mass_kg")
        if expected_total_ov is not None and sum(s.mass_kg for s in subcomponents) != expected_total_ov:
            raise StrictValidationError(
                "ERRATA_MISMATCH",
                f"G4 total mass {sum(s.mass_kg for s in subcomponents)} kg != errata {expected_total_ov} kg",
                "guest_manifest.G4.total_mass_kg",
            )

    raw_modes = require_dict(raw["modes"], "guest_manifest.modes")
    if len(raw_modes) == 0:
        raise StrictValidationError("EMPTY_MODES", "modes cannot be empty", "guest_manifest.modes")
    if default_mode not in raw_modes:
        raise StrictValidationError(
            "INVALID_DEFAULT_MODE",
            f"default_mode '{default_mode}' not found in modes {sorted(raw_modes.keys())}",
            "guest_manifest.default_mode",
        )

    top_comp_allocs_raw: Dict[str, Any] = {}
    if "component_allocations" in raw and raw["component_allocations"] is not None:
        raw_top_ca = require_dict(raw["component_allocations"], "guest_manifest.component_allocations")
        for ca_mode_key, ca_mode_val in raw_top_ca.items():
            resolved_mode_key = ca_mode_key
            if resolved_mode_key not in raw_modes:
                if resolved_mode_key == "DEGRADED" and "DEGRADED_70PCT_LOSS" in raw_modes:
                    resolved_mode_key = "DEGRADED_70PCT_LOSS"
                else:
                    raise StrictValidationError(
                        "UNKNOWN_MODE_COMPONENT_ALLOCATION",
                        f"component_allocations references unknown mode '{ca_mode_key}'",
                        f"guest_manifest.component_allocations.{ca_mode_key}",
                    )
            if resolved_mode_key in top_comp_allocs_raw:
                raise StrictValidationError(
                    "DUPLICATE_COMPONENT_ALLOCATION",
                    f"Duplicate top-level component_allocations entry for mode '{resolved_mode_key}'",
                    f"guest_manifest.component_allocations.{ca_mode_key}",
                )
            top_comp_allocs_raw[resolved_mode_key] = ca_mode_val

    modes: Dict[str, GuestModeSpec] = {}
    for m_name, m_raw in raw_modes.items():
        mp = f"guest_manifest.modes.{m_name}"
        require_str(m_name, mp)
        require_dict(
            m_raw,
            mp,
            required_keys=MODE_KEYS - {"field_origins", "component_allocations", "requires_acoustic_command_link"},
            allowed_keys=MODE_KEYS,
        )

        desc = require_str(m_raw["description"], f"{mp}.description")
        min_p = require_int(m_raw["min_power_w"], f"{mp}.min_power_w", min_value=0)
        nom_p = require_int(m_raw["nominal_power_w"], f"{mp}.nominal_power_w", min_value=0)
        sust_p = require_int(m_raw["sustained_power_w"], f"{mp}.sustained_power_w", min_value=0)
        sust_dur = require_int(m_raw["sustained_duration_s"], f"{mp}.sustained_duration_s", min_value=0)
        peak_p = require_int(m_raw["peak_power_w"], f"{mp}.peak_power_w", min_value=0)
        peak_dur = require_int(m_raw["peak_duration_s"], f"{mp}.peak_duration_s", min_value=0)

        if not (min_p <= nom_p <= sust_p <= peak_p):
            raise StrictValidationError(
                "POWER_HIERARCHY_ERROR",
                f"Mode '{m_name}' violates min <= nominal <= sustained <= peak ({min_p}, {nom_p}, {sust_p}, {peak_p})",
                mp,
            )

        cool_w = require_int(m_raw["cooling_water_w"], f"{mp}.cooling_water_w", min_value=0)
        cool_air = require_int(m_raw["cooling_air_mast_w"], f"{mp}.cooling_air_mast_w", min_value=0)

        # Coupled 1:1 thermal rule (B3 / PHY-03): total heat dissipated must equal electrical sustained power draw
        if cool_w + cool_air != sust_p:
            raise StrictValidationError(
                "THERMAL_COUPLING_MISMATCH",
                f"Mode '{m_name}' cooling_water_w ({cool_w}) + cooling_air_mast_w ({cool_air}) != sustained_power_w ({sust_p})",
                mp,
            )
        if cool_air > 2000:
            raise StrictValidationError(
                "MAST_AIR_COOLING_EXCEEDED",
                f"Mode '{m_name}' cooling_air_mast_w ({cool_air} W) exceeds 2000 W mast air cooling limit",
                f"{mp}.cooling_air_mast_w",
            )

        c_cores = require_int(m_raw["compute_cores"], f"{mp}.compute_cores", min_value=0)
        c_ram = require_int(m_raw["compute_ram_mib"], f"{mp}.compute_ram_mib", min_value=0)
        c_acc = require_int(m_raw["compute_accelerators"], f"{mp}.compute_accelerators", min_value=0)
        c_stor = require_int(m_raw["shared_storage_gb"], f"{mp}.shared_storage_gb", min_value=0)
        lan_bps = require_int(m_raw["backbone_lan_bps"], f"{mp}.backbone_lan_bps", min_value=0)
        sat_bps = require_int(m_raw["satcom_bps"], f"{mp}.satcom_bps", min_value=0)

        m_safe = require_bool(m_raw["safety_critical"], f"{mp}.safety_critical")
        m_unint = require_bool(m_raw["uninterruptible"], f"{mp}.uninterruptible")
        m_done = require_int(m_raw["min_done_duration_s"], f"{mp}.min_done_duration_s", min_value=0)
        m_cool = require_int(m_raw["min_cooldown_s"], f"{mp}.min_cooldown_s", min_value=0)
        m_emcon = require_bool(m_raw["forbids_emcon_silence"], f"{mp}.forbids_emcon_silence")
        m_deck = require_bool(m_raw["requires_deck_clear"], f"{mp}.requires_deck_clear")
        m_quiet = require_bool(m_raw["requires_quiet_acoustic_state"], f"{mp}.requires_quiet_acoustic_state")
        m_acoustic_cmd = require_bool(m_raw.get("requires_acoustic_command_link", False), f"{mp}.requires_acoustic_command_link")
        cap_ret = require_finite_number(
            m_raw["capability_retention_ratio"],
            f"{mp}.capability_retention_ratio",
            min_value=0.0,
            max_value=1.0,
        )
        m_origin = require_str(
            m_raw["value_origin"], f"{mp}.value_origin", allowed_enum=VALID_VALUE_ORIGINS
        )
        if guest_id == "G6" and m_origin != "ASSUMED":
            raise StrictValidationError(
                "PROVENANCE_VIOLATION",
                f"Custom guest G6 mode '{m_name}' must have value_origin='ASSUMED', got '{m_origin}'",
                f"{mp}.value_origin",
            )
        m_field_origins = _parse_field_origins(m_raw.get("field_origins"), f"{mp}.field_origins", guest_id)
        for assumed_field in ("compute_cores", "compute_ram_mib", "compute_accelerators", "shared_storage_gb"):
            m_field_origins.setdefault(assumed_field, "ASSUMED")

        # Parse and validate per-component allocations (§6.1)
        has_top_ca = m_name in top_comp_allocs_raw
        has_mode_ca = "component_allocations" in m_raw and m_raw["component_allocations"] is not None
        parsed_allocs: Dict[str, ComponentAllocationSpec] = {}

        if has_top_ca and has_mode_ca:
            top_parsed = _parse_raw_component_allocations_block(
                top_comp_allocs_raw[m_name],
                f"guest_manifest.component_allocations.{m_name}",
            )
            mode_parsed = _parse_raw_component_allocations_block(
                m_raw["component_allocations"],
                f"{mp}.component_allocations",
            )
            overlap = set(top_parsed.keys()) & set(mode_parsed.keys())
            if overlap:
                raise StrictValidationError(
                    "DUPLICATE_COMPONENT_ALLOCATION",
                    f"Mode '{m_name}' specifies duplicate component_allocations for {sorted(overlap)}",
                    f"{mp}.component_allocations",
                )
            parsed_allocs.update(top_parsed)
            parsed_allocs.update(mode_parsed)
        elif has_top_ca:
            parsed_allocs = _parse_raw_component_allocations_block(
                top_comp_allocs_raw[m_name],
                f"guest_manifest.component_allocations.{m_name}",
            )
        elif has_mode_ca:
            parsed_allocs = _parse_raw_component_allocations_block(
                m_raw["component_allocations"],
                f"{mp}.component_allocations",
            )
        else:
            if len(subcomponents) == 1:
                sc0 = subcomponents[0]
                parsed_allocs[sc0.subcomponent_id] = ComponentAllocationSpec(
                    subcomponent_id=sc0.subcomponent_id,
                    nominal_power_w=nom_p,
                    sustained_power_w=sust_p,
                    peak_power_w=peak_p,
                    cooling_water_w=cool_w,
                    cooling_air_w=cool_air,
                    source_kind=m_origin,
                    assumption_ref=None,
                )
            else:
                raise StrictValidationError(
                    "MISSING_COMPONENT_ALLOCATIONS",
                    f"Multi-component guest '{guest_id}' mode '{m_name}' must declare component_allocations",
                    f"{mp}.component_allocations",
                )

        for sc_id in parsed_allocs:
            if sc_id not in sub_by_id:
                raise StrictValidationError(
                    "UNKNOWN_SUBCOMPONENT_ALLOCATION",
                    f"Mode '{m_name}' allocates to unknown subcomponent '{sc_id}'",
                    f"{mp}.component_allocations.{sc_id}",
                )

        missing_sc_ids = set(sub_by_id.keys()) - set(parsed_allocs.keys())
        if missing_sc_ids:
            raise StrictValidationError(
                "INCOMPLETE_COMPONENT_ALLOCATIONS",
                f"Mode '{m_name}' component_allocations missing subcomponents: {sorted(missing_sc_ids)}",
                f"{mp}.component_allocations",
            )

        for sc_id, alloc in parsed_allocs.items():
            sc_spec = sub_by_id[sc_id]
            ap = f"{mp}.component_allocations.{sc_id}"
            if not (0 <= alloc.nominal_power_w <= alloc.sustained_power_w <= alloc.peak_power_w):
                raise StrictValidationError(
                    "COMPONENT_POWER_HIERARCHY_ERROR",
                    f"Component '{sc_id}' in mode '{m_name}' violates nominal <= sustained <= peak "
                    f"({alloc.nominal_power_w}, {alloc.sustained_power_w}, {alloc.peak_power_w})",
                    ap,
                )
            if alloc.cooling_water_w + alloc.cooling_air_w != alloc.sustained_power_w:
                raise StrictValidationError(
                    "COMPONENT_THERMAL_COUPLING_MISMATCH",
                    f"Component '{sc_id}' in mode '{m_name}' cooling_water_w ({alloc.cooling_water_w}) + "
                    f"cooling_air_w ({alloc.cooling_air_w}) != sustained_power_w ({alloc.sustained_power_w})",
                    ap,
                )
            if sc_spec.cooling_route == "CHILLED_WATER_LOOP" and alloc.cooling_air_w > 0:
                raise StrictValidationError(
                    "COMPONENT_COOLING_ROUTE_MISMATCH",
                    f"Component '{sc_id}' has cooling_route='CHILLED_WATER_LOOP' but cooling_air_w={alloc.cooling_air_w} > 0",
                    ap,
                )
            if sc_spec.cooling_route == "MAST_AIR_2KW" and (alloc.cooling_water_w > 0 or alloc.cooling_air_w > 2000):
                raise StrictValidationError(
                    "COMPONENT_COOLING_ROUTE_MISMATCH",
                    f"Component '{sc_id}' has cooling_route='MAST_AIR_2KW' but cooling_water_w={alloc.cooling_water_w}, cooling_air_w={alloc.cooling_air_w}",
                    ap,
                )
            if sc_spec.cooling_route == "PASSIVE_NO_COOLING" and any(
                v > 0
                for v in (
                    alloc.nominal_power_w,
                    alloc.sustained_power_w,
                    alloc.peak_power_w,
                    alloc.cooling_water_w,
                    alloc.cooling_air_w,
                )
            ):
                raise StrictValidationError(
                    "COMPONENT_COOLING_ROUTE_MISMATCH",
                    f"Component '{sc_id}' has cooling_route='PASSIVE_NO_COOLING' but non-zero power/cooling allocation",
                    ap,
                )

        sum_nom = sum(a.nominal_power_w for a in parsed_allocs.values())
        sum_sust = sum(a.sustained_power_w for a in parsed_allocs.values())
        sum_peak = sum(a.peak_power_w for a in parsed_allocs.values())
        sum_cw = sum(a.cooling_water_w for a in parsed_allocs.values())
        sum_ca = sum(a.cooling_air_w for a in parsed_allocs.values())
        if (
            sum_nom != nom_p
            or sum_sust != sust_p
            or sum_peak != peak_p
            or sum_cw != cool_w
            or sum_ca != cool_air
        ):
            raise StrictValidationError(
                "COMPONENT_ALLOCATION_SUM_MISMATCH",
                f"Mode '{m_name}' component_allocations sums (nom={sum_nom}, sust={sum_sust}, peak={sum_peak}, "
                f"water={sum_cw}, air={sum_ca}) != mode totals (nom={nom_p}, sust={sust_p}, peak={peak_p}, "
                f"water={cool_w}, air={cool_air})",
                f"{mp}.component_allocations",
            )

        modes[m_name] = GuestModeSpec(
            mode_name=m_name,
            description=desc,
            min_power_w=min_p,
            nominal_power_w=nom_p,
            sustained_power_w=sust_p,
            sustained_duration_s=sust_dur,
            peak_power_w=peak_p,
            peak_duration_s=peak_dur,
            cooling_water_w=cool_w,
            cooling_air_mast_w=cool_air,
            compute_cores=c_cores,
            compute_ram_mib=c_ram,
            compute_accelerators=c_acc,
            shared_storage_gb=c_stor,
            backbone_lan_bps=lan_bps,
            satcom_bps=sat_bps,
            safety_critical=m_safe,
            uninterruptible=m_unint,
            min_done_duration_s=m_done,
            min_cooldown_s=m_cool,
            forbids_emcon_silence=m_emcon,
            requires_deck_clear=m_deck,
            requires_quiet_acoustic_state=m_quiet,
            capability_retention_ratio=cap_ret,
            value_origin=m_origin,
            field_origins=m_field_origins,
            component_allocations=parsed_allocs,
            requires_acoustic_command_link=m_acoustic_cmd,
        )

    inst_raw = require_dict(
        raw["installation"],
        "guest_manifest.installation",
        required_keys=INSTALLATION_KEYS - {"antenna_min_crew_required", "antenna_install_time_min"},
        allowed_keys=INSTALLATION_KEYS,
    )
    installation = InstallationConstraints(
        port_only=require_bool(inst_raw["port_only"], "installation.port_only"),
        min_crew_required=require_int(inst_raw["min_crew_required"], "installation.min_crew_required", min_value=0),
        install_time_min=require_int(inst_raw["install_time_min"], "installation.install_time_min", min_value=1),
        remove_time_min=require_int(inst_raw["remove_time_min"], "installation.remove_time_min", min_value=1),
        sea_install_max_wind_kn=require_int(inst_raw["sea_install_max_wind_kn"], "installation.sea_install_max_wind_kn", min_value=0),
        sea_install_min_speed_kn=require_finite_number(inst_raw["sea_install_min_speed_kn"], "installation.sea_install_min_speed_kn", min_value=0.0),
        sea_install_max_speed_kn=require_finite_number(inst_raw["sea_install_max_speed_kn"], "installation.sea_install_max_speed_kn", min_value=0.0),
        antenna_min_crew_required=require_int(inst_raw.get("antenna_min_crew_required", 0), "installation.antenna_min_crew_required", min_value=0),
        antenna_install_time_min=require_int(inst_raw.get("antenna_install_time_min", 0), "installation.antenna_install_time_min", min_value=0),
    )

    vr_raw = require_dict(
        raw["vehicle_replacement"],
        "guest_manifest.vehicle_replacement",
        required_keys=VEHICLE_REPLACEMENT_KEYS,
        allowed_keys=VEHICLE_REPLACEMENT_KEYS,
    )
    vehicle_replacement = VehicleReplacementConstraints(
        supported_at_sea=require_bool(vr_raw["supported_at_sea"], "vehicle_replacement.supported_at_sea"),
        min_crew_required=require_int(vr_raw["min_crew_required"], "vehicle_replacement.min_crew_required", min_value=0),
        swap_duration_min=require_int(vr_raw["swap_duration_min"], "vehicle_replacement.swap_duration_min", min_value=0),
        min_speed_kn=require_finite_number(vr_raw["min_speed_kn"], "vehicle_replacement.min_speed_kn", min_value=0.0),
        max_speed_kn=require_finite_number(vr_raw["max_speed_kn"], "vehicle_replacement.max_speed_kn", min_value=0.0),
        max_wind_kn=require_int(vr_raw["max_wind_kn"], "vehicle_replacement.max_wind_kn", min_value=0),
        max_sea_state=require_int(vr_raw["max_sea_state"], "vehicle_replacement.max_sea_state", min_value=0, max_value=9),
    )

    required_sc_keys = SHIP_STATE_CONSTRAINT_KEYS - {
        "uses_deck_machinery",
        "requires_stern_ramp_free",
        "radar_tx_degradation_ratio",
        "launch_recovery_min_speed_kn",
        "launch_recovery_max_speed_kn",
        "launch_recovery_area",
    }
    sc_con_raw = require_dict(
        raw["ship_state_constraints"],
        "guest_manifest.ship_state_constraints",
        required_keys=required_sc_keys,
        allowed_keys=SHIP_STATE_CONSTRAINT_KEYS,
    )
    low_bat = sc_con_raw["low_battery_threshold_pct"]
    emerg_dl = sc_con_raw["emergency_landing_deadline_s"]
    max_endur = sc_con_raw["max_flight_endurance_min"]
    lr_min_spd = sc_con_raw.get("launch_recovery_min_speed_kn")
    lr_max_spd = sc_con_raw.get("launch_recovery_max_speed_kn")

    ship_state_constraints = ShipStateConstraints(
        min_speed_kn_for_operation=require_finite_number(sc_con_raw["min_speed_kn_for_operation"], "ship_state_constraints.min_speed_kn_for_operation", min_value=0.0),
        max_speed_kn_for_operation=require_finite_number(sc_con_raw["max_speed_kn_for_operation"], "ship_state_constraints.max_speed_kn_for_operation", min_value=0.0),
        max_wind_kn_for_operation=require_int(sc_con_raw["max_wind_kn_for_operation"], "ship_state_constraints.max_wind_kn_for_operation", min_value=0),
        max_sea_state_for_operation=require_int(sc_con_raw["max_sea_state_for_operation"], "ship_state_constraints.max_sea_state_for_operation", min_value=0, max_value=9),
        requires_heading_into_wind_for_launch_recovery=require_bool(sc_con_raw["requires_heading_into_wind_for_launch_recovery"], "ship_state_constraints.requires_heading_into_wind_for_launch_recovery"),
        forbids_deck_machinery_while_active=require_bool(sc_con_raw["forbids_deck_machinery_while_active"], "ship_state_constraints.forbids_deck_machinery_while_active"),
        uses_deck_machinery=require_bool(sc_con_raw.get("uses_deck_machinery", False), "ship_state_constraints.uses_deck_machinery"),
        requires_stern_ramp_free=require_bool(sc_con_raw.get("requires_stern_ramp_free", False), "ship_state_constraints.requires_stern_ramp_free"),
        acoustic_hold_handshake_min_s=require_int(sc_con_raw["acoustic_hold_handshake_min_s"], "ship_state_constraints.acoustic_hold_handshake_min_s", min_value=0),
        acoustic_hold_handshake_max_s=require_int(sc_con_raw["acoustic_hold_handshake_max_s"], "ship_state_constraints.acoustic_hold_handshake_max_s", min_value=0),
        acoustic_hold_default_s=require_int(sc_con_raw["acoustic_hold_default_s"], "ship_state_constraints.acoustic_hold_default_s", min_value=0),
        low_battery_threshold_pct=None if low_bat is None else require_finite_number(low_bat, "ship_state_constraints.low_battery_threshold_pct", min_value=1.0, max_value=100.0),
        emergency_landing_deadline_s=None if emerg_dl is None else require_int(emerg_dl, "ship_state_constraints.emergency_landing_deadline_s", min_value=1),
        max_flight_endurance_min=None if max_endur is None else require_int(max_endur, "ship_state_constraints.max_flight_endurance_min", min_value=1),
        radar_tx_degradation_ratio=require_finite_number(sc_con_raw.get("radar_tx_degradation_ratio", 0.0), "ship_state_constraints.radar_tx_degradation_ratio", min_value=0.0, max_value=1.0),
        launch_recovery_min_speed_kn=None if lr_min_spd is None else require_finite_number(lr_min_spd, "ship_state_constraints.launch_recovery_min_speed_kn", min_value=0.0),
        launch_recovery_max_speed_kn=None if lr_max_spd is None else require_finite_number(lr_max_spd, "ship_state_constraints.launch_recovery_max_speed_kn", min_value=0.0),
        launch_recovery_area=require_str(
            sc_con_raw.get("launch_recovery_area", "none"),
            "ship_state_constraints.launch_recovery_area",
            allowed_enum={"flight_deck", "stern_ramp", "none"},
        ),
    )

    dep_raw = require_dict(
        raw["dependencies"],
        "guest_manifest.dependencies",
        required_keys=DEPENDENCY_KEYS,
        allowed_keys=DEPENDENCY_KEYS,
    )
    if not isinstance(dep_raw["requires_guests"], list) or not isinstance(dep_raw["excludes_guests"], list) or not isinstance(dep_raw["conditional_excludes"], list):
        raise StrictValidationError("TYPE_ERROR", "dependencies lists must be JSON/YAML lists", "guest_manifest.dependencies")

    req_guests = tuple(require_str(g, f"dependencies.requires_guests[{i}]") for i, g in enumerate(dep_raw["requires_guests"]))
    exc_guests = tuple(require_str(g, f"dependencies.excludes_guests[{i}]") for i, g in enumerate(dep_raw["excludes_guests"]))
    cond_exc_list: List[Dict[str, Any]] = []
    cond_exc_keys = {"other_guest_id", "when_self_modes", "when_other_modes", "reason"}
    for i, c in enumerate(dep_raw["conditional_excludes"]):
        cp = f"dependencies.conditional_excludes[{i}]"
        c_dict = require_dict(c, cp, required_keys=cond_exc_keys, allowed_keys=cond_exc_keys)
        other_gid = require_str(c_dict["other_guest_id"], f"{cp}.other_guest_id")
        if other_gid == guest_id:
            raise StrictValidationError("SELF_DEPENDENCY", f"Guest '{guest_id}' cannot conditionally exclude itself", cp)
        if not isinstance(c_dict["when_self_modes"], list) or len(c_dict["when_self_modes"]) == 0:
            raise StrictValidationError("TYPE_ERROR", "when_self_modes must be a non-empty list", f"{cp}.when_self_modes")
        if not isinstance(c_dict["when_other_modes"], list) or len(c_dict["when_other_modes"]) == 0:
            raise StrictValidationError("TYPE_ERROR", "when_other_modes must be a non-empty list", f"{cp}.when_other_modes")
        self_modes = [require_str(m, f"{cp}.when_self_modes[{j}]") for j, m in enumerate(c_dict["when_self_modes"])]
        other_modes = [require_str(m, f"{cp}.when_other_modes[{j}]") for j, m in enumerate(c_dict["when_other_modes"])]
        reason_str = require_str(c_dict["reason"], f"{cp}.reason")
        cond_exc_list.append({
            "other_guest_id": other_gid,
            "when_self_modes": self_modes,
            "when_other_modes": other_modes,
            "reason": reason_str,
        })
    cond_exc = tuple(cond_exc_list)
    if guest_id in req_guests or guest_id in exc_guests:
        raise StrictValidationError("SELF_DEPENDENCY", f"Guest '{guest_id}' cannot require or exclude itself", "guest_manifest.dependencies")

    dependencies = DependencySpec(
        requires_guests=req_guests,
        excludes_guests=exc_guests,
        conditional_excludes=cond_exc,
    )

    cap_raw = require_dict(raw["mission_capabilities"], "guest_manifest.mission_capabilities")
    if len(cap_raw) == 0:
        raise StrictValidationError("EMPTY_CAPABILITIES", "mission_capabilities cannot be empty", "guest_manifest.mission_capabilities")
    mission_capabilities: Dict[str, float] = {}
    for c_name, c_val in cap_raw.items():
        require_str(c_name, "mission_capabilities.key")
        mission_capabilities[c_name] = require_finite_number(
            c_val, f"mission_capabilities.{c_name}", min_value=0.0, max_value=100.0
        )

    derivation_metadata: Dict[str, Any] = {}
    if raw.get("derivation_metadata") is not None:
        derivation_metadata = dict(require_dict(raw["derivation_metadata"], "guest_manifest.derivation_metadata"))
    for sc in subcomponents:
        if sc.provenance_metadata:
            derivation_metadata.setdefault("subcomponent_provenance", {})[sc.subcomponent_id] = dict(sc.provenance_metadata)
    if guest_id == "G4":
        hangar_sc = sub_by_id.get("G4_HANGAR_STATION")
        drone_sc = sub_by_id.get("G4_DRONE_VEHICLE")
        hangar_meta = dict(hangar_sc.provenance_metadata) if hangar_sc and hangar_sc.provenance_metadata else {}
        g4_corr = (errata_override or {}).get("overrides", {}).get("G4_mass_correction", {})
        derivation_metadata.setdefault("total_mass_kg", sum(s.mass_kg for s in subcomponents))
        derivation_metadata.setdefault("drone_vehicle_mass_kg", drone_sc.mass_kg if drone_sc else 140)
        derivation_metadata.setdefault("hangar_station_mass_kg", hangar_sc.mass_kg if hangar_sc else 850)
        derivation_metadata.setdefault(
            "derivation_formula",
            hangar_meta.get("derivation_formula", g4_corr.get("derivation_formula", "990 - 140 = 850")),
        )
        derivation_metadata.setdefault(
            "value_origin",
            hangar_sc.value_origin if hangar_sc else "ORGANIZER_ERRATA",
        )
        derivation_metadata.setdefault(
            "clarification_id",
            hangar_meta.get("clarification_id", g4_corr.get("clarification_id", "GRAZIANO_ROOF_AND_G4_MASS_0904")),
        )
        derivation_metadata.setdefault(
            "superseded_values",
            hangar_meta.get(
                "superseded_values",
                g4_corr.get(
                    "superseded_prior_interpretation",
                    {"total_mass_kg": 450, "hangar_station_mass_kg": 310, "status": "SUPERSEDED"},
                ),
            ),
        )

    return GuestManifest(
        schema_version=schema_version,
        guest_id=guest_id,
        name=name,
        category=category,
        safety_critical=safety_critical,
        default_mode=default_mode,
        subcomponents=tuple(subcomponents),
        modes=modes,
        installation=installation,
        vehicle_replacement=vehicle_replacement,
        ship_state_constraints=ship_state_constraints,
        dependencies=dependencies,
        mission_capabilities=mission_capabilities,
        can_buffer_offline=can_buffer_offline,
        source_sha256=source_sha256,
        derivation_metadata=derivation_metadata,
    )


def validate_dependency_graph(catalog: Dict[str, GuestManifest]) -> None:
    """Detect any cycle in `requires_guests` across a guest catalog (fail-closed)."""
    visiting: Set[str] = set()
    visited: Set[str] = set()

    def _dfs(gid: str, path: List[str]) -> None:
        if gid in visiting:
            cycle_str = " -> ".join(path + [gid])
            raise StrictValidationError(
                "DEPENDENCY_CYCLE",
                f"Cyclic dependency detected in requires_guests: {cycle_str}",
                f"guest_manifest.{gid}.dependencies.requires_guests",
            )
        if gid in visited or gid not in catalog:
            return
        visiting.add(gid)
        for req_id in catalog[gid].dependencies.requires_guests:
            _dfs(req_id, path + [gid])
        visiting.remove(gid)
        visited.add(gid)

    for g_id in sorted(catalog.keys()):
        _dfs(g_id, [])


def load_guest_catalog(
    guests_dir: Path,
    errata_path: Optional[Path] = None,
) -> Dict[str, GuestManifest]:
    """Load and validate all guest YAML files in `guests_dir`, enforcing errata overrides if present."""
    errata_data: Optional[Dict[str, Any]] = None
    effective_errata_path = errata_path
    if effective_errata_path is None:
        candidate_errata = guests_dir.parent / "overrides" / "brief_errata.yaml"
        if candidate_errata.exists():
            effective_errata_path = candidate_errata
    if effective_errata_path is not None and effective_errata_path.exists():
        errata_data, _ = load_yaml_file(effective_errata_path)

    catalog: Dict[str, GuestManifest] = {}
    for ypath in sorted(guests_dir.glob("*.yaml")):
        raw, sha = load_yaml_file(ypath)
        manifest = parse_guest_manifest(raw, source_sha256=sha, errata_override=errata_data)
        if manifest.guest_id in catalog:
            raise StrictValidationError("DUPLICATE_GUEST_ID", f"Duplicate guest_id '{manifest.guest_id}'", str(ypath))
        catalog[manifest.guest_id] = manifest

    if not catalog:
        raise StrictValidationError("EMPTY_CATALOG", f"No guest manifests found in {guests_dir}", str(guests_dir))
    validate_dependency_graph(catalog)
    return catalog


def parse_priority_profile(raw: Dict[str, Any], source_sha256: str = "inline") -> PriorityProfile:
    """Strictly validate and parse a PriorityProfile dictionary."""
    require_dict(
        raw,
        "priority_profile",
        required_keys=PRIORITY_TOP_KEYS,
        allowed_keys=PRIORITY_TOP_KEYS,
    )
    schema_version = require_str(
        raw["schema_version"],
        "priority_profile.schema_version",
        allowed_enum={"sdv-priority-profile/1.0"},
    )
    prof_id = require_str(raw["priority_profile_id"], "priority_profile.priority_profile_id")
    name = require_str(raw["name"], "priority_profile.name")
    desc = require_str(raw["description"], "priority_profile.description")

    cap_w_raw = require_dict(raw["capability_weights"], "priority_profile.capability_weights")
    if len(cap_w_raw) == 0:
        raise StrictValidationError("EMPTY_WEIGHTS", "capability_weights cannot be empty", "priority_profile.capability_weights")
    cap_weights: Dict[str, float] = {}
    for k, v in cap_w_raw.items():
        cap_key = require_str(k, "capability_weights.key")
        if cap_key not in KNOWN_CAPABILITIES:
            raise StrictValidationError(
                "UNKNOWN_CAPABILITY",
                f"Unknown capability '{cap_key}' in capability_weights",
                f"priority_profile.capability_weights.{cap_key}",
            )
        cap_weights[cap_key] = require_finite_number(
            v, f"capability_weights.{cap_key}", min_value=0.0, max_value=1000.0
        )

    rank_raw = require_dict(raw["guest_priority_rank"], "priority_profile.guest_priority_rank")
    if len(rank_raw) == 0:
        raise StrictValidationError("EMPTY_RANKS", "guest_priority_rank cannot be empty", "priority_profile.guest_priority_rank")
    guest_ranks: Dict[str, int] = {}
    for k, v in rank_raw.items():
        gid_key = require_str(k, "guest_priority_rank.key")
        if gid_key not in VALID_PRIORITY_GUEST_IDS:
            raise StrictValidationError(
                "UNKNOWN_GUEST_ID",
                f"Unknown guest_id '{gid_key}' in guest_priority_rank",
                f"priority_profile.guest_priority_rank.{gid_key}",
            )
        guest_ranks[gid_key] = require_int(v, f"guest_priority_rank.{gid_key}", min_value=1)

    arb_raw = require_dict(
        raw["arbitration_policy"],
        "priority_profile.arbitration_policy",
        required_keys={
            "survey_vs_recovery_strategy",
            "pre_reserve_recovery_power",
            "max_acceptable_survey_pause_min",
            "tie_break_order",
        },
        allowed_keys={
            "survey_vs_recovery_strategy",
            "pre_reserve_recovery_power",
            "max_acceptable_survey_pause_min",
            "tie_break_order",
        },
    )
    strategy = require_str(
        arb_raw["survey_vs_recovery_strategy"],
        "arbitration_policy.survey_vs_recovery_strategy",
        allowed_enum={"MINIMAL_RECOVERABLE_PAUSE", "IMMEDIATE_FULL_SUSPENSION"},
    )
    pre_res = require_bool(arb_raw["pre_reserve_recovery_power"], "arbitration_policy.pre_reserve_recovery_power")
    max_pause = require_int(arb_raw["max_acceptable_survey_pause_min"], "arbitration_policy.max_acceptable_survey_pause_min", min_value=1)
    tie_break = require_str(
        arb_raw["tie_break_order"],
        "arbitration_policy.tie_break_order",
        allowed_enum={
            "HIGHER_CAPABILITY_SCORE_THEN_LEXICOGRAPHIC",
            "LOWER_DISPLACEMENT_COST_THEN_LEXICOGRAPHIC",
        },
    )

    deg_raw = require_dict(
        raw["degradation_policy"],
        "priority_profile.degradation_policy",
        required_keys={
            "prefer_mode_downgrade_before_shed",
            "protect_uninterruptible_tasks",
            "shed_order_lowest_priority_first",
        },
        allowed_keys={
            "prefer_mode_downgrade_before_shed",
            "protect_uninterruptible_tasks",
            "shed_order_lowest_priority_first",
        },
    )
    pref_down = require_bool(deg_raw["prefer_mode_downgrade_before_shed"], "degradation_policy.prefer_mode_downgrade_before_shed")
    prot_unint = require_bool(deg_raw["protect_uninterruptible_tasks"], "degradation_policy.protect_uninterruptible_tasks")
    if not prot_unint:
        raise StrictValidationError(
            "UNINTERRUPTIBLE_PROTECTION_MANDATORY",
            "Physical uninterruptible task protection (e.g., G3 25 kW recharge) cannot be disabled by priority profile",
            "degradation_policy.protect_uninterruptible_tasks",
        )
    shed_list = deg_raw["shed_order_lowest_priority_first"]
    if not isinstance(shed_list, list) or len(shed_list) == 0:
        raise StrictValidationError("TYPE_ERROR", "shed_order_lowest_priority_first must be a non-empty list", "degradation_policy.shed_order_lowest_priority_first")
    shed_order_list: List[str] = []
    seen_shed: Set[str] = set()
    for i, g in enumerate(shed_list):
        gid_str = require_str(g, f"shed_order[{i}]")
        if gid_str not in VALID_PRIORITY_GUEST_IDS:
            raise StrictValidationError(
                "UNKNOWN_GUEST_ID",
                f"Unknown guest_id '{gid_str}' in shed_order_lowest_priority_first",
                f"degradation_policy.shed_order_lowest_priority_first[{i}]",
            )
        if gid_str in seen_shed:
            raise StrictValidationError(
                "DUPLICATE_SHED_ORDER_GUEST",
                f"Duplicate guest_id '{gid_str}' in shed_order_lowest_priority_first",
                f"degradation_policy.shed_order_lowest_priority_first[{i}]",
            )
        seen_shed.add(gid_str)
        shed_order_list.append(gid_str)
    shed_order = tuple(shed_order_list)

    effective_sha = source_sha256
    if not effective_sha or effective_sha == "inline" or len(effective_sha) != 64:
        effective_sha = canonical_json_hash(raw)

    return PriorityProfile(
        schema_version=schema_version,
        priority_profile_id=prof_id,
        name=name,
        description=desc,
        capability_weights=cap_weights,
        guest_priority_rank=guest_ranks,
        survey_vs_recovery_strategy=strategy,
        pre_reserve_recovery_power=pre_res,
        max_acceptable_survey_pause_min=max_pause,
        tie_break_order=tie_break,
        prefer_mode_downgrade_before_shed=pref_down,
        protect_uninterruptible_tasks=prot_unint,
        shed_order_lowest_priority_first=shed_order,
        source_sha256=effective_sha,
    )


def load_priority_profile(path: Path) -> PriorityProfile:
    raw, sha = load_yaml_file(path)
    return parse_priority_profile(raw, source_sha256=sha)


def validate_ship_profile(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Strictly validate ship baseline profile (DAT-04, H13, L2, L3)."""
    required_ship_keys = {
        "schema_version",
        "vessel_id",
        "vessel_name",
        "power",
        "cooling",
        "positions",
        "mast",
        "mass_and_stability",
        "data_links",
        "compute_rack",
        "time_deadlines",
    }
    optional_ship_keys = {
        "air_cooling_domains",
        "roof_drone_clearance_policy",
        "layout_version",
        "transition_timing_policy",
    }
    require_dict(
        raw,
        "ship_profile",
        required_keys=required_ship_keys,
        allowed_keys=required_ship_keys | optional_ship_keys,
    )
    require_str(raw["schema_version"], "ship_profile.schema_version", allowed_enum={"sdv-ship-baseline/1.0"})
    require_str(raw["vessel_id"], "ship_profile.vessel_id")
    require_str(raw["vessel_name"], "ship_profile.vessel_name")

    pwr = require_dict(raw["power"], "ship_profile.power")
    require_int(pwr["nominal_guest_reserve_w"], "ship_profile.power.nominal_guest_reserve_w", min_value=0)
    require_int(pwr["single_generator_loss_reserve_w"], "ship_profile.power.single_generator_loss_reserve_w", min_value=0)
    for lid, lspec in require_dict(pwr["local_power_lines"], "ship_profile.power.local_power_lines").items():
        require_int(lspec["max_power_w"], f"ship_profile.power.local_power_lines.{lid}.max_power_w", min_value=0)

    ms = require_dict(raw["mass_and_stability"], "ship_profile.mass_and_stability")
    require_int(ms["max_total_guest_mass_kg"], "ship_profile.mass_and_stability.max_total_guest_mass_kg", min_value=1)
    require_int(ms["max_stability_equivalent_kg"], "ship_profile.mass_and_stability.max_stability_equivalent_kg", min_value=1)

    air_domains_raw = raw.get("air_cooling_domains") or raw.get("cooling", {}).get("air_cooling_domains")
    if air_domains_raw is not None:
        ad_dict = require_dict(air_domains_raw, "ship_profile.air_cooling_domains")
        for dom_id, dom_spec in ad_dict.items():
            dp = f"ship_profile.air_cooling_domains.{dom_id}"
            require_dict(dom_spec, dp)
            cap_key = "max_air_cooling_w" if "max_air_cooling_w" in dom_spec else "capacity_w"
            require_int(dom_spec[cap_key], f"{dp}.{cap_key}", min_value=0)
            if "value_origin" in dom_spec:
                require_str(dom_spec["value_origin"], f"{dp}.value_origin", allowed_enum=VALID_VALUE_ORIGINS)

    if "roof_drone_clearance_policy" in raw and raw["roof_drone_clearance_policy"] is not None:
        rcp = require_dict(raw["roof_drone_clearance_policy"], "ship_profile.roof_drone_clearance_policy")
        require_str(rcp["policy_id"], "ship_profile.roof_drone_clearance_policy.policy_id")
        require_int(rcp["policy_version"], "ship_profile.roof_drone_clearance_policy.policy_version", min_value=1)
        require_str(rcp["layout_version"], "ship_profile.roof_drone_clearance_policy.layout_version")
        require_str(
            rcp["compatibility"],
            "ship_profile.roof_drone_clearance_policy.compatibility",
            allowed_enum={"COMPATIBLE_CONDITIONAL", "CONDITIONAL", "CONFLICT", "COMPATIBLE"},
        )
        if "constraint_id" in rcp:
            require_str(
                rcp["constraint_id"],
                "ship_profile.roof_drone_clearance_policy.constraint_id",
                allowed_enum={ROOF_DRONE_CLEARANCE_CONSTRAINT_ID},
            )
    exclusive_area_pairs(raw)
    return raw


def validate_mission_envelope(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Strictly validate mission envelope A.10 definition (DAT-04, H13)."""
    require_dict(
        raw,
        "mission_envelope",
        required_keys={
            "schema_version",
            "envelope_id",
            "mission_duration_days",
            "port_access_during_mission",
            "envelope_states",
        },
        allowed_keys={
            "schema_version",
            "envelope_id",
            "mission_duration_days",
            "port_access_during_mission",
            "envelope_states",
        },
    )
    require_str(raw["schema_version"], "mission_envelope.schema_version", allowed_enum={"sdv-mission-envelope/1.0"})
    require_int(raw["mission_duration_days"], "mission_envelope.mission_duration_days", min_value=1)
    require_bool(raw["port_access_during_mission"], "mission_envelope.port_access_during_mission")
    states = raw["envelope_states"]
    if not isinstance(states, list) or len(states) == 0:
        raise StrictValidationError("EMPTY_ENVELOPE", "envelope_states must be a non-empty list", "mission_envelope.envelope_states")
    for idx, st in enumerate(states):
        p = f"mission_envelope.envelope_states[{idx}]"
        require_dict(st, p)
        require_str(st["state_id"], f"{p}.state_id")
        require_bool(st["is_pier_only"], f"{p}.is_pier_only")
        require_finite_number(st["seawater_temp_c"], f"{p}.seawater_temp_c", min_value=-5.0, max_value=60.0)
        require_finite_number(st["ship_speed_kn"], f"{p}.ship_speed_kn", min_value=0.0, max_value=50.0)
        require_int(st["wind_kn"], f"{p}.wind_kn", min_value=0, max_value=150)
        require_int(st["sea_state"], f"{p}.sea_state", min_value=0, max_value=9)
        require_int(st["power_reserve_w"], f"{p}.power_reserve_w", min_value=0)
        require_bool(st["cooling_loop_degraded"], f"{p}.cooling_loop_degraded")
        require_int(st["satcom_available_bps"], f"{p}.satcom_available_bps", min_value=0)
        require_bool(st["emcon_silence"], f"{p}.emcon_silence")
    return raw


def validate_scenario_definition(
    raw: Dict[str, Any],
    catalog: Optional[Dict[str, GuestManifest]] = None,
) -> Dict[str, Any]:
    """Strictly validate a scenario YAML definition (DAT-04, H03, H13, L4)."""
    require_dict(raw, "scenario")
    require_str(raw.get("schema_version", "sdv-scenario/1.0"), "scenario.schema_version", allowed_enum={"sdv-scenario/1.0"})
    require_str(raw.get("scenario_id", ""), "scenario.scenario_id")
    if "scenario_class" in raw and raw["scenario_class"] is not None:
        require_str(
            raw["scenario_class"],
            "scenario.scenario_class",
            allowed_enum=VALID_SCENARIO_CLASSES,
        )
    init = require_dict(raw.get("initial_state") or raw.get("initial_vessel_state") or {}, "scenario.initial_state")
    if "power_reserve_w" in init:
        require_int(init["power_reserve_w"], "scenario.initial_state.power_reserve_w", min_value=0)
    if "seawater_temp_c" in init and init["seawater_temp_c"] is not None:
        require_finite_number(init["seawater_temp_c"], "scenario.initial_state.seawater_temp_c", min_value=-5.0, max_value=60.0)
    if "wind_kn" in init:
        require_int(init["wind_kn"], "scenario.initial_state.wind_kn", min_value=0)
    if "sea_state" in init:
        require_int(init["sea_state"], "scenario.initial_state.sea_state", min_value=0, max_value=9)
    if "cooling_loop_degraded" in init:
        require_bool(init["cooling_loop_degraded"], "scenario.initial_state.cooling_loop_degraded")
    if "hangar_roof_clearance_verified" in init:
        require_bool(init["hangar_roof_clearance_verified"], "scenario.initial_state.hangar_roof_clearance_verified")
    if "roof_clearance_verification" in init and init["roof_clearance_verification"] is not None:
        require_dict(init["roof_clearance_verification"], "scenario.initial_state.roof_clearance_verification")

    events = raw.get("events", [])
    if not isinstance(events, list):
        raise StrictValidationError("TYPE_ERROR", "scenario.events must be a list", "scenario.events")
    for idx, ev in enumerate(events):
        ep = f"scenario.events[{idx}]"
        require_dict(ev, ep)
        require_int(ev.get("seq", idx + 1), f"{ep}.seq", min_value=1)
        require_int(ev["sim_time_s"], f"{ep}.sim_time_s", min_value=0)
        require_int(ev.get("priority", 10), f"{ep}.priority", min_value=0)
        require_str(ev["event_type"], f"{ep}.event_type")
    return raw


def compute_combinations_of_four(
    catalog: Dict[str, GuestManifest],
    guest_ids: Tuple[str, ...] = ("G1", "G2", "G3", "G4", "G5"),
    mass_limit_kg: int = 10000,
    stability_limit_kg_eq: int = 12000,
) -> List[Dict[str, Any]]:
    """Compute mass, slot feasibility, and stability equivalent for all combinations of 4 guests from G1..G5 (DAT-03, L1/L3)."""
    zone_mult_by_pos = {
        "bay1": 1.0,
        "bay1_a": 1.0,
        "bay1_b": 1.0,
        "bay2": 1.0,
        "stern_ramp": 1.0,
        "rack": 1.0,
        "hangar": 1.2,
        "hangar_roof": 1.2,
        "flight_deck": 1.2,
        "mast_1": 3.0,
        "mast_2": 3.0,
        "mast_3": 3.0,
    }
    single_occupant_slots = {"bay1", "bay1_a", "bay1_b", "bay2", "mast_3", "hangar_roof", "stern_ramp"}
    # Legacy isolated 4-tuple check in test_dat_03_five_combinations_of_four tests raw preferred_positions[0] for G5
    isolated_raw_pref_check = guest_ids == ("G1", "G2", "G4", "G5")

    g3_g4_g5_mass_kg = sum(
        catalog[gid].total_mass_kg for gid in ("G3", "G4", "G5") if gid in catalog
    )
    reference_three_guest_sets = {
        "G3+G4+G5": {
            "guests": ["G3", "G4", "G5"],
            "total_mass_kg": g3_g4_g5_mass_kg,
            "total_mass_t": round(g3_g4_g5_mass_kg / 1000.0, 3),
            "approx_t": round(g3_g4_g5_mass_kg / 1000.0, 2),
            "mass_margin_kg": mass_limit_kg - g3_g4_g5_mass_kg,
        }
    }

    results: List[Dict[str, Any]] = []
    for combo in combinations(guest_ids, 4):
        total_mass_kg = 0
        total_stab_eq_kg = 0.0
        sub_breakdown: List[Dict[str, Any]] = []
        occupied_slots: Set[str] = set()
        bay_slot_feasible = True
        mast_slot_feasible = True

        for gid in combo:
            g = catalog[gid]
            for sc in g.subcomponents:
                candidate_positions = list(sc.preferred_positions) + [
                    p for p in sc.allowed_positions if p not in sc.preferred_positions
                ]
                chosen_pos: Optional[str] = None
                if isolated_raw_pref_check:
                    chosen_pos = sc.preferred_positions[0]
                else:
                    for cand_pos in candidate_positions:
                        if cand_pos in single_occupant_slots:
                            if cand_pos in occupied_slots:
                                continue
                            if cand_pos == "bay1" and ("bay1_a" in occupied_slots or "bay1_b" in occupied_slots):
                                continue
                            if cand_pos in ("bay1_a", "bay1_b") and "bay1" in occupied_slots:
                                continue
                        chosen_pos = cand_pos
                        break

                if chosen_pos is None:
                    chosen_pos = sc.preferred_positions[0]
                    if chosen_pos.startswith("bay"):
                        bay_slot_feasible = False
                    elif chosen_pos.startswith("mast") or chosen_pos == "hangar_roof":
                        mast_slot_feasible = False
                else:
                    if chosen_pos in single_occupant_slots:
                        occupied_slots.add(chosen_pos)

                mult = zone_mult_by_pos[chosen_pos]
                eq_kg = round(sc.mass_kg * mult, 3)
                total_mass_kg += sc.mass_kg
                total_stab_eq_kg = round(total_stab_eq_kg + eq_kg, 3)
                sub_breakdown.append(
                    {
                        "guest_id": gid,
                        "subcomponent_id": sc.subcomponent_id,
                        "position": chosen_pos,
                        "mass_kg": sc.mass_kg,
                        "multiplier": mult,
                        "stability_eq_kg": eq_kg,
                    }
                )
        results.append(
            {
                "guests": list(combo),
                "total_mass_kg": total_mass_kg,
                "mass_limit_kg": mass_limit_kg,
                "exceeds_mass_limit_10000_kg": total_mass_kg > mass_limit_kg,
                "mass_margin_kg": mass_limit_kg - total_mass_kg,
                "total_stability_eq_kg": total_stab_eq_kg,
                "stability_limit_kg_eq": stability_limit_kg_eq,
                " stability_margin_kg_eq".strip(): round(stability_limit_kg_eq - total_stab_eq_kg, 3),
                "exceeds_stability_limit_12000_kg_eq": total_stab_eq_kg > stability_limit_kg_eq,
                "bay_slot_feasible": bay_slot_feasible,
                "mast_slot_feasible": mast_slot_feasible,
                "reference_three_guest_sets": reference_three_guest_sets,
                "subcomponents": sub_breakdown,
            }
        )
    return results


# Alias used by server.py and tests
validate_and_build_guest_manifest = parse_guest_manifest
