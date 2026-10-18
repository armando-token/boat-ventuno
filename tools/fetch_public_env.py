#!/usr/bin/env python3
"""Fetch, normalize, and freeze public environmental data for Golden Gate (E1).

Sources:
- Open-Meteo Marine Weather API (no-key, CC BY 4.0)
- Open-Meteo Forecast API (no-key, CC BY 4.0)
- NOAA National Data Buoy Center (realtime2 text, Public Domain)

Enforces fail-closed validation, canonical hashing, and offline reproducible snapshots.
ONLY standard library is used for execution.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


USER_AGENT = "sdv-resource-governor/1.1 (hackathon; offline snapshot)"
MPS_TO_KN = 1.943844


def wave_height_to_sea_state_wmo(hs_m: Optional[float]) -> Optional[int]:
    """WMO Code Table 3700 wave height to sea state mapping (inclusive upper bound)."""
    if hs_m is None:
        return None
    if hs_m <= 0.0:
        return 0
    if hs_m <= 0.1:
        return 1
    if hs_m <= 0.5:
        return 2
    if hs_m <= 1.25:
        return 3
    if hs_m <= 2.5:
        return 4
    if hs_m <= 4.0:
        return 5
    if hs_m <= 6.0:
        return 6
    if hs_m <= 9.0:
        return 7
    if hs_m <= 14.0:
        return 8
    return 9


def fetch_url(url: str, max_retries: int = 2, timeout: int = 20) -> bytes:
    """Fetch URL with retries, exponential backoff, and custom User-Agent."""
    headers = {"User-Agent": USER_AGENT}
    backoffs = [2, 4]
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:
            if attempt < max_retries:
                time.sleep(backoffs[attempt])
            else:
                raise RuntimeError(f"Failed to fetch {url} after {max_retries} retries: {e}") from e
    raise RuntimeError(f"Unexpected termination fetching {url}")


def parse_ndbc_table(
    raw_text: str,
    station_id: str,
    min_time_utc: datetime.datetime,
    max_time_utc: datetime.datetime,
) -> Tuple[List[Dict[str, Any]], int]:
    """Parse NOAA NDBC realtime2 fixed-width text data into normalized samples.
    
    Returns (sorted_samples, dropped_rows_count).
    """
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if len(lines) < 3:
        return [], 0

    header_cols = lines[0].lstrip("#").split()
    col_idx = {name: i for i, name in enumerate(header_cols)}

    samples_by_time: Dict[str, Dict[str, Any]] = {}
    dropped_count = 0

    for line in lines[2:]:
        tokens = line.split()
        if len(tokens) != len(header_cols):
            dropped_count += 1
            continue

        try:
            yr = int(tokens[col_idx.get("YY", col_idx.get("#YY", 0))])
            mo = int(tokens[col_idx["MM"]])
            dy = int(tokens[col_idx["DD"]])
            hr = int(tokens[col_idx["hh"]])
            mn = int(tokens[col_idx["mm"]])
            dt = datetime.datetime(yr, mo, dy, hr, mn, 0, tzinfo=datetime.timezone.utc)
        except Exception:
            dropped_count += 1
            continue

        if dt < min_time_utc or dt > max_time_utc:
            continue

        t_utc = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        def _get_val(col_name: str) -> Optional[str]:
            idx = col_idx.get(col_name)
            if idx is None or idx >= len(tokens):
                return None
            val = tokens[idx]
            return None if val == "MM" else val

        sst_c: Optional[float] = None
        wtmp = _get_val("WTMP")
        if wtmp is not None:
            try:
                sst_c = round(float(wtmp), 2)
            except ValueError:
                sst_c = None

        wave_hs_m: Optional[float] = None
        wvht = _get_val("WVHT")
        if wvht is not None:
            try:
                wave_hs_m = round(float(wvht), 2)
            except ValueError:
                wave_hs_m = None

        wave_tp_s: Optional[float] = None
        dpd = _get_val("DPD")
        if dpd is not None:
            try:
                wave_tp_s = round(float(dpd), 1)
            except ValueError:
                wave_tp_s = None

        wave_dir_deg: Optional[float] = None
        mwd = _get_val("MWD")
        if mwd is not None:
            try:
                wave_dir_deg = round(float(mwd), 1)
            except ValueError:
                wave_dir_deg = None

        wind_kn: Optional[float] = None
        wspd = _get_val("WSPD")
        if wspd is not None:
            try:
                wind_kn = round(float(wspd) * MPS_TO_KN, 2)
            except ValueError:
                wind_kn = None

        gust_kn: Optional[float] = None
        gst = _get_val("GST")
        if gst is not None:
            try:
                gust_kn = round(float(gst) * MPS_TO_KN, 2)
            except ValueError:
                gust_kn = None

        wind_dir_deg: Optional[float] = None
        wdir = _get_val("WDIR")
        if wdir is not None:
            try:
                wind_dir_deg = round(float(wdir), 1)
            except ValueError:
                wind_dir_deg = None

        sea_state_wmo = wave_height_to_sea_state_wmo(wave_hs_m)

        sample: Dict[str, Any] = {
            "t_utc": t_utc,
            "sst_c": sst_c,
            "wave_hs_m": wave_hs_m,
            "wave_tp_s": wave_tp_s,
            "wave_dir_deg": wave_dir_deg,
            "wind_kn": wind_kn,
            "gust_kn": gust_kn,
            "wind_dir_deg": wind_dir_deg,
            "sea_state_wmo": sea_state_wmo,
            "provenance": "MEASURED",
            "source_id": f"NDBC-{station_id}",
        }

        # Keep latest reading for any duplicate timestamp
        samples_by_time[t_utc] = sample

    # Sort strictly ascending by timestamp
    sorted_samples = [samples_by_time[k] for k in sorted(samples_by_time.keys())]
    return sorted_samples, dropped_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch and freeze public environmental data snapshot.")
    parser.add_argument("--site", default="golden_gate", help="Site identifier (default: golden_gate)")
    parser.add_argument("--point-primary", default="37.788,-122.634", help="Primary lat,lon (default: 37.788,-122.634)")
    parser.add_argument("--point-secondary", default="37.8199,-122.4783", help="Secondary lat,lon (default: 37.8199,-122.4783)")
    parser.add_argument("--ndbc", default="46237,46026,FTPC1", help="Comma-separated NDBC station IDs")
    parser.add_argument("--past-days", type=int, default=7, help="Past days for backtest (default: 7)")
    parser.add_argument("--forecast-days", type=int, default=3, help="Forecast days (default: 3)")
    parser.add_argument("--out", default="data/environment/golden_gate", help="Output directory")
    parser.add_argument("--force", action="store_true", help="Overwrite snapshot directory if it exists")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    fetched_utc_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Anti-cherrypicking rule (§5.4): anchor is full UTC hour preceding fetched_utc
    anchor_dt = now_utc.replace(minute=0, second=0, microsecond=0)
    anchor_utc_str = anchor_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Issue time is most recent 6h model cycle (00Z, 06Z, 12Z, 18Z) at or before anchor
    issue_hour = (anchor_dt.hour // 6) * 6
    issue_dt = anchor_dt.replace(hour=issue_hour, minute=0, second=0, microsecond=0)
    issue_utc_str = issue_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    snap_id = anchor_dt.strftime("%Y%m%dT%H%MZ")
    out_dir = Path(args.out).resolve()
    snap_dir = out_dir / snap_id
    raw_dir = snap_dir / "raw"

    if snap_dir.exists() and not args.force:
        print(f"Snapshot directory {snap_dir} already exists. Use --force to overwrite.")
        # If already exists and valid, we can still ensure ACTIVE is set or exit cleanly
        return 0

    raw_dir.mkdir(parents=True, exist_ok=True)

    lat_prim, lon_prim = [float(x.strip()) for x in args.point_primary.split(",")]
    lat_sec, lon_sec = [float(x.strip()) for x in args.point_secondary.split(",")] if args.point_secondary else (None, None)
    stations = [s.strip() for s in args.ndbc.split(",") if s.strip()]

    # 1. Fetch URLs
    marine_primary_url = (
        f"https://marine-api.open-meteo.com/v1/marine?"
        f"latitude={lat_prim}&longitude={lon_prim}&"
        f"hourly=sea_surface_temperature,wave_height,wave_period,wave_direction,wind_wave_height,swell_wave_height&"
        f"current=sea_surface_temperature,wave_height&"
        f"past_days={args.past_days}&forecast_days={args.forecast_days}&timezone=UTC&cell_selection=sea"
    )
    forecast_primary_url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat_prim}&longitude={lon_prim}&"
        f"hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m,visibility&"
        f"current=wind_speed_10m,wind_gusts_10m,wind_direction_10m&"
        f"wind_speed_unit=kn&past_days={args.past_days}&forecast_days={args.forecast_days}&timezone=UTC"
    )

    marine_sec_url: Optional[str] = None
    if lat_sec is not None and lon_sec is not None:
        marine_sec_url = (
            f"https://marine-api.open-meteo.com/v1/marine?"
            f"latitude={lat_sec}&longitude={lon_sec}&"
            f"hourly=sea_surface_temperature,wave_height,wave_period,wave_direction,wind_wave_height,swell_wave_height&"
            f"current=sea_surface_temperature,wave_height&"
            f"past_days={args.past_days}&forecast_days={args.forecast_days}&timezone=UTC&cell_selection=sea"
        )

    sources_metadata: List[Dict[str, Any]] = []

    # Fetch and save raw primary marine
    print(f"Fetching Open-Meteo Marine (GG_BAR) from {marine_primary_url} ...")
    raw_marine_prim = fetch_url(marine_primary_url)
    marine_prim_path = raw_dir / "openmeteo_marine_GG_BAR.json"
    marine_prim_path.write_bytes(raw_marine_prim)
    marine_prim_sha = hashlib.sha256(raw_marine_prim).hexdigest()
    data_marine_prim = json.loads(raw_marine_prim.decode("utf-8"))
    sources_metadata.append({
        "id": "openmeteo_marine_GG_BAR",
        "url": marine_primary_url,
        "sha256": marine_prim_sha,
        "bytes": len(raw_marine_prim),
        "license": "CC BY 4.0",
        "attribution": "Weather data by Open-Meteo.com",
        "provenance": "DERIVED",
        "generationtime_ms": data_marine_prim.get("generationtime_ms"),
    })

    # Fetch and save raw primary forecast
    print(f"Fetching Open-Meteo Forecast (GG_BAR) from {forecast_primary_url} ...")
    raw_forecast_prim = fetch_url(forecast_primary_url)
    forecast_prim_path = raw_dir / "openmeteo_forecast_GG_BAR.json"
    forecast_prim_path.write_bytes(raw_forecast_prim)
    forecast_prim_sha = hashlib.sha256(raw_forecast_prim).hexdigest()
    data_forecast_prim = json.loads(raw_forecast_prim.decode("utf-8"))
    sources_metadata.append({
        "id": "openmeteo_forecast_GG_BAR",
        "url": forecast_primary_url,
        "sha256": forecast_prim_sha,
        "bytes": len(raw_forecast_prim),
        "license": "CC BY 4.0",
        "attribution": "Weather data by Open-Meteo.com",
        "provenance": "DERIVED",
        "generationtime_ms": data_forecast_prim.get("generationtime_ms"),
    })

    # Fetch and save raw secondary marine if requested
    data_marine_sec: Optional[Dict[str, Any]] = None
    if marine_sec_url:
        print(f"Fetching Open-Meteo Marine (GG_BRIDGE) from {marine_sec_url} ...")
        raw_marine_sec = fetch_url(marine_sec_url)
        marine_sec_path = raw_dir / "openmeteo_marine_GG_BRIDGE.json"
        marine_sec_path.write_bytes(raw_marine_sec)
        marine_sec_sha = hashlib.sha256(raw_marine_sec).hexdigest()
        data_marine_sec = json.loads(raw_marine_sec.decode("utf-8"))
        sources_metadata.append({
            "id": "openmeteo_marine_GG_BRIDGE",
            "url": marine_sec_url,
            "sha256": marine_sec_sha,
            "bytes": len(raw_marine_sec),
            "license": "CC BY 4.0",
            "attribution": "Weather data by Open-Meteo.com",
            "provenance": "DERIVED",
            "generationtime_ms": data_marine_sec.get("generationtime_ms"),
        })

    # Fetch and save raw NDBC stations
    raw_ndbc_texts: Dict[str, str] = {}
    for st in stations:
        st_url = f"https://www.ndbc.noaa.gov/data/realtime2/{st}.txt"
        print(f"Fetching NDBC station {st} from {st_url} ...")
        raw_st_bytes = fetch_url(st_url)
        st_path = raw_dir / f"ndbc_{st}.txt"
        st_path.write_bytes(raw_st_bytes)
        st_sha = hashlib.sha256(raw_st_bytes).hexdigest()
        raw_ndbc_texts[st] = raw_st_bytes.decode("utf-8", errors="replace")
        sources_metadata.append({
            "id": f"ndbc_{st}",
            "url": st_url,
            "sha256": st_sha,
            "bytes": len(raw_st_bytes),
            "license": "Public Domain (US Government work)",
            "attribution": f"NOAA National Data Buoy Center (Station {st})",
            "provenance": "MEASURED",
            "generationtime_ms": None,
        })

    # 2. Normalize Open-Meteo GG_BAR series
    m_hourly = data_marine_prim.get("hourly", {})
    f_hourly = data_forecast_prim.get("hourly", {})

    m_times = m_hourly.get("time", [])
    m_sst = m_hourly.get("sea_surface_temperature", [])
    m_hs = m_hourly.get("wave_height", [])
    m_tp = m_hourly.get("wave_period", [])
    m_dir = m_hourly.get("wave_direction", [])

    f_times = f_hourly.get("time", [])
    f_wspd = f_hourly.get("wind_speed_10m", [])
    f_gust = f_hourly.get("wind_gusts_10m", [])
    f_wdir = f_hourly.get("wind_direction_10m", [])

    f_by_time: Dict[str, Dict[str, Any]] = {}
    for i, t in enumerate(f_times):
        f_by_time[t] = {
            "wind_kn": round(float(f_wspd[i]), 2) if i < len(f_wspd) and f_wspd[i] is not None else None,
            "gust_kn": round(float(f_gust[i]), 2) if i < len(f_gust) and f_gust[i] is not None else None,
            "wind_dir_deg": round(float(f_wdir[i]), 1) if i < len(f_wdir) and f_wdir[i] is not None else None,
        }

    forecast_gg_bar_series: List[Dict[str, Any]] = []
    for i, t in enumerate(m_times):
        t_utc = f"{t}:00Z" if not t.endswith("Z") else t
        t_dt = datetime.datetime.fromisoformat(t_utc.replace("Z", "+00:00"))

        lead_s: Optional[int] = None
        if t_dt >= issue_dt:
            lead_s = int((t_dt - issue_dt).total_seconds())

        hs_val = round(float(m_hs[i]), 2) if i < len(m_hs) and m_hs[i] is not None else None
        sst_val = round(float(m_sst[i]), 2) if i < len(m_sst) and m_sst[i] is not None else None
        tp_val = round(float(m_tp[i]), 1) if i < len(m_tp) and m_tp[i] is not None else None
        dir_val = round(float(m_dir[i]), 1) if i < len(m_dir) and m_dir[i] is not None else None

        f_data = f_by_time.get(t, {})
        w_kn = f_data.get("wind_kn")
        g_kn = f_data.get("gust_kn")
        w_dir = f_data.get("wind_dir_deg")

        # Clamp gust >= wind if both present
        if w_kn is not None and g_kn is not None and g_kn < w_kn:
            g_kn = w_kn

        sea_state_wmo = wave_height_to_sea_state_wmo(hs_val)

        forecast_gg_bar_series.append({
            "t_utc": t_utc,
            "lead_s": lead_s,
            "sst_c": sst_val,
            "wave_hs_m": hs_val,
            "wave_tp_s": tp_val,
            "wave_dir_deg": dir_val,
            "wind_kn": w_kn,
            "gust_kn": g_kn,
            "wind_dir_deg": w_dir,
            "sea_state_wmo": sea_state_wmo,
            "provenance": "DERIVED",
            "source_id": "openmeteo:marine+forecast:GG_BAR",
        })

    # Sort strictly ascending by timestamp
    forecast_gg_bar_series.sort(key=lambda s: s["t_utc"])

    # 3. Normalize NDBC series
    min_time_utc = anchor_dt - datetime.timedelta(days=args.past_days)
    max_time_utc = anchor_dt + datetime.timedelta(days=args.forecast_days)

    total_dropped_rows = 0
    all_series: Dict[str, List[Dict[str, Any]]] = {
        "forecast_GG_BAR": forecast_gg_bar_series
    }

    for st in stations:
        raw_text = raw_ndbc_texts.get(st, "")
        parsed_rows, dropped = parse_ndbc_table(raw_text, st, min_time_utc, max_time_utc)
        total_dropped_rows += dropped
        series_name = f"observed_NDBC_{st}"
        all_series[series_name] = parsed_rows

    # 4. Compute Quality Summary
    missing_counts: Dict[str, int] = {}
    for s_name, rows in all_series.items():
        for field in ("sst_c", "wave_hs_m", "wind_kn", "gust_kn"):
            key = f"{s_name}.{field}"
            missing_counts[key] = sum(1 for r in rows if r.get(field) is None)

    quality = {
        "missing_counts": missing_counts,
        "dropped_rows": total_dropped_rows,
        "notes": [
            f"Captured at fetched_utc={fetched_utc_str}, anchor_utc={anchor_utc_str}, issue_utc={issue_utc_str}.",
            f"Coverage: {args.past_days} past days and {args.forecast_days} forecast days.",
            "WMO 3700 scale applied for sea state from wave height."
        ],
    }

    env_series_dict: Dict[str, Any] = {
        "schema_version": "sdv-env-series/1.0",
        "site_id": args.site,
        "anchor_utc": anchor_utc_str,
        "issue_utc": issue_utc_str,
        "resolution_s": 3600,
        "max_valid_lead_s": 172800,
        "series": all_series,
        "quality": quality,
    }

    env_series_json = json.dumps(env_series_dict, indent=2, sort_keys=True) + "\n"
    env_series_path = snap_dir / "env_series.json"
    env_series_path.write_text(env_series_json, encoding="utf-8")
    normalized_sha256 = hashlib.sha256(env_series_json.encode("utf-8")).hexdigest()

    # 5. Build Points Metadata
    points: Dict[str, Any] = {
        "GG_BAR": {
            "req": [lat_prim, lon_prim],
            "grid": [data_marine_prim.get("latitude"), data_marine_prim.get("longitude")],
        }
    }
    if data_marine_sec and lat_sec is not None and lon_sec is not None:
        points["GG_BRIDGE"] = {
            "req": [lat_sec, lon_sec],
            "grid": [data_marine_sec.get("latitude"), data_marine_sec.get("longitude")],
        }

    manifest_dict: Dict[str, Any] = {
        "schema_version": "sdv-env-snapshot/1.0",
        "site_id": args.site,
        "fetched_utc": fetched_utc_str,
        "anchor_utc": anchor_utc_str,
        "points": points,
        "sources": sources_metadata,
        "normalized_sha256": normalized_sha256,
        "selection_rule": "anchor = hora UTC completa anterior a fetched_utc; sin elección manual",
        "tool_version": "1.0.0",
    }

    manifest_json = json.dumps(manifest_dict, indent=2, sort_keys=True) + "\n"
    manifest_path = snap_dir / "manifest.json"
    manifest_path.write_text(manifest_json, encoding="utf-8")

    # 6. Update ACTIVE pointer
    active_path = out_dir / "ACTIVE"
    active_path.write_text(snap_id + "\n", encoding="utf-8")

    # 7. Mirror to _bundled assets for packaging parity
    repo_root = Path(__file__).resolve().parents[1]
    bundled_env_dir = repo_root / "src" / "sdv_governor" / "_bundled" / "data" / "environment" / args.site
    try:
        bundled_snap_dir = bundled_env_dir / snap_id
        bundled_raw_dir = bundled_snap_dir / "raw"
        bundled_raw_dir.mkdir(parents=True, exist_ok=True)
        for f in snap_dir.iterdir():
            if f.is_file():
                (bundled_snap_dir / f.name).write_bytes(f.read_bytes())
        for f in raw_dir.iterdir():
            if f.is_file():
                (bundled_raw_dir / f.name).write_bytes(f.read_bytes())
        (bundled_env_dir / "ACTIVE").write_text(snap_id + "\n", encoding="utf-8")
        print(f"Mirrored snapshot to bundled location: {bundled_snap_dir}")
    except Exception as e:
        print(f"Warning: Could not mirror to bundled directory: {e}")

    print(f"Successfully generated environmental snapshot {snap_id} at {snap_dir}")
    print(f"env_series.json sha256: {normalized_sha256}")
    print(f"Active snapshot set to: {snap_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
