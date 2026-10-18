"""Advisory state machine with hysteresis and non-canned sentence generator.

Implements §6.7 of MEGAPLAN:
- States: QUIET, WATCH, WARN, BREACH_OBSERVED, SUPPRESSED.
- Hysteresis:
  - WARN entered when h*_hi <= 45s for 3 consecutive seconds with status OK.
  - Exits to QUIET when h*_hi > 60s for 10 consecutive seconds.
  - WATCH entered on single sample with h*_hi <= 60s.
  - SUPPRESSED on telemetry anomalies (WARMUP, STALE, UNAVAILABLE, REGIME_BREAK).
  - BREACH_OBSERVED when existing reactive detector acts.
- Non-canned sentence generator advisory_sentence(p, guest_name, src).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .ood import OOD_WARN_PERSIST_S


VALID_ADVISORY_STATES = ("QUIET", "WATCH", "WARN", "BREACH_OBSERVED", "SUPPRESSED")


@dataclass
class AdvisoryTransition:
    """Audit record for a state machine transition."""
    t: int
    from_state: str
    to_state: str
    cause: str
    h_star_hi: Optional[float]
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "t": self.t,
            "from": self.from_state,
            "to": self.to_state,
            "cause": self.cause,
            "h_star_hi": self.h_star_hi,
            "status": self.status,
        }


class AdvisoryStateMachine:
    """Per-guest advisory state machine with temporal hysteresis."""

    def __init__(self, guest_id: str):
        self.guest_id = guest_id
        self.state: str = "QUIET"
        self.risk_streak_s: int = 0
        self.clear_streak_s: int = 0
        self.transitions: List[AdvisoryTransition] = []
        self.last_p: Optional[Dict[str, Any]] = None
        # Streaks are measured in simulated seconds (t - first qualifying sample + 1),
        # so the hysteresis is cadence-independent (1 Hz or coarser ticks).
        self._risk_since_t: Optional[int] = None
        self._clear_since_t: Optional[int] = None
        self._point_risk_since_t: Optional[int] = None
        self.first_warn_t: Optional[int] = None
        self.breach_observed_t: Optional[int] = None
        self.lead_s: Optional[int] = None

    def _transition(
        self,
        t: int,
        new_state: str,
        cause: str,
        h_star_hi: Optional[float],
        status: str,
    ) -> None:
        if new_state == self.state:
            return
        if new_state == "WARN" and self.first_warn_t is None:
            self.first_warn_t = t
        rec = AdvisoryTransition(
            t=t,
            from_state=self.state,
            to_state=new_state,
            cause=cause,
            h_star_hi=h_star_hi,
            status=status,
        )
        self.transitions.append(rec)
        self.state = new_state

    def _reset_streaks(self) -> None:
        self._risk_since_t = None
        self._clear_since_t = None
        self._point_risk_since_t = None
        self.risk_streak_s = 0
        self.clear_streak_s = 0

    def update(
        self,
        t: int,
        h_star_hi: Optional[float] = None,
        status: str = "OK",
        reactive_breach_active: bool = False,
        p: Optional[Dict[str, Any]] = None,
        ood_active: bool = False,
    ) -> str:
        """Advance the advisory state machine by one second tick.
        
        Args:
            t: Current simulation time (seconds).
            h_star_hi: Breach horizon in seconds evaluated on upper bound (or extracted from p).
            status: Telemetry status ("OK", "WARMUP", "STALE", "UNAVAILABLE", "REGIME_BREAK").
            reactive_breach_active: True if reactive governor event is currently active.
            p: Projection dictionary from projector.py (optional).
            ood_active: Telemetry residuals are outside the calibrated range (see ood.py). WARN then
                requires the point forecast (not just the upper band) to cross within 45 s for
                OOD_WARN_PERSIST_S consecutive seconds; upper-band-only risk stays at WATCH.
            
        Returns:
            Current state string.
        """
        if p is not None:
            self.last_p = p
            if h_star_hi is None:
                band = p.get("h_star_band_s")
                if band and len(band) > 0:
                    h_star_hi = float(band[0])
                elif p.get("h_star_s") is not None:
                    h_star_hi = float(p["h_star_s"])

        # 1. Reactive breach has highest priority (§6.7)
        if reactive_breach_active:
            if self.state != "BREACH_OBSERVED":
                self._transition(t, "BREACH_OBSERVED", "REACTIVE_BREACH_DETECTED", h_star_hi, status)
                if self.breach_observed_t is None:
                    self.breach_observed_t = t
                    if self.first_warn_t is not None:
                        self.lead_s = t - self.first_warn_t
            self._reset_streaks()
            return self.state
        elif self.state == "BREACH_OBSERVED":
            # Reactive event closed by governor
            self._transition(t, "QUIET", "REACTIVE_BREACH_RESOLVED", h_star_hi, status)
            self._reset_streaks()

        # 2. Telemetry suppression checks
        if status in ("WARMUP", "STALE", "UNAVAILABLE", "REGIME_BREAK"):
            self._reset_streaks()
            if self.state != "SUPPRESSED":
                self._transition(t, "SUPPRESSED", f"TELEMETRY_STATUS_{status}", h_star_hi, status)
            return self.state
        elif self.state == "SUPPRESSED" and status == "OK":
            self._transition(t, "QUIET", "TELEMETRY_STATUS_RECOVERED_OK", h_star_hi, status)

        # 3. Nominal operation: status == "OK"
        is_in_risk_45 = (h_star_hi is not None and h_star_hi <= 45.0)
        is_in_risk_60 = (h_star_hi is not None and h_star_hi <= 60.0)
        is_clear = (h_star_hi is None or h_star_hi > 60.0)

        if is_in_risk_45:
            if self._risk_since_t is None:
                self._risk_since_t = t
            self.risk_streak_s = t - self._risk_since_t + 1
        else:
            self._risk_since_t = None
            self.risk_streak_s = 0

        if is_clear:
            if self._clear_since_t is None:
                self._clear_since_t = t
            self.clear_streak_s = t - self._clear_since_t + 1
        else:
            self._clear_since_t = None
            self.clear_streak_s = 0

        point_risk_45 = bool(
            p is not None
            and p.get("point_forecast_crosses", False)
            and p.get("h_star_s") is not None
            and float(p["h_star_s"]) <= 45.0
        )
        if point_risk_45:
            if self._point_risk_since_t is None:
                self._point_risk_since_t = t
            point_streak_s = t - self._point_risk_since_t + 1
        else:
            self._point_risk_since_t = None
            point_streak_s = 0

        if ood_active:
            warn_ready = point_streak_s >= OOD_WARN_PERSIST_S
            warn_cause = f"OOD_POINT_RISK_LE_45S_FOR_{OOD_WARN_PERSIST_S}S"
        else:
            warn_ready = self.risk_streak_s >= 3
            warn_cause = "RISK_LE_45S_FOR_3S"

        # 4. State transitions
        if self.state == "QUIET":
            if warn_ready:
                self._transition(t, "WARN", warn_cause, h_star_hi, status)
            elif is_in_risk_60:
                self._transition(t, "WATCH", "RISK_LE_60S_SAMPLE", h_star_hi, status)

        elif self.state == "WATCH":
            if warn_ready:
                self._transition(t, "WARN", warn_cause, h_star_hi, status)
            elif self.clear_streak_s >= 10:
                self._transition(t, "QUIET", "CLEAR_GT_60S_FOR_10S", h_star_hi, status)

        elif self.state == "WARN":
            if self.clear_streak_s >= 10:
                self._transition(t, "QUIET", "CLEAR_GT_60S_FOR_10S", h_star_hi, status)

        return self.state


def advisory_sentence(p: Dict[str, Any], guest_name: str, src: str = "") -> str:
    """Generate non-canned advisory sentence from projection witness and telemetry per §6.7.
    
    Format:
    "{guest_id} {guest_name} rising {slope:+.0f} W/s; "
    "projected to exceed {threshold_kw:.1f} kW (110% of declared {declared_kw:.1f} kW) "
    "in ~{h_star_s} s (90% band {band[0]}–{band[1]} s); "
    "binding: {w_class} {w_component} {w_projected_kw:.1f}/{w_capacity_kw:.1f} kW. "
    "Advisory only — governor action unchanged."
    """
    gid = p.get("guest_id", "")
    name = guest_name or ""
    if gid and name.startswith(gid):
        name = name[len(gid):].lstrip(" -—:")
    slope = float(p.get("slope_w_per_s", 0.0))
    threshold_kw = float(p.get("threshold_w", 0.0)) / 1000.0
    declared_kw = float(p.get("declared_w", 0.0)) / 1000.0
    h_star = p.get("h_star_s", 0)
    band = p.get("h_star_band_s", [h_star, h_star])

    w = p.get("witness", {})
    w_class = w.get("class") or w.get("resource_class", "power")
    w_comp = w.get("component") or w.get("constraint_id", "")
    w_proj_kw = float(w.get("projected_w", w.get("demand", 0.0))) / 1000.0
    w_cap_kw = float(w.get("capacity_w", w.get("capacity", 0.0))) / 1000.0

    if slope >= 0.5:
        trend = f"rising {slope:+.0f} W/s"
    elif slope <= -0.5:
        trend = f"falling {slope:+.0f} W/s"
    else:
        trend = "flat (|slope| < 0.5 W/s)"
    upper = f">{band[1]}" if p.get("band_open_upper") else f"{band[1]}"
    ood_note = ""
    if p.get("ood_ratio") is not None:
        ood_note = (
            f"Unfamiliar telemetry (residual noise x{float(p['ood_ratio']):.1f} calibration): "
            f"band widened, WARN on point forecast only. "
        )
    if p.get("point_forecast_crosses", True):
        when = f"projected to exceed {threshold_kw:.1f} kW (110% of declared {declared_kw:.1f} kW) in ~{h_star} s "
    else:
        when = (
            f"upper 90% bound reaches {threshold_kw:.1f} kW (110% of declared {declared_kw:.1f} kW) in ~{band[0]} s; "
            f"point forecast stays below within 60 s "
        )
    return (
        f"{gid} {name} {trend}; "
        f"{when}(90% band {band[0]}–{upper} s); "
        f"binding: {w_class} {w_comp} {w_proj_kw:.1f}/{w_cap_kw:.1f} kW. "
        f"{ood_note}"
        f"Advisory only — governor action unchanged."
    )
