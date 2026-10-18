"""Environmental data loading, validation, interpolation, and replay feed generation.

Part of E1: Public real environmental data runtime engine (Golden Gate).
Uses ONLY Python standard library + PyYAML (INV-9).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import yaml

from .models import (
    StrictValidationError,
    VALID_PROVENANCE_LABELS,
    canonical_json_bytes,
    canonical_json_hash,
)
from .ship_state import FeedReading
from .budgets import compute_cooling_water_capacity_w


@dataclass(frozen=True)
class EnvSample:
    """A point-in-time environmental sample (§5.7)."""

    t_utc: str
    sim_time_s: int
    sst_c: Optional[float]
    wave_hs_m: Optional[float]
    sea_state_wmo: Optional[int]
    wind_kn: Optional[float]
    gust_kn: Optional[float]
    wind_dir_deg: Optional[float]
    provenance: str  # "DERIVED" | "MEASURED" | "UNAVAILABLE"
    source_id: str
    lead_s: Optional[int]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def wave_height_to_sea_state_wmo(hs_m: float) -> int:
    """Convert significant wave height Hs (meters) to WMO Code 3700 sea state category.

    Table WMO Code 3700 (ASM-ENV-SEASTATE-WMO3700, §5.6):
        0: 0.0 m (Calm - glassy)
        1: 0.0 < Hs <= 0.10 m (Calm - rippled)
        2: 0.10 < Hs <= 0.50 m (Smooth - wavelets)
        3: 0.50 < Hs <= 1.25 m (Slight)
        4: 1.25 < Hs <= 2.50 m (Moderate)
        5: 2.50 < Hs <= 4.00 m (Rough)
        6: 4.00 < Hs <= 6.00 m (Very rough)
        7: 6.00 < Hs <= 9.00 m (High)
        8: 9.00 < Hs <= 14.00 m (Very high)
        9: Hs > 14.00 m (Phenomenal)
    Upper bound is inclusive.
    """
    if hs_m is None:
        raise StrictValidationError("ENV_INVALID_PARAM", "hs_m cannot be None", "wave_hs_m")
    if math.isnan(hs_m) or math.isinf(hs_m):
        raise StrictValidationError("ENV_INVALID_PARAM", f"hs_m cannot be NaN/inf: {hs_m}", "wave_hs_m")
    if hs_m <= 0.0:
        return 0
    elif hs_m <= 0.10:
        return 1
    elif hs_m <= 0.50:
        return 2
    elif hs_m <= 1.25:
        return 3
    elif hs_m <= 2.50:
        return 4
    elif hs_m <= 4.00:
        return 5
    elif hs_m <= 6.00:
        return 6
    elif hs_m <= 9.00:
        return 7
    elif hs_m <= 14.00:
        return 8
    else:
        return 9


def mps_to_kn(mps: float) -> float:
    """Convert meters per second to knots per ASM-ENV-UNITS (kn = m/s * 1.943844)."""
    if mps is None:
        raise StrictValidationError("ENV_INVALID_PARAM", "mps cannot be None", "mps")
    if math.isnan(mps) or math.isinf(mps):
        raise StrictValidationError("ENV_INVALID_PARAM", f"mps cannot be NaN/inf: {mps}", "mps")
    return float(mps) * 1.943844


def parse_ndbc_table(text: str) -> List[Dict[str, Any]]:
    """Parse NOAA NDBC standard meteorological realtime text format into row dicts.

    Requirements (ENV-UNIT-03):
    - Converts 'MM' missing values to None.
    - Handles 2-line header (#YY ... and #yr ...).
    - Converts descending NDBC file order to ascending chronological order.
    - Accessible by column name.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    header_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("#"):
            header_idx = i
            break
    if header_idx == -1:
        raise StrictValidationError("NDBC_PARSE_ERROR", "No header line found in NDBC table")

    header_line = lines[header_idx].lstrip("#").strip()
    col_names = header_line.split()

    start_data_idx = header_idx + 1
    if start_data_idx < len(lines) and lines[start_data_idx].startswith("#"):
        start_data_idx += 1

    rows: List[Dict[str, Any]] = []
    for line in lines[start_data_idx:]:
        if line.startswith("#"):
            continue
        tokens = line.split()
        if len(tokens) < len(col_names):
            continue
        row_dict: Dict[str, Any] = {}
        for col, val in zip(col_names, tokens):
            if val == "MM":
                row_dict[col] = None
            else:
                try:
                    if col.upper() in ("#YY", "YY", "YYYY", "YR", "YEAR", "MM", "DD", "HH", "MN", "MIN"):
                        row_dict[col] = int(val)
                    else:
                        row_dict[col] = float(val)
                except ValueError:
                    row_dict[col] = val

        yr_key = next((k for k in ("YYYY", "YY", "#YY", "yr", "year") if k in row_dict and row_dict[k] is not None), None)
        mo_key = next((k for k in ("MM", "mo", "month") if k in row_dict and row_dict[k] is not None), None)
        dy_key = next((k for k in ("DD", "dy", "day") if k in row_dict and row_dict[k] is not None), None)
        hr_key = next((k for k in ("hh", "hr", "hour") if k in row_dict and row_dict[k] is not None), None)
        mn_key = next((k for k in ("mm", "mn", "minute", "min") if k in row_dict and row_dict[k] is not None), None)

        if yr_key and mo_key and dy_key and hr_key:
            yr = row_dict[yr_key]
            if yr < 100:
                yr += 2000
            mo = row_dict[mo_key]
            dy = row_dict[dy_key]
            hr = row_dict[hr_key]
            mn = row_dict.get(mn_key, 0) or 0
            row_dict["t_utc"] = f"{yr:04d}-{mo:02d}-{dy:02d}T{hr:02d}:{mn:02d}:00Z"

        rows.append(row_dict)

    if len(rows) >= 2 and "t_utc" in rows[0] and "t_utc" in rows[-1]:
        if rows[0]["t_utc"] > rows[-1]["t_utc"]:
            rows.reverse()

    return rows


def validate_env_series(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Fail-closed validator for normalized env_series data (§5.5, INV-6).

    Checks:
    - schema_version and site_id are non-empty strings.
    - anchor_utc is valid ISO-8601 UTC.
    - series contains series lists.
    - Timestamps are strictly monotonic in UTC without duplicates.
    - Physical bounds:
        sst_c in [-2.0, 40.0]
        wave_hs_m in [0.0, 20.0]
        wind_kn in [0.0, 120.0]
        gust_kn >= wind_kn (when both present)
        wind_dir_deg in [0.0, 360.0)
        sea_state_wmo in [0, 9]
    - No NaN or inf values.
    - provenance in VALID_PROVENANCE_LABELS.
    - MEASURED provenance requires physical station source_id.
    """
    if not isinstance(raw, dict):
        raise StrictValidationError("ENV_SNAPSHOT_INVALID", "Root must be a dict", "root")

    if not isinstance(raw.get("schema_version"), str):
        raise StrictValidationError("ENV_SNAPSHOT_INVALID", "schema_version must be a string", "schema_version")
    if not isinstance(raw.get("site_id"), str):
        raise StrictValidationError("ENV_SNAPSHOT_INVALID", "site_id must be a string", "site_id")

    anchor_utc = raw.get("anchor_utc")
    if not isinstance(anchor_utc, str):
        raise StrictValidationError("ENV_SNAPSHOT_INVALID", "anchor_utc must be a string", "anchor_utc")
    try:
        dt = datetime.fromisoformat(anchor_utc.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("anchor_utc must be UTC")
    except Exception as e:
        raise StrictValidationError("ENV_SNAPSHOT_INVALID", f"Invalid anchor_utc '{anchor_utc}': {e}", "anchor_utc")

    series_map = raw.get("series")
    if not isinstance(series_map, dict) or not series_map:
        raise StrictValidationError("ENV_SNAPSHOT_INVALID", "series must be a non-empty dict", "series")

    for s_name, rows in series_map.items():
        if not isinstance(rows, list):
            raise StrictValidationError("ENV_SNAPSHOT_INVALID", f"Series '{s_name}' must be a list", f"series.{s_name}")

        last_dt: Optional[datetime] = None
        last_t_str: Optional[str] = None

        for idx, row in enumerate(rows):
            path = f"series.{s_name}[{idx}]"
            if not isinstance(row, dict):
                raise StrictValidationError("ENV_SNAPSHOT_INVALID", "Row must be a dict", path)

            t_utc = row.get("t_utc")
            if not isinstance(t_utc, str):
                raise StrictValidationError("ENV_SNAPSHOT_INVALID", "t_utc must be string", f"{path}.t_utc")
            try:
                row_dt = datetime.fromisoformat(t_utc.replace("Z", "+00:00"))
            except Exception as e:
                raise StrictValidationError("ENV_SNAPSHOT_INVALID", f"Invalid t_utc '{t_utc}': {e}", f"{path}.t_utc")

            if last_dt is not None:
                if row_dt <= last_dt:
                    raise StrictValidationError(
                        "ENV_SNAPSHOT_INVALID",
                        f"Non-monotonic timestamp: {last_t_str} >= {t_utc}",
                        f"{path}.t_utc",
                    )
            last_dt = row_dt
            last_t_str = t_utc

            # Check numeric limits and NaN/inf
            for key, (lo, hi) in {
                "sst_c": (-2.0, 40.0),
                "wave_hs_m": (0.0, 20.0),
                "wind_kn": (0.0, 120.0),
                "gust_kn": (0.0, 150.0),
                "wind_dir_deg": (0.0, 360.0),
                "wave_tp_s": (0.0, 60.0),
                "wave_dir_deg": (0.0, 360.0),
            }.items():
                val = row.get(key)
                if val is not None:
                    if not isinstance(val, (int, float)):
                        raise StrictValidationError("ENV_SNAPSHOT_INVALID", f"Field '{key}' must be numeric", f"{path}.{key}")
                    if math.isnan(val) or math.isinf(val):
                        raise StrictValidationError("ENV_SNAPSHOT_INVALID", f"Field '{key}' is NaN or Inf", f"{path}.{key}")
                    if not (lo <= val <= hi):
                        raise StrictValidationError("ENV_SNAPSHOT_INVALID", f"Field '{key}' {val} outside [{lo}, {hi}]", f"{path}.{key}")

            w_kn = row.get("wind_kn")
            g_kn = row.get("gust_kn")
            if w_kn is not None and g_kn is not None:
                if g_kn < w_kn:
                    raise StrictValidationError(
                        "ENV_SNAPSHOT_INVALID",
                        f"gust_kn {g_kn} < wind_kn {w_kn}",
                        f"{path}.gust_kn",
                    )

            ss = row.get("sea_state_wmo")
            if ss is not None:
                if not isinstance(ss, int) or isinstance(ss, bool):
                    raise StrictValidationError("ENV_SNAPSHOT_INVALID", f"sea_state_wmo must be int: {ss}", f"{path}.sea_state_wmo")
                if not (0 <= ss <= 9):
                    raise StrictValidationError("ENV_SNAPSHOT_INVALID", f"sea_state_wmo {ss} outside [0, 9]", f"{path}.sea_state_wmo")

            prov = row.get("provenance")
            if prov is not None:
                if prov not in VALID_PROVENANCE_LABELS:
                    raise StrictValidationError("ENV_SNAPSHOT_INVALID", f"Invalid provenance '{prov}'", f"{path}.provenance")
                source_id = str(row.get("source_id", "")).strip()
                if prov == "MEASURED":
                    if not source_id or source_id.lower() in ("constant", "literal", "config", "synthetic", "simulated", "virtual") or "synthetic" in source_id.lower():
                        raise StrictValidationError(
                            "MEASURED_WITHOUT_PHYSICAL_SOURCE_ID",
                            f"MEASURED feed requires physical sensor source_id, not '{source_id}'",
                            f"{path}.source_id",
                        )

    return raw


def snapshot_sha256(snapshot_dir: Path) -> str:
    """Compute canonical SHA-256 hash of manifest and env_series in snapshot_dir (§5.7)."""
    manifest_file = snapshot_dir / "manifest.json"
    series_file = snapshot_dir / "env_series.json"
    if not manifest_file.exists():
        raise StrictValidationError("ENV_SNAPSHOT_MISSING", f"Missing manifest.json in {snapshot_dir}")
    if not series_file.exists():
        raise StrictValidationError("ENV_SNAPSHOT_MISSING", f"Missing env_series.json in {snapshot_dir}")

    manifest_raw = json.loads(manifest_file.read_text(encoding="utf-8"))
    series_raw = json.loads(series_file.read_text(encoding="utf-8"))

    payload = {
        "manifest": manifest_raw,
        "series": series_raw,
    }
    return canonical_json_hash(payload)


def load_active_snapshot(repo_root: Path, site_id: str = "golden_gate") -> Dict[str, Any]:
    """Load and validate the active environmental snapshot for site_id (fail-closed, §5.7)."""
    env_base = repo_root / "data" / "environment" / site_id
    if not (env_base / "ACTIVE").exists():
        env_base = repo_root / "src" / "sdv_governor" / "_bundled" / "data" / "environment" / site_id
    if not (env_base / "ACTIVE").exists():
        raise StrictValidationError("ENV_SNAPSHOT_MISSING", f"No ACTIVE file found for site '{site_id}' at {env_base}")

    active_name = (env_base / "ACTIVE").read_text(encoding="utf-8").strip()
    snapshot_dir = env_base / active_name
    if not snapshot_dir.is_dir():
        raise StrictValidationError("ENV_SNAPSHOT_MISSING", f"Active snapshot directory does not exist: {snapshot_dir}")

    manifest_path = snapshot_dir / "manifest.json"
    series_path = snapshot_dir / "env_series.json"
    if not manifest_path.exists() or not series_path.exists():
        raise StrictValidationError("ENV_SNAPSHOT_MISSING", f"Snapshot missing manifest or series in {snapshot_dir}")

    manifest_dict = json.loads(manifest_path.read_text(encoding="utf-8"))
    series_bytes = series_path.read_bytes()

    expected_series_sha = manifest_dict.get("normalized_sha256")
    raw_sha = hashlib.sha256(series_bytes).hexdigest()
    if expected_series_sha and raw_sha != expected_series_sha:
        try:
            series_dict = json.loads(series_bytes.decode("utf-8"))
            canonical_sha = canonical_json_hash(series_dict)
        except Exception:
            canonical_sha = ""
        if expected_series_sha not in (raw_sha, canonical_sha):
            raise StrictValidationError(
                "ENV_SNAPSHOT_INVALID",
                f"Hash mismatch: manifest normalized_sha256 '{expected_series_sha}' != actual '{raw_sha}'",
                "manifest.normalized_sha256",
            )

    try:
        series_dict = json.loads(series_bytes.decode("utf-8"))
    except Exception as e:
        raise StrictValidationError("ENV_SNAPSHOT_INVALID", f"Corrupt JSON in env_series.json: {e}", "env_series.json")

    validated_series = validate_env_series(series_dict)
    snap_sha = snapshot_sha256(snapshot_dir)

    return {
        "site_id": site_id,
        "active_snapshot_name": active_name,
        "snapshot_dir": snapshot_dir,
        "manifest": manifest_dict,
        "series": validated_series,
        "snapshot_sha256": snap_sha,
        "anchor_utc": validated_series.get("anchor_utc"),
    }


def _extract_series_rows(series: Union[List[Dict[str, Any]], Dict[str, Any]]) -> List[Dict[str, Any]]:
    if isinstance(series, list):
        return series
    if isinstance(series, dict):
        if "forecast_GG_BAR" in series:
            return series["forecast_GG_BAR"]
        if "series" in series:
            inner = series["series"]
            if isinstance(inner, list):
                return inner
            if isinstance(inner, dict):
                for k in ("forecast_GG_BAR", "forecast", "primary"):
                    if k in inner:
                        return inner[k]
                for v in inner.values():
                    if isinstance(v, list):
                        return v
        for k in ("forecast_GG_BAR", "forecast"):
            if k in series:
                return series[k]
        for v in series.values():
            if isinstance(v, list):
                return v
    return []


def _interp_val(v0: Optional[float], v1: Optional[float], frac: float) -> Optional[float]:
    if v0 is None and v1 is None:
        return None
    if v0 is None:
        return v1
    if v1 is None:
        return v0
    return v0 + frac * (v1 - v0)


def _interp_deg(d0: Optional[float], d1: Optional[float], frac: float) -> Optional[float]:
    if d0 is None and d1 is None:
        return None
    if d0 is None:
        return d1
    if d1 is None:
        return d0
    diff = (d1 - d0 + 180.0) % 360.0 - 180.0
    return (d0 + frac * diff) % 360.0


def sample_at(
    series: Union[List[Dict[str, Any]], Dict[str, Any]],
    sim_time_s: int,
    anchor_utc: str,
) -> EnvSample:
    """Sample environmental state at sim_time_s relative to anchor_utc (causal interpolation, §5.7).

    Rules:
    - Lead > 48 h (172800 s) or < 0 -> provenance UNAVAILABLE.
    - Strictly causal: observations with t > query are never used.
    - Hourly forecast interpolation is linear for SST, wind, and Hs.
    - Sea state category is strictly recalculated from interpolated Hs via wave_height_to_sea_state_wmo.
    """
    anchor_dt = datetime.fromisoformat(anchor_utc.replace("Z", "+00:00"))
    query_dt = anchor_dt + timedelta(seconds=sim_time_s)
    query_iso = query_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    if sim_time_s < 0 or sim_time_s > 172800:
        return EnvSample(
            t_utc=query_iso,
            sim_time_s=sim_time_s,
            sst_c=None,
            wave_hs_m=None,
            sea_state_wmo=None,
            wind_kn=None,
            gust_kn=None,
            wind_dir_deg=None,
            provenance="UNAVAILABLE",
            source_id="openmeteo:unavailable",
            lead_s=sim_time_s,
        )

    rows = _extract_series_rows(series)
    if not rows:
        return EnvSample(
            t_utc=query_iso,
            sim_time_s=sim_time_s,
            sst_c=None,
            wave_hs_m=None,
            sea_state_wmo=None,
            wind_kn=None,
            gust_kn=None,
            wind_dir_deg=None,
            provenance="UNAVAILABLE",
            source_id="openmeteo:unavailable",
            lead_s=sim_time_s,
        )

    # Check if purely observation series (MEASURED)
    is_measured = all(r.get("provenance") == "MEASURED" for r in rows)
    if is_measured:
        # Strictly causal: filter out any observation in the future of query_dt
        valid_obs = [r for r in rows if datetime.fromisoformat(r["t_utc"].replace("Z", "+00:00")) <= query_dt]
        if not valid_obs:
            return EnvSample(
                t_utc=query_iso,
                sim_time_s=sim_time_s,
                sst_c=None,
                wave_hs_m=None,
                sea_state_wmo=None,
                wind_kn=None,
                gust_kn=None,
                wind_dir_deg=None,
                provenance="UNAVAILABLE",
                source_id="ndbc:unavailable",
                lead_s=sim_time_s,
            )
        latest = valid_obs[-1]
        raw_hs = latest.get("wave_hs_m")
        ss = wave_height_to_sea_state_wmo(raw_hs) if raw_hs is not None else latest.get("sea_state_wmo")
        return EnvSample(
            t_utc=latest.get("t_utc", query_iso),
            sim_time_s=sim_time_s,
            sst_c=latest.get("sst_c"),
            wave_hs_m=raw_hs,
            sea_state_wmo=ss,
            wind_kn=latest.get("wind_kn"),
            gust_kn=latest.get("gust_kn"),
            wind_dir_deg=latest.get("wind_dir_deg"),
            provenance="MEASURED",
            source_id=latest.get("source_id", "NDBC"),
            lead_s=sim_time_s,
        )

    # Forecast series: linear interpolation between bounding hourly rows
    row_times = [
        (datetime.fromisoformat(r["t_utc"].replace("Z", "+00:00")), r)
        for r in rows
        if "t_utc" in r
    ]
    if not row_times:
        return EnvSample(
            t_utc=query_iso,
            sim_time_s=sim_time_s,
            sst_c=None,
            wave_hs_m=None,
            sea_state_wmo=None,
            wind_kn=None,
            gust_kn=None,
            wind_dir_deg=None,
            provenance="UNAVAILABLE",
            source_id="openmeteo:unavailable",
            lead_s=sim_time_s,
        )

    # Exact match check
    for r_dt, r in row_times:
        if r_dt == query_dt:
            raw_hs = r.get("wave_hs_m")
            ss = wave_height_to_sea_state_wmo(raw_hs) if raw_hs is not None else r.get("sea_state_wmo")
            return EnvSample(
                t_utc=query_iso,
                sim_time_s=sim_time_s,
                sst_c=r.get("sst_c"),
                wave_hs_m=raw_hs,
                sea_state_wmo=ss,
                wind_kn=r.get("wind_kn"),
                gust_kn=r.get("gust_kn"),
                wind_dir_deg=r.get("wind_dir_deg"),
                provenance=r.get("provenance", "DERIVED"),
                source_id=r.get("source_id", "openmeteo:marine+forecast:GG_BAR"),
                lead_s=r.get("lead_s", sim_time_s),
            )

    before = [(dt, r) for dt, r in row_times if dt <= query_dt]
    after = [(dt, r) for dt, r in row_times if dt >= query_dt]

    if not before and not after:
        prov = "UNAVAILABLE"
        return EnvSample(query_iso, sim_time_s, None, None, None, None, None, None, prov, "openmeteo:unavailable", sim_time_s)

    if not before:
        # Query is before the first row
        first_dt, first_r = after[0]
        if (first_dt - query_dt).total_seconds() <= 3600:
            raw_hs = first_r.get("wave_hs_m")
            ss = wave_height_to_sea_state_wmo(raw_hs) if raw_hs is not None else first_r.get("sea_state_wmo")
            return EnvSample(
                t_utc=query_iso,
                sim_time_s=sim_time_s,
                sst_c=first_r.get("sst_c"),
                wave_hs_m=raw_hs,
                sea_state_wmo=ss,
                wind_kn=first_r.get("wind_kn"),
                gust_kn=first_r.get("gust_kn"),
                wind_dir_deg=first_r.get("wind_dir_deg"),
                provenance=first_r.get("provenance", "DERIVED"),
                source_id=first_r.get("source_id", "openmeteo:marine+forecast:GG_BAR"),
                lead_s=first_r.get("lead_s", sim_time_s),
            )
        return EnvSample(query_iso, sim_time_s, None, None, None, None, None, None, "UNAVAILABLE", "openmeteo:unavailable", sim_time_s)

    if not after:
        # Query is after the last row
        last_dt, last_r = before[-1]
        if (query_dt - last_dt).total_seconds() <= 3600:
            raw_hs = last_r.get("wave_hs_m")
            ss = wave_height_to_sea_state_wmo(raw_hs) if raw_hs is not None else last_r.get("sea_state_wmo")
            return EnvSample(
                t_utc=query_iso,
                sim_time_s=sim_time_s,
                sst_c=last_r.get("sst_c"),
                wave_hs_m=raw_hs,
                sea_state_wmo=ss,
                wind_kn=last_r.get("wind_kn"),
                gust_kn=last_r.get("gust_kn"),
                wind_dir_deg=last_r.get("wind_dir_deg"),
                provenance=last_r.get("provenance", "DERIVED"),
                source_id=last_r.get("source_id", "openmeteo:marine+forecast:GG_BAR"),
                lead_s=last_r.get("lead_s", sim_time_s),
            )
        return EnvSample(query_iso, sim_time_s, None, None, None, None, None, None, "UNAVAILABLE", "openmeteo:unavailable", sim_time_s)

    b_dt, b_r = before[-1]
    a_dt, a_r = after[0]

    span_s = (a_dt - b_dt).total_seconds()
    frac = (query_dt - b_dt).total_seconds() / span_s if span_s > 0 else 0.0

    sst_interp = _interp_val(b_r.get("sst_c"), a_r.get("sst_c"), frac)
    hs_interp = _interp_val(b_r.get("wave_hs_m"), a_r.get("wave_hs_m"), frac)
    wind_interp = _interp_val(b_r.get("wind_kn"), a_r.get("wind_kn"), frac)
    gust_interp = _interp_val(b_r.get("gust_kn"), a_r.get("gust_kn"), frac)
    dir_interp = _interp_deg(b_r.get("wind_dir_deg"), a_r.get("wind_dir_deg"), frac)

    # Crucial requirement: sea_state_wmo is recalculated from interpolated Hs, NEVER interpolated directly!
    ss_recalc = wave_height_to_sea_state_wmo(hs_interp) if hs_interp is not None else None

    lead_val = b_r.get("lead_s")
    if lead_val is not None and a_r.get("lead_s") is not None:
        lead_interp = int(round(lead_val + frac * (a_r["lead_s"] - lead_val)))
    else:
        lead_interp = sim_time_s

    return EnvSample(
        t_utc=query_iso,
        sim_time_s=sim_time_s,
        sst_c=round(sst_interp, 2) if sst_interp is not None else None,
        wave_hs_m=round(hs_interp, 2) if hs_interp is not None else None,
        sea_state_wmo=ss_recalc,
        wind_kn=round(wind_interp, 1) if wind_interp is not None else None,
        gust_kn=round(gust_interp, 1) if gust_interp is not None else None,
        wind_dir_deg=round(dir_interp, 1) if dir_interp is not None else None,
        provenance=b_r.get("provenance", "DERIVED"),
        source_id=b_r.get("source_id", "openmeteo:marine+forecast:GG_BAR"),
        lead_s=lead_interp,
    )


def build_env_feed_readings(sample: EnvSample, sim_time_s: int) -> Dict[str, FeedReading]:
    """Build FeedReading dictionary for vessel state from EnvSample (§5.7).

    Returns:
        {"seawater_temperature_sensor": FeedReading(..., provenance="DERIVED",
            source_id="openmeteo:marine:GG_BAR", ttl_ms=5_400_000, fallback_value=30.0,
            fallback_reason="A.10 conservative 30C")}
    """
    try:
        dt = datetime.fromisoformat(sample.t_utc.replace("Z", "+00:00"))
        source_time_ns = int(dt.timestamp() * 1_000_000_000)
    except Exception:
        source_time_ns = max(1, (sim_time_s + 1) * 1_000_000_000)

    prov = sample.provenance if sample.provenance in VALID_PROVENANCE_LABELS else "DERIVED"
    source_id = sample.source_id if sample.source_id else "openmeteo:marine:GG_BAR"
    if not source_id.startswith("openmeteo:") and prov == "DERIVED":
        source_id = f"openmeteo:marine:{source_id}"

    feed = FeedReading(
        feed_id="seawater_temperature_sensor",
        value=sample.sst_c,
        unit="degC",
        provenance=prov,
        source_id=source_id,
        source_time_ns=source_time_ns,
        ttl_ms=5_400_000,  # 90 minutes per ASM-ENV-TTL
        age_ms=0,
        fallback_value=30.0,
        fallback_reason="A.10 conservative 30C",
    )
    return {"seawater_temperature_sensor": feed}


def cooling_capacity_outlook(
    series: Any,
    *,
    loop_degraded: bool = False,
    horizon_s: int = 172800,
) -> List[Dict[str, Any]]:
    """Compute cooling water capacity outlook over forecast series using compute_cooling_water_capacity_w.

    Returns:
        [{"t_utc", "sim_time_s", "sst_c", "cooling_nominal_w", "cooling_effective_w", "policy_note"}]
    """
    rows = _extract_series_rows(series)
    if not rows and isinstance(series, list):
        rows = series

    outlook: List[Dict[str, Any]] = []
    for row in rows:
        sim_time_s = row.get("sim_time_s", row.get("lead_s", 0))
        if sim_time_s is not None and sim_time_s > horizon_s:
            continue
        sst_c = row.get("sst_c")
        if sst_c is not None:
            nom_w, eff_w, note = compute_cooling_water_capacity_w(float(sst_c), loop_degraded=loop_degraded)
        else:
            nom_w, eff_w, note = compute_cooling_water_capacity_w(30.0, loop_degraded=loop_degraded)

        outlook.append({
            "t_utc": row.get("t_utc", ""),
            "sim_time_s": sim_time_s if sim_time_s is not None else 0,
            "sst_c": sst_c,
            "cooling_nominal_w": nom_w,
            "cooling_effective_w": eff_w,
            "policy_note": note,
        })
    return outlook


def env_events_for_scenario(
    series: Any,
    anchor_utc: str,
    horizon_s: int,
    cadence_s: int = 3600,
) -> List[Dict[str, Any]]:
    """Generate ENVIRONMENT_FEED_UPDATE replay events for a given scenario horizon (§5.8.1)."""
    events: List[Dict[str, Any]] = []
    for sim_s in range(0, horizon_s + 1, cadence_s):
        sample = sample_at(series, sim_s, anchor_utc)
        feeds = build_env_feed_readings(sample, sim_s)
        sw_feed = feeds["seawater_temperature_sensor"]
        feed_dict = {
            "feed_id": sw_feed.feed_id,
            "value": sw_feed.value,
            "unit": sw_feed.unit,
            "provenance": sw_feed.provenance,
            "source_id": sw_feed.source_id,
            "source_time_ns": sw_feed.source_time_ns,
            "ttl_ms": sw_feed.ttl_ms,
            "age_ms": sw_feed.age_ms,
            "fallback_value": sw_feed.fallback_value,
            "fallback_reason": sw_feed.fallback_reason,
        }
        ev = {
            "event_type": "ENVIRONMENT_FEED_UPDATE",
            "time_s": sim_s,
            "sim_time_s": sim_s,
            "t_utc": sample.t_utc,
            "sst_c": sample.sst_c,
            "wind_kn": sample.wind_kn,
            "sea_state_wmo": sample.sea_state_wmo,
            "wave_hs_m": sample.wave_hs_m,
            "seawater_feed": feed_dict,
        }
        events.append(ev)
    return events
