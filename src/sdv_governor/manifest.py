"""Strict parser and validator for Guest Manifests, Priority Profiles, Ship Baseline, Mission Envelope, and Scenarios (B2, DAT-01..04)."""

from __future__ import annotations

import hashlib
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import yaml

from .models import (
    DependencySpec,
    GuestManifest,
    GuestModeSpec,
    InstallationConstraints,
    PriorityProfile,
    ShipStateConstraints,
    StrictValidationError,
    SubcomponentSpec,
    VALID_COOLING_ROUTES,
    VALID_POSITIONS,
    VALID_VALUE_ORIGINS,
    VehicleReplacementConstraints,
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
    "capability_retention_ratio",
    "value_origin",
    "field_origins",
}

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
        required_keys=TOP_LEVEL_GUEST_KEYS,
        allowed_keys=TOP_LEVEL_GUEST_KEYS,
    )

    schema_version = require_str(
        raw["schema_version"],
        "guest_manifest.schema_version",
        allowed_enum={"sdv-guest-manifest/1.0"},
    )
    guest_id = require_str(raw["guest_id"], "guest_manifest.guest_id")
    name = require_str(raw["name"], "guest_manifest.name")
    category = require_str(raw["category"], "guest_manifest.category")
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
        require_dict(sc_raw, p, required_keys=SUBCOMPONENT_KEYS - {"field_origins"}, allowed_keys=SUBCOMPONENT_KEYS)
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
            )
        )

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

    modes: Dict[str, GuestModeSpec] = {}
    for m_name, m_raw in raw_modes.items():
        mp = f"guest_manifest.modes.{m_name}"
        require_str(m_name, mp)
        require_dict(m_raw, mp, required_keys=MODE_KEYS - {"field_origins"}, allowed_keys=MODE_KEYS)

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
    cond_exc = tuple(require_dict(c, f"dependencies.conditional_excludes[{i}]") for i, c in enumerate(dep_raw["conditional_excludes"]))
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
    )


def load_guest_catalog(
    guests_dir: Path,
    errata_path: Optional[Path] = None,
) -> Dict[str, GuestManifest]:
    """Load and validate all guest YAML files in `guests_dir`, enforcing errata overrides if present."""
    errata_data: Optional[Dict[str, Any]] = None
    if errata_path is not None and errata_path.exists():
        errata_data, _ = load_yaml_file(errata_path)

    catalog: Dict[str, GuestManifest] = {}
    for ypath in sorted(guests_dir.glob("*.yaml")):
        raw, sha = load_yaml_file(ypath)
        manifest = parse_guest_manifest(raw, source_sha256=sha, errata_override=errata_data)
        if manifest.guest_id in catalog:
            raise StrictValidationError("DUPLICATE_GUEST_ID", f"Duplicate guest_id '{manifest.guest_id}'", str(ypath))
        catalog[manifest.guest_id] = manifest

    if not catalog:
        raise StrictValidationError("EMPTY_CATALOG", f"No guest manifests found in {guests_dir}", str(guests_dir))
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
    cap_weights = {
        require_str(k, "capability_weights.key"): require_finite_number(v, f"capability_weights.{k}", min_value=0.0, max_value=1000.0)
        for k, v in cap_w_raw.items()
    }

    rank_raw = require_dict(raw["guest_priority_rank"], "priority_profile.guest_priority_rank")
    if len(rank_raw) == 0:
        raise StrictValidationError("EMPTY_RANKS", "guest_priority_rank cannot be empty", "priority_profile.guest_priority_rank")
    guest_ranks = {
        require_str(k, "guest_priority_rank.key"): require_int(v, f"guest_priority_rank.{k}", min_value=1)
        for k, v in rank_raw.items()
    }

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
    shed_order = tuple(require_str(g, f"shed_order[{i}]") for i, g in enumerate(shed_list))

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
        source_sha256=source_sha256,
    )


def load_priority_profile(path: Path) -> PriorityProfile:
    raw, sha = load_yaml_file(path)
    return parse_priority_profile(raw, source_sha256=sha)


def validate_ship_profile(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Strictly validate ship baseline profile (DAT-04, H13)."""
    require_dict(
        raw,
        "ship_profile",
        required_keys={
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
        },
        allowed_keys={
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
        },
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
    """Strictly validate a scenario YAML definition (DAT-04, H03, H13)."""
    require_dict(raw, "scenario")
    require_str(raw.get("schema_version", "sdv-scenario/1.0"), "scenario.schema_version", allowed_enum={"sdv-scenario/1.0"})
    require_str(raw.get("scenario_id", ""), "scenario.scenario_id")
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
    """Compute mass and stability equivalent for all combinations of 4 guests from G1..G5 (DAT-03)."""
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

    results: List[Dict[str, Any]] = []
    for combo in combinations(guest_ids, 4):
        total_mass_kg = 0
        total_stab_eq_kg = 0.0
        sub_breakdown: List[Dict[str, Any]] = []
        for gid in combo:
            g = catalog[gid]
            for sc in g.subcomponents:
                pref_pos = sc.preferred_positions[0]
                mult = zone_mult_by_pos[pref_pos]
                eq_kg = round(sc.mass_kg * mult, 3)
                total_mass_kg += sc.mass_kg
                total_stab_eq_kg = round(total_stab_eq_kg + eq_kg, 3)
                sub_breakdown.append(
                    {
                        "guest_id": gid,
                        "subcomponent_id": sc.subcomponent_id,
                        "position": pref_pos,
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
                "exceeds_stability_limit_12000_kg_eq": total_stab_eq_kg > stability_limit_kg_eq,
                "subcomponents": sub_breakdown,
            }
        )
    return results


# Alias used by server.py and tests
validate_and_build_guest_manifest = parse_guest_manifest
