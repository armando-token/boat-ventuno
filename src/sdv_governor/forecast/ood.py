"""Out-of-distribution (OOD) guard for the advisory observer.

The conformal band is calibrated on telemetry whose one-step Holt residual scale is ~1.4% of the
declared power. When live residuals are much larger (unfamiliar noise, coarse sensors), that band is
too narrow and its upper edge grazes the 110% threshold on pure noise, producing false WARNs.

The guard reads the residual scale already tracked by HoltState (EWM of one-step residuals, gamma 0.05)
and, with hysteresis, flags the guest as OOD. While OOD the observer:
  1. caps widened-band-only risk at WATCH: WARN needs the point forecast plus half the calibrated
     band (OOD_GATE_BAND_FRACTION) to cross the physical limit within 45 s,
  2. widens the displayed conformal band by the live/calibrated residual ratio (online recalibration),
  3. requires that gated risk to persist OOD_WARN_PERSIST_S (6 s instead of 3 s) before WARN.

OOD_SIGMA_REF/ratios come from the calibration split (seeds 2000-2079) and are re-derived by
tests/unit/test_forecast_ood_guard.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Median one-step residual sigma (normalized z units) on the calibration split, status OK.
OOD_SIGMA_REF = 0.0139
# Enter OOD above 1.9x the reference (above in-distribution p99 ~0.021 for non-spike profiles);
# leave below 1.6x. Large spikes can also trip it briefly, which only makes WARN stricter.
OOD_RATIO_ON = 1.9
OOD_RATIO_OFF = 1.6
OOD_WARN_PERSIST_S = 6
# While OOD, WARN is gated on point forecast + this fraction of the *calibrated* (not widened) upper
# band. Selected on a held-out OOD validation set (seeds 6000-6119, never used by any split), not on
# the benchmark 'ood' split: 0.25 -> recall 73%, 0.5 -> 90% / 0.7 FA/h, 0.75 -> 95% / 1.3 FA/h.
OOD_GATE_BAND_FRACTION = 0.5


@dataclass
class OodGuard:
    """Per-guest hysteretic OOD flag driven by the Holt residual EWM variance."""

    active: bool = False
    ratio: float = 1.0

    def update(self, resid_ewm_var: float) -> bool:
        sigma = math.sqrt(max(0.0, float(resid_ewm_var)))
        self.ratio = sigma / OOD_SIGMA_REF
        if self.active:
            if self.ratio < OOD_RATIO_OFF:
                self.active = False
        elif self.ratio >= OOD_RATIO_ON:
            self.active = True
        return self.active

    @property
    def band_scale(self) -> float:
        """Conformal band multiplier: live/calibrated residual ratio while OOD, 1.0 otherwise."""
        return max(1.0, self.ratio) if self.active else 1.0
