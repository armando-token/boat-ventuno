/**
 * Cliente API para el SDV Resource Governor (`http://127.0.0.1:8765/api/*` o proxy local `/api/*`).
 * Opera exclusivamente contra el backend autoritativo del gobernador (`LIVE`).
 * Si se pierde la conexión (HTTP 503 o fallo de red), congela el último snapshot válido y entra en
 * estado `STALE_DISCONNECTED` (`connection_state: "STALE"`), sin fabricar decisiones ni presupuestos locales (ALIGN-T38).
 * Solo carga `offlineFixture.js` cuando la URL incluye explícitamente `?mode=preview_synthetic`.
 */

import { buildDeterministicOfflineState } from "./offlineFixture.js";

function isPreviewSyntheticRequested() {
  if (typeof window === "undefined" || !window.location) return false;
  const params = new URLSearchParams(window.location.search || "");
  return params.get("mode") === "preview_synthetic";
}

export class GovernorClient {
  constructor() {
    this.bridgeMode = "CONNECTING";
    this.connectionState = "CONNECTING";
    this.lastPayload = null;
    this.disconnectedAtIso = null;
    this.candidatebaseUrls = ["", "http://127.0.0.1:8765"];
    this.activeBaseUrl = "";
    this.previewSynthetic = isPreviewSyntheticRequested();
  }

  getStaleInfo() {
    return {
      lastSnapshotId: this.lastPayload?.snapshot_id || "NONE",
      lastVersion: this.lastPayload?.snapshot_version ?? this.lastPayload?.state_version ?? 0,
      lastSimTimeS:
        this.lastPayload?.sim_time_s ??
        this.lastPayload?.current_sim_time_s ??
        this.lastPayload?.live_operational_state?.sim_time_s ??
        0,
      disconnectedAtIso: this.disconnectedAtIso,
    };
  }

  _markDisconnected() {
    this.bridgeMode = "STALE_DISCONNECTED";
    this.connectionState = "STALE";
    if (!this.disconnectedAtIso) {
      this.disconnectedAtIso = new Date().toISOString();
    }
  }

  async _request(method, path, body = null) {
    if (this.previewSynthetic) {
      this.bridgeMode = "PREVIEW_SYNTHETIC";
      this.connectionState = "PREVIEW_SYNTHETIC";
      return { ok: false, status: 0, data: null, mode: "PREVIEW_SYNTHETIC" };
    }

    for (const base of this.candidatebaseUrls) {
      const url = `${base}${path}`;
      try {
        const opts = {
          method,
          headers: body ? { "Content-Type": "application/json" } : {},
          body: body ? JSON.stringify(body) : undefined,
        };
        const res = await fetch(url, opts);
        if (res.status === 503) {
          continue;
        }
        if (res.ok || res.status === 422) {
          this.activeBaseUrl = base;
          const headerMode = res.headers.get("X-SDV-Bridge-Mode");
          this.bridgeMode = headerMode || (base === "http://127.0.0.1:8765" ? "LIVE_8765" : "LIVE_API");
          this.connectionState = "LIVE";
          this.disconnectedAtIso = null;
          const data = await res.json();
          return { ok: res.ok, status: res.status, data, mode: this.bridgeMode, connectionState: "LIVE" };
        }
      } catch (_err) {
        // Probar siguiente URL candidata
      }
    }

    this._markDisconnected();
    return {
      ok: false,
      status: 503,
      data: null,
      mode: "STALE_DISCONNECTED",
      connectionState: "STALE",
      staleInfo: this.getStaleInfo(),
    };
  }

  async getState(priorityProfile = "survey_primary") {
    if (this.previewSynthetic) {
      const syn = buildDeterministicOfflineState(priorityProfile);
      syn.connection_state = "PREVIEW_SYNTHETIC";
      syn.source = "SYNTHETIC_FIXTURE";
      this.lastPayload = syn;
      return {
        payload: syn,
        mode: "PREVIEW_SYNTHETIC",
        connectionState: "PREVIEW_SYNTHETIC",
        staleInfo: null,
      };
    }

    const resp = await this._request(
      "GET",
      `/api/state?priority_profile=${encodeURIComponent(priorityProfile)}`
    );
    if (resp.data && resp.data.status === "OK") {
      this.lastPayload = resp.data;
      return {
        payload: resp.data,
        mode: this.bridgeMode,
        connectionState: "LIVE",
        staleInfo: null,
      };
    }
    return {
      payload: this.lastPayload,
      mode: "STALE_DISCONNECTED",
      connectionState: "STALE",
      staleInfo: this.getStaleInfo(),
    };
  }

  async resetSession(priorityProfile = "survey_primary") {
    const resp = await this._request("POST", "/api/reset", {
      priority_profile_id: priorityProfile,
    });
    if (resp.data && resp.ok) {
      return await this.getState(priorityProfile);
    }
    return {
      payload: this.lastPayload,
      mode: this.bridgeMode,
      connectionState: this.connectionState,
      staleInfo: this.getStaleInfo(),
    };
  }

  async switchProfile(priorityProfile = "survey_primary", extra = {}) {
    const resp = await this._request("POST", "/api/profile", {
      priority_profile_id: priorityProfile,
      ...extra,
    });
    return {
      ok: resp.ok,
      status: resp.status,
      response: resp.data,
      mode: this.bridgeMode,
      connectionState: this.connectionState,
      staleInfo: this.getStaleInfo(),
    };
  }

  async advanceClock(reqBody = {}) {
    const resp = await this._request("POST", "/api/advance", reqBody);
    return {
      ok: resp.ok,
      status: resp.status,
      response: resp.data,
      mode: this.bridgeMode,
      connectionState: this.connectionState,
      staleInfo: this.getStaleInfo(),
    };
  }

  async injectPerturbation(perturbationType, priorityProfile = "survey_primary", extra = {}) {
    const resp = await this._request("POST", "/api/perturb", {
      perturbation_type: perturbationType,
      priority_profile_id: priorityProfile,
      ...extra,
    });
    if (resp.data) {
      return {
        ok: resp.ok,
        status: resp.status,
        response: resp.data,
        mode: this.bridgeMode,
        connectionState: this.connectionState,
      };
    }
    return {
      ok: false,
      status: resp.status,
      response: {
        status: "UPSTREAM_UNAVAILABLE",
        connection_state: "STALE",
        message: "Disconnected from authoritative governor (:8765). Local perturbation simulation is disabled.",
      },
      mode: "STALE_DISCONNECTED",
      connectionState: "STALE",
      staleInfo: this.getStaleInfo(),
    };
  }

  async evaluateLiveAdmission(reqBody) {
    const resp = await this._request("POST", "/api/admit_live", reqBody);
    if (resp.data) {
      return {
        ok: resp.ok,
        status: resp.status,
        response: resp.data,
        mode: this.bridgeMode,
        connectionState: this.connectionState,
      };
    }
    return {
      ok: false,
      status: resp.status,
      response: {
        status: "UPSTREAM_UNAVAILABLE",
        connection_state: "STALE",
        message: "Disconnected from authoritative governor (:8765). Local admission simulation is disabled.",
      },
      mode: "STALE_DISCONNECTED",
      connectionState: "STALE",
      staleInfo: this.getStaleInfo(),
    };
  }
}
