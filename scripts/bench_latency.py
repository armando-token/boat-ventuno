#!/usr/bin/env python3
"""Measure HTTP latency (p50/p95/p99) of the governor API; stdlib only (urllib + perf_counter).

Writes artifacts/<run_id>/latency_board.json. Gate G-LAT: exit 1 if any p95 > 1.5 s.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GATE_P95_MS = 1500.0
DEFAULT_ENDPOINTS = (
    ("GET", "/api/state", None),
    ("GET", "/api/forecast", None),
    ("GET", "/api/environment", None),
    ("POST", "/api/arbitrate", {}),
)


def _pct(sorted_ms: list[float], q: float) -> float:
    idx = max(0, math.ceil(q * len(sorted_ms)) - 1)
    return round(sorted_ms[idx], 3)


def _call(target: str, method: str, path: str, body: object, timeout: float) -> tuple[float, int]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(target.rstrip("/") + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        exc.read()
        status = exc.code
    return (time.perf_counter() - t0) * 1000.0, status


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", required=True, help="base URL, e.g. http://127.0.0.1:8765")
    ap.add_argument("--n", type=int, default=50, help="requests per endpoint (default 50)")
    ap.add_argument("--run-id", default=None, help="artifacts/<run_id> (default: latency_<UTC timestamp>)")
    ap.add_argument("--out-root", default="artifacts")
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args(argv)

    run_id = args.run_id or datetime.now(timezone.utc).strftime("latency_%Y%m%dT%H%M%SZ")
    results = []
    gate_ok = True
    for method, path, body in DEFAULT_ENDPOINTS:
        _call(args.target, method, path, body, args.timeout)  # warm-up, not counted
        samples: list[float] = []
        statuses: dict[str, int] = {}
        for _ in range(args.n):
            ms, status = _call(args.target, method, path, body, args.timeout)
            samples.append(ms)
            statuses[str(status)] = statuses.get(str(status), 0) + 1
        samples.sort()
        p95 = _pct(samples, 0.95)
        passed = p95 <= GATE_P95_MS and all(int(s) < 500 for s in statuses)
        gate_ok = gate_ok and passed
        results.append({
            "endpoint": f"{method} {path}",
            "n": args.n,
            "p50_ms": _pct(samples, 0.50),
            "p95_ms": p95,
            "p99_ms": _pct(samples, 0.99),
            "max_ms": round(samples[-1], 3),
            "status_counts": statuses,
            "g_lat_pass": passed,
        })

    out_dir = Path(args.out_root) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "latency_board.json"
    payload = {
        "run_id": run_id,
        "target": args.target,
        "gate_p95_ms": GATE_P95_MS,
        "g_lat_pass": gate_ok,
        "endpoints": results,
    }
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} g_lat_pass={gate_ok}")
    return 0 if gate_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
