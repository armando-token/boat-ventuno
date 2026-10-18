/**
 * viz3d/app_standalone.js
 * Motor 3D Naval Autocontenido de Alta Fidelidad (Zero-CDN / Funciona 100% Offline y en file://).
 *
 * Incluye:
 * - Rasterizador 3D con Z-Buffer (Painter's Algorithm por profundidad + iluminación normal Lambert/Blinn-Phong)
 * - Casco Fincantieri SDV 60.0m × 11.0m a escala 1:1 con 500+ polígonos curvos (bulbo de proa, pantoque, francobordo flare)
 * - Superestructura completa, Puente 360°, Mástil integrado (mast_1, mast_2, mast_3), Cubierta de vuelo CAP 437
 * - Actores invitados: Dron VTOL G4, Submarino AUV G3 bajo el agua con cono sonar, Contenedor G5 Edge + haz SATCOM
 * - Modo Rayos X interior: Generadores DG1/DG2, Buses LINE_A/LINE_B, Lazo Agua Helada ISO 14726 con pulsos y Rack de 4 nodos
 * - Océano dinámico Gerstner + Estela Kelvin V-Wake + Bahía de San Francisco y Puente Golden Gate al horizonte
 */

(function () {
  "use strict";

  // ==========================================================================
  // 1. ESTADO DETERMINISTA DEL RESOURCE GOVERNOR Y CLIENTE API (:8765 / LOCAL)
  // ==========================================================================
  function buildDeterministicState(priorityProfileId) {
    const isSurvey = priorityProfileId !== "rescue_primary";
    const holdCmdS = isSurvey ? 720 : 480;
    const holdAckS = holdCmdS + 15;
    const turnS = holdAckS;
    const stabS = turnS + 90;
    const touchdownS = turnS + 360;

    const timelineTicks = [];
    for (let tick = 0; tick <= 90; tick++) {
      const simTimeS = tick * 20;
      const events = [];
      let vesselMode = "QUIET_SURVEY";
      let powerCapW = 200000;
      let coolingCapW = 133333;
      let g3Mode = "SURVEY_QUIET";
      let g4Mode = "FLIGHT_PATROL";
      let g5Mode = "FULL_SPECTRUM";
      let planVersion = 0;

      if (simTimeS === 0) events.push("EVENT_MISSION_START@0s");
      if (simTimeS >= 480) {
        planVersion = 1;
        if (simTimeS === 480) events.push("ALERT_G4_BATTERY_18.0PCT@480s");
      }
      if (simTimeS >= holdCmdS && simTimeS - 20 < holdCmdS) {
        events.push("G3_ACOUSTIC_HOLD_CMD_SENT@" + holdCmdS + "s");
      }
      if (simTimeS >= holdAckS) {
        g3Mode = "HOLD_ACOUSTIC";
        if (simTimeS - 20 < holdAckS) events.push("G3_ENTERED_HOLD_ACOUSTIC@" + holdAckS + "s");
      }
      if (simTimeS >= turnS) {
        vesselMode = "UAV_RECOVERY_HEADING";
        if (simTimeS - 20 < turnS) events.push("VESSEL_TURNED_INTO_WIND_FOR_G4_RECOVERY@" + turnS + "s");
      }
      if (simTimeS >= stabS) {
        g4Mode = "LAUNCH_RECOVERY_OPTICS";
        if (simTimeS - 20 < stabS) events.push("G4_STARTED_35KW_RECOVERY_APPROACH@" + stabS + "s");
      }
      if (simTimeS >= touchdownS && simTimeS - 20 < touchdownS) {
        events.push("G4_TOUCHDOWN_AND_SECURED_IN_HANGAR@" + touchdownS + "s");
        events.push("G4_STARTED_20MIN_35KW_HANGAR_CHARGE@" + touchdownS + "s");
      }
      if (simTimeS >= 720 && simTimeS < 1500) {
        powerCapW = 100000;
        vesselMode = "DEGRADED_POWER";
        planVersion = 2;
        g5Mode = "DEGRADED_70PCT_LOSS";
        if (simTimeS === 720) {
          events.push("PERTURBATION_GENERATOR_LOSS_TO_100KW@720s");
          events.push("D4_STEPDOWN:G5:FULL_SPECTRUM->DEGRADED_70PCT_LOSS(45->8kW)");
        }
      }
      if (simTimeS >= 1500) {
        powerCapW = 200000;
        vesselMode = simTimeS >= touchdownS ? "QUIET_SURVEY" : "UAV_RECOVERY_HEADING";
        g5Mode = "FULL_SPECTRUM";
        planVersion = 3;
        if (simTimeS === 1500) {
          events.push("RESTORATION_RESERVE_200KW_COOLING_NOMINAL@1500s");
          events.push("G5_RECONNECTED_TO_FULL_SPECTRUM@1500s");
        }
      }

      const g3Power = 1000;
      const g4Power = g4Mode === "LAUNCH_RECOVERY_OPTICS" ? 35000 : g4Mode === "FLIGHT_PATROL" ? 10000 : 2000;
      const g5Power = g5Mode === "FULL_SPECTRUM" ? 45000 : 8000;
      const actualPowerW = g3Power + g4Power + g5Power;
      const committedPowerW = 25000 + g4Power + g5Power;
      const coolingWaterW = 1000 + g4Power + (g5Mode === "FULL_SPECTRUM" ? 43800 : 7500);

      timelineTicks.push({
        tick: tick,
        sim_time_s: simTimeS,
        plan_version: planVersion,
        vessel_mode: vesselMode,
        events: events,
        guest_modes: {
          G3: { mode: g3Mode, powered_on: true, mode_elapsed_s: simTimeS },
          G4: { mode: g4Mode, powered_on: true, mode_elapsed_s: Math.max(0, simTimeS - stabS) },
          G5: { mode: g5Mode, powered_on: true, mode_elapsed_s: simTimeS },
        },
        power_actual_w: actualPowerW,
        power_committed_w: committedPowerW,
        power_capacity_w: powerCapW,
        cooling_actual_w: coolingWaterW,
        cooling_capacity_w: coolingCapW,
        feasible: committedPowerW <= powerCapW && coolingWaterW <= coolingCapW,
      });
    }

    const t0 = timelineTicks[0];
    const baseSnapshot = {
      feasible: true,
      classes: {
        power: { capacity: 200000, degraded_capacity: t0.power_capacity_w, committed: t0.power_committed_w, actual: t0.power_actual_w, free: t0.power_capacity_w - t0.power_committed_w, unit: "W", feasible: true },
        cooling: { capacity: 160000, degraded_capacity: 133333, committed: t0.cooling_actual_w, actual: t0.cooling_actual_w, free: 133333 - t0.cooling_actual_w, unit: "W", feasible: true },
        positions: {
          capacity: 6, degraded_capacity: 6, committed: 4, actual: 4, free: 2, unit: "slots", feasible: true,
          local_limits: {
            bay1_20ft: ["G3:G3_AUV_CRADLE_20FT"], bay1_total_mass_kg: 5500, bay1_max_mass_kg: 8000,
            bay2_10ft: ["G5:G5_COMPUTE_10FT"], bay2_total_mass_kg: 3000, bay2_max_mass_kg: 4000,
            hangar: ["G4:G4_HANGAR_STATION"], hangar_total_mass_kg: 850, hangar_max_mass_kg: 1000,
            hangar_roof: ["G5:G5_RF_ANTENNA"], hangar_roof_total_mass_kg: 25,
            flight_deck: ["G4:G4_DRONE_VEHICLE"],
          },
        },
        mast: {
          capacity: 1, degraded_capacity: 1, committed: 1, actual: 1, free: 0, unit: "slot", feasible: true,
          local_limits: { guest_slot_mast_3: { owner: "G1:G1_NAV_SENSOR", mass_kg: 45 } },
        },
        mass_stability: {
          capacity: 10000, degraded_capacity: 10000, committed: 9560, actual: 9560, free: 440, unit: "kg", feasible: true,
          local_limits: {
            mass_margin_kg: { actual_kg: 9560, max_kg: 10000 },
            stability_equivalent_kg: { actual_kg_eq: 9833, max_kg_eq: 12000 },
          },
        },
        data: { capacity: 10000000000, degraded_capacity: 10000000000, committed: 1120000000, actual: 1120000000, free: 8880000000, unit: "bps", feasible: true },
        compute: { capacity: 100, degraded_capacity: 100, committed: 24, actual: 24, free: 76, unit: "cores", feasible: true },
        time: { capacity: 2000, degraded_capacity: 2000, committed: 1, actual: 1, free: 1999, unit: "ms", feasible: true },
      },
    };

    return {
      status: "OK",
      state_version: 1,
      snapshot_id: "SNAP_V0001",
      sim_time_s: 0,
      priority_profile_id: priorityProfileId,
      initial_budget_snapshot: baseSnapshot,
      current_budget_snapshot: JSON.parse(JSON.stringify(baseSnapshot)),
      scenario_B_replay: {
        executed_touchdown_time_s: touchdownS,
        timeline_ticks: timelineTicks,
      },
    };
  }

  const isPreviewSynthetic = (function () {
    try {
      const params = new URLSearchParams(window.location.search || "");
      return params.get("mode") === "preview_synthetic" || window.location.protocol === "file:";
    } catch (_e) {
      return false;
    }
  })();

  let isBackendConnected = false;

  async function tryFetchBackend(method, path, bodyObj) {
    const bases = [];
    if (window.location && window.location.protocol && window.location.protocol.indexOf("http") === 0) {
      bases.push("");
    }
    bases.push("http://127.0.0.1:8765", "http://127.0.0.1:5173");
    for (let i = 0; i < bases.length; i++) {
      const b = bases[i];
      try {
        const controller = new AbortController();
        const timer = setTimeout(function () { controller.abort(); }, 800);
        const res = await fetch(b + path, {
          method: method,
          headers: bodyObj ? { "Content-Type": "application/json" } : {},
          body: bodyObj ? JSON.stringify(bodyObj) : undefined,
          signal: controller.signal,
        });
        clearTimeout(timer);
        if (res.status === 503) {
          continue;
        }
        if (res.ok || res.status === 422) {
          const data = await res.json();
          if (data && data.status === "UPSTREAM_UNAVAILABLE") {
            continue;
          }
          isBackendConnected = true;
          return { data: data, mode: b.indexOf("8765") !== -1 ? "LIVE :8765" : (b ? "LIVE :5173" : "LIVE GOVERNOR") };
        }
      } catch (_e) {
        // Silencioso si el servidor no está levantado
      }
    }
    isBackendConnected = false;
    return null;
  }

  // ==========================================================================
  // 2. ESTADO REACTIVO DE SIMULACIÓN Y CONSOLA DE PUENTE (IEC 62288 / BAM)
  // ==========================================================================
  let statePayload = buildDeterministicState("survey_primary");
  let currentTickIndex = 0;
  let playInterval = null;
  let navalLiveryActive = true;
  let xrayActive = false;
  let sfBayVisible = true;
  let exaggerateHydro = false;
  let ecoBatteryMode = true;
  let autoDirectorEnabled = true;
  let activeShotKey = "ORBIT_HERO";
  let activePaletteIdx = 0;
  const PALETTES = ["dusk", "day", "night"];

  const sim = {
    simTimeS: 0,
    vesselMode: "QUIET_SURVEY",
    speedKn: 3.0,
    headingDeg: 90.0,
    windKn: 14,
    seaState: 2,
    seawaterTempC: 24.0,
    gen2Tripped: false,
    coolingDegraded: false,
    g3Mode: "SURVEY_QUIET",
    g4Mode: "FLIGHT_PATROL",
    g4Landed: false,
    g5Mode: "FULL_SPECTRUM",
    g1OnMast3: true,
    g5OnHangarRoof: true,
    guestMassKg: 9560,
    stabilityKgEq: 9833,
    bamAlerts: [],
    ghostSlot: null,
    ghostVerdict: "YES",
    ghostCandidate: "G4",
  };

  function safeEl(id) {
    return document.getElementById(id);
  }

  function updateSourceBadge(text, isLive) {
    const staleBanner = safeEl("staleBanner");
    if (staleBanner) {
      staleBanner.style.display = (!isLive && !isPreviewSynthetic) ? "block" : "none";
    }
    const el = safeEl("badge-bridge-source");
    if (!el) return;
    el.textContent = (isLive ? "● " : "⚠ ") + text;
    el.classList.toggle("offline", !isLive);
  }

  function renderBAMAlerts() {
    const listEl = safeEl("bam-alert-list");
    if (!listEl) return;
    if (!sim.bamAlerts.length) {
      listEl.innerHTML =
        '<div class="bam-item"><span>● NORMAL · All 8 physical resource classes within envelope</span><span style="color: var(--bam-ok)">OK</span></div>';
      return;
    }
    listEl.innerHTML = sim.bamAlerts
      .map(function (a) {
        return (
          '<div class="bam-item ' +
          a.level +
          " " +
          (a.acked ? "" : "unacked") +
          '"><div><strong>' +
          a.symbol +
          " " +
          a.code +
          "</strong> · " +
          a.msg +
          '</div><span style="font-weight:700;">' +
          (a.acked ? "ACKED" : "ACTIVE") +
          "</span></div>"
        );
      })
      .join("");
  }

  function renderEightClassStrip(snapshot) {
    const container = safeEl("eight-classes-container");
    if (!container || !snapshot || !snapshot.classes) return;
    const labels = [
      ["power", "1. Power", "kW", 1000],
      ["cooling", "2. Cooling", "kW", 1000],
      ["positions", "3. Positions", "slots", 1],
      ["mast", "4. Mast Slot", "slot", 1],
      ["mass_stability", "5. Mass/Stab", "t", 1000],
      ["data", "6. Data LAN", "Gbps", 1e9],
      ["compute", "7. Compute", "cores", 1],
      ["time", "8. Deadlines", "ms", 1],
    ];
    container.innerHTML = labels
      .map(function (item) {
        const key = item[0], title = item[1], unit = item[2], div = item[3];
        const c = snapshot.classes[key];
        const cap = (c.degraded_capacity / div).toFixed(div >= 1000 ? 1 : 0);
        const comm = (c.committed / div).toFixed(div >= 1000 ? 1 : 0);
        const act = (c.actual / div).toFixed(div >= 1000 ? 1 : 0);
        const pct = Math.min(100, Math.round((c.committed / Math.max(1, c.degraded_capacity)) * 100));
        return (
          '<div class="res-gauge-card ' +
          (c.feasible ? "" : "infeasible") +
          '"><div class="res-gauge-header"><span>' +
          title +
          '</span><span style="color:' +
          (c.feasible ? "var(--bam-ok)" : "var(--bam-alarm)") +
          '">' +
          (c.feasible ? "OK" : "BOUND") +
          '</span></div><div class="res-gauge-values">Firm: <strong>' +
          comm +
          "</strong> / " +
          cap +
          " " +
          unit +
          "<br/>Actual: " +
          act +
          " " +
          unit +
          '</div><div class="meter-bar"><div class="meter-fill ' +
          (!c.feasible ? "danger" : pct > 80 ? "warn" : "") +
          '" style="width:' +
          pct +
          '%"></div></div></div>'
        );
      })
      .join("");
  }

  function renderPMSAndCooling(snapshot) {
    if (!snapshot || !snapshot.classes) return;
    const pwr = snapshot.classes.power;
    const cool = snapshot.classes.cooling;
    const isGenDown = pwr.degraded_capacity <= 100000;
    sim.gen2Tripped = isGenDown;

    const dg2Box = safeEl("gen-box-2");
    const dg2Status = safeEl("gen-box-2-status");
    if (dg2Box && dg2Status) {
      dg2Box.className = isGenDown ? "genset-box tripped" : "genset-box";
      dg2Status.textContent = isGenDown ? "TRIPPED (FAULT)" : "ONLINE (STBD)";
    }

    if (safeEl("pms-reserve-label")) safeEl("pms-reserve-label").textContent = (pwr.degraded_capacity / 1000).toFixed(0) + " kW RES";
    if (safeEl("pwr-actual-txt")) safeEl("pwr-actual-txt").textContent = (pwr.actual / 1000).toFixed(1) + " kW";
    if (safeEl("pwr-comm-txt")) safeEl("pwr-comm-txt").textContent = (pwr.committed / 1000).toFixed(1) + " kW";

    const pwrPct = Math.min(100, Math.round((pwr.committed / Math.max(1, pwr.degraded_capacity)) * 100));
    const pwrFill = safeEl("pwr-meter-fill");
    if (pwrFill) {
      pwrFill.style.width = pwrPct + "%";
      pwrFill.className = "meter-fill " + (pwrPct > 95 ? "danger" : pwrPct > 80 ? "warn" : "");
    }

    if (safeEl("cool-cap-label")) safeEl("cool-cap-label").textContent = (cool.degraded_capacity / 1000).toFixed(1) + " kW CAP";
    if (safeEl("cool-actual-txt")) safeEl("cool-actual-txt").textContent = (cool.actual / 1000).toFixed(1) + " kW";
    const coolPct = Math.min(100, Math.round((cool.actual / Math.max(1, cool.degraded_capacity)) * 100));
    const coolFill = safeEl("cool-meter-fill");
    if (coolFill) {
      coolFill.style.width = coolPct + "%";
      coolFill.className = "meter-fill " + (coolPct > 95 ? "danger" : coolPct > 80 ? "warn" : "");
    }
  }

  // ==========================================================================
  // 3. MOTOR 3D NAVAL DE ALTA PRECISIÓN (100% NATIVO, CERO DEPENDENCIAS CDN)
  // ==========================================================================
  const canvas = safeEl("viewport-canvas") || safeEl("gl");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  const cam = {
    tx: -2.0, ty: 3.5, tz: 0.0,
    dtx: -2.0, dty: 3.5, dtz: 0.0,
    r: 58.0, dr: 58.0,
    theta: -0.68, dtheta: -0.68,
    phi: 0.34, dphi: 0.34,
    dragging: false,
    lastX: 0,
    lastY: 0,
  };

  const SHOTS = {
    ORBIT_HERO:      { tx: -2.0,  ty: 3.5,  tz: 0.0,  r: 58.0, theta: -0.68, phi: 0.34 },
    SF_GOLDEN_GATE:  { tx: 5.0,   ty: 5.5,  tz: -8.0, r: 68.0, theta: -0.42, phi: 0.24 },
    FLIGHT_DECK_G4:  { tx: -19.5, ty: 4.5,  tz: 0.0,  r: 32.0, theta: -0.95, phi: 0.42 },
    SUBSEA_AUV_G3:   { tx: -15.0, ty: -3.5, tz: 2.0,  r: 38.0, theta: -0.60, phi: 0.25 },
    MAST_SLOT_3:     { tx: 6.5,   ty: 13.5, tz: 0.0,  r: 26.0, theta: -0.55, phi: 0.30 },
    XRAY_ENGINE_ROOM:{ tx: -3.0,  ty: 2.2,  tz: 0.0,  r: 36.0, theta: -0.25, phi: 0.38 },
    TOP_GA_BLUEPRINT:{ tx: 0.0,   ty: 3.0,  tz: 0.0,  r: 64.0, theta: 0.0,   phi: 0.06 },
  };

  function setCameraShotFn(shotKey) {
    const s = SHOTS[shotKey] || SHOTS.ORBIT_HERO;
    activeShotKey = shotKey;
    cam.dtx = s.tx; cam.dty = s.ty; cam.dtz = s.tz;
    cam.dr = s.r;
    cam.dtheta = s.theta;
    cam.dphi = s.phi;
    document.querySelectorAll("[data-shot]").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-shot") === shotKey);
    });
  }

  function applyXRayFn(enabled) {
    xrayActive = enabled;
    const btn = safeEl("btn-xray-toggle");
    if (btn) btn.classList.toggle("active", enabled);
  }

  function resizeCanvas() {
    const dpr = Math.min(window.devicePixelRatio || 1, ecoBatteryMode ? 1.25 : 2.0);
    canvas.width = Math.max(640, Math.floor(window.innerWidth * dpr));
    canvas.height = Math.max(400, Math.floor(window.innerHeight * dpr));
  }
  window.addEventListener("resize", resizeCanvas);
  resizeCanvas();

  canvas.addEventListener("pointerdown", function (e) {
    cam.dragging = true;
    cam.lastX = e.clientX;
    cam.lastY = e.clientY;
    autoDirectorEnabled = false;
    const btn = safeEl("btn-auto-director");
    if (btn) btn.classList.remove("active");
    if (canvas.setPointerCapture) canvas.setPointerCapture(e.pointerId);
  });
  window.addEventListener("pointerup", function () { cam.dragging = false; });
  window.addEventListener("pointermove", function (e) {
    if (!cam.dragging) return;
    const dx = e.clientX - cam.lastX;
    const dy = e.clientY - cam.lastY;
    cam.lastX = e.clientX;
    cam.lastY = e.clientY;
    cam.dtheta -= dx * 0.007;
    cam.dphi = Math.max(-0.15, Math.min(1.25, cam.dphi + dy * 0.006));
  });
  canvas.addEventListener("wheel", function (e) {
    e.preventDefault();
    cam.dr = Math.max(18.0, Math.min(135.0, cam.dr + e.deltaY * 0.045));
  }, { passive: false });

  // Transformación 3D del barco (heave, pitch, roll) + proyección de cámara
  let cosT = 1, sinT = 0, cosP = 1, sinP = 0;
  let shipHeave = 0, shipPitch = 0, shipRoll = 0;

  function shipToWorld(x, y, z) {
    // Roll (alrededor de X) y Pitch (alrededor de Z)
    const y1 = y * Math.cos(shipRoll) - z * Math.sin(shipRoll);
    const z1 = y * Math.sin(shipRoll) + z * Math.cos(shipRoll);
    const x2 = x * Math.cos(shipPitch) - y1 * Math.sin(shipPitch);
    const y2 = x * Math.sin(shipPitch) + y1 * Math.cos(shipPitch) + shipHeave;
    return [x2, y2, z1];
  }

  function projectWorld(wx, wy, wz, w, h) {
    const dx = wx - cam.tx;
    const dy = wy - cam.ty;
    const dz = wz - cam.tz;

    const rx = dx * cosT - dz * sinT;
    const rz1 = dx * sinT + dz * cosT;

    const ry = dy * cosP - rz1 * sinP;
    const rz = dy * sinP + rz1 * cosP + cam.r;

    if (rz <= 1.2) return null;
    const fov = Math.min(w, h) * 1.08;
    const s = fov / rz;
    return {
      x: w * 0.5 + rx * s,
      y: h * 0.50 - ry * s,
      z: rz,
      s: s,
    };
  }

  // Construcción de la malla curva lofted del casco Fincantieri 60m (una sola vez)
  const hullFaces = [];
  (function buildCurvedHullMesh() {
    const stationsX = [
      -30.0, -28.0, -25.0, -21.5, -17.5, -13.5, -9.0, -4.5,
      0.0, 4.5, 9.0, 13.5, 17.5, 21.0, 24.0, 26.5, 28.4, 29.5, 30.2
    ];
    const vLevels = [0.0, 0.18, 0.38, 0.44, 0.50, 0.72, 1.0];

    function hullPoint(x, side, v) {
      const u = (x + 30.0) / 60.2;
      let halfBeam = 5.5;
      if (u < 0.15) halfBeam = 4.5 + (u / 0.15) * 1.0;
      else if (u > 0.62) {
        const tb = (u - 0.62) / 0.38;
        halfBeam = 5.5 * Math.pow(Math.max(0.0, Math.cos(tb * Math.PI * 0.495)), 0.80);
      }
      let sheerY = 2.75;
      if (u > 0.54) {
        const tf = Math.min(1.0, (u - 0.54) / 0.22);
        sheerY = 2.75 + (1.0 - Math.cos(tf * Math.PI)) * 0.5 * 2.05 + Math.max(0, u - 0.76) * 0.9;
      }
      let keelY = -3.1;
      if (u < 0.22) keelY = -3.1 + (1.0 - u / 0.22) * 1.35;
      const y = keelY + v * (sheerY - keelY);

      let wf = 1.0;
      if (v < 0.15) wf = 0.25 + (v / 0.15) * 0.35;
      else if (v < 0.45) wf = 0.60 + ((v - 0.15) / 0.30) * 0.36;
      else wf = 0.96 + (u > 0.58 ? ((u - 0.58) / 0.42) * 0.22 * ((v - 0.45) / 0.55) : 0.04 * ((v - 0.45) / 0.55));

      // Bulbo de proa (Bulbous Bow)
      if (u > 0.85 && v >= 0.0 && v <= 0.40) {
        const bu = (u - 0.85) / 0.15;
        const bv = 1.0 - Math.abs(v - 0.20) / 0.22;
        if (bv > 0) {
          const br = 1.25 * Math.sin(bu * Math.PI * 0.9) * bv;
          return [x + bu * 1.15, y, side * Math.max(halfBeam * wf, br)];
        }
      }
      return [x, y, side * halfBeam * wf];
    }

    [-1, 1].forEach(function (side) {
      for (let i = 0; i < stationsX.length - 1; i++) {
        for (let j = 0; j < vLevels.length - 1; j++) {
          const p00 = hullPoint(stationsX[i], side, vLevels[j]);
          const p10 = hullPoint(stationsX[i + 1], side, vLevels[j]);
          const p11 = hullPoint(stationsX[i + 1], side, vLevels[j + 1]);
          const p01 = hullPoint(stationsX[i], side, vLevels[j + 1]);

          const vMid = 0.5 * (vLevels[j] + vLevels[j + 1]);
          const uMid = (0.5 * (stationsX[i] + stationsX[i + 1]) + 30.0) / 60.0;
          let zone = "upper";
          if (vMid < 0.41) zone = "lower";
          else if (vMid < 0.48) zone = "boottop";
          else if (uMid > 0.72 && uMid < 0.77) zone = "stripe_orange";
          else if (uMid >= 0.69 && uMid <= 0.72) zone = "stripe_white";

          hullFaces.push({
            pts: side > 0 ? [p00, p10, p11, p01] : [p01, p11, p10, p00],
            zone: zone,
          });
        }
      }
    });

    // Espejo de popa (Transom)
    hullFaces.push({
      pts: [
        [-30.0, -1.75, -4.2],
        [-30.0, -1.75, 4.2],
        [-30.0, 2.75, 4.5],
        [-30.0, 2.75, -4.5],
      ],
      zone: "upper",
    });

    // Cubierta principal de popa y castillo de proa
    const deckPolys = [
      // Popa / Cubierta de vuelo (-30m a -8m)
      {
        pts: [
          [-30.0, 2.75, -4.5], [-8.0, 2.75, -5.4],
          [-8.0, 2.75, 5.4],   [-30.0, 2.75, 4.5]
        ],
        zone: "deck"
      },
      // Cubierta central (-8m a +9m)
      {
        pts: [
          [-8.0, 2.75, -5.4], [9.0, 3.3, -5.3],
          [9.0, 3.3, 5.3],    [-8.0, 2.75, 5.4]
        ],
        zone: "deck"
      },
      // Castillo de proa elevado (+9m a +30.2m)
      {
        pts: [
          [9.0, 3.4, -5.2], [18.5, 4.65, -4.3], [26.0, 5.05, -2.1], [30.2, 5.35, 0.0],
          [26.0, 5.05, 2.1], [18.5, 4.65, 4.3], [9.0, 3.4, 5.2]
        ],
        zone: "foredeck"
      }
    ];
    deckPolys.forEach(function (d) { hullFaces.push(d); });
  })();

  // Generador de cajas 3D para superestructura, racks, contenedores y actores
  function pushBoxFaces(outList, cx, cy, cz, sx, sy, sz, colorTop, colorSide, colorFront, stroke, isShipSpace) {
    const hx = sx * 0.5, hy = sy * 0.5, hz = sz * 0.5;
    const v = [
      [cx - hx, cy - hy, cz - hz], [cx + hx, cy - hy, cz - hz],
      [cx + hx, cy + hy, cz - hz], [cx - hx, cy + hy, cz - hz],
      [cx - hx, cy - hy, cz + hz], [cx + hx, cy - hy, cz + hz],
      [cx + hx, cy + hy, cz + hz], [cx - hx, cy + hy, cz + hz],
    ];
    const quads = [
      { idx: [0, 1, 2, 3], col: colorSide },
      { idx: [5, 4, 7, 6], col: colorSide },
      { idx: [1, 5, 6, 2], col: colorFront },
      { idx: [4, 0, 3, 7], col: colorFront },
      { idx: [3, 2, 6, 7], col: colorTop },
    ];
    quads.forEach(function (q) {
      outList.push({
        pts: q.idx.map(function (i) { return isShipSpace ? shipToWorld(v[i][0], v[i][1], v[i][2]) : v[i]; }),
        fill: q.col,
        stroke: stroke,
      });
    });
  }

  function shadeHex(hexStr, brightness) {
    const c = parseInt(hexStr.slice(1), 16);
    const r = Math.min(255, Math.max(0, Math.round(((c >> 16) & 255) * brightness)));
    const g = Math.min(255, Math.max(0, Math.round(((c >> 8) & 255) * brightness)));
    const b = Math.min(255, Math.max(0, Math.round((c & 255) * brightness)));
    return "rgb(" + r + "," + g + "," + b + ")";
  }

  function renderFrame(nowMs) {
    requestAnimationFrame(renderFrame);
    const t = nowMs * 0.001;
    const w = canvas.width;
    const h = canvas.height;

    // Suavizado exponencial de cámara
    cam.tx += (cam.dtx - cam.tx) * 0.10;
    cam.ty += (cam.dty - cam.ty) * 0.10;
    cam.tz += (cam.dtz - cam.tz) * 0.10;
    cam.r += (cam.dr - cam.r) * 0.10;
    cam.theta += (cam.dtheta - cam.theta) * 0.10;
    cam.phi += (cam.dphi - cam.phi) * 0.10;

    cosT = Math.cos(cam.theta); sinT = Math.sin(cam.theta);
    cosP = Math.cos(cam.phi);   sinP = Math.sin(cam.phi);

    // Movimiento hidrodinámico 6DOF + Inmersión TPC = 4.94 t/cm
    const sinkRealM = sim.guestMassKg / 494000.0;
    const sinkShowM = sinkRealM * (exaggerateHydro ? 20.0 : 1.0);
    shipHeave = Math.sin(t * 1.45) * (0.10 * sim.seaState) - sinkShowM;
    shipPitch = Math.sin(t * 1.05) * (0.011 * sim.seaState);
    shipRoll = Math.cos(t * 1.25) * (0.014 * sim.seaState);

    const sinkLbl = safeEl("hydro-sinkage-label");
    if (sinkLbl) {
      sinkLbl.textContent = exaggerateHydro
        ? "Sinkage: 1.93 cm (×20 = " + (sinkShowM * 100).toFixed(1) + " cm)"
        : "Sinkage: 1.93 cm (TPC 4.94 t/cm)";
    }

    const pal = PALETTES[activePaletteIdx];

    // 1. CIELO Y HORIZONTE ATMOSFÉRICO
    const horizonY = Math.max(h * 0.15, Math.min(h * 0.72, h * (0.44 - Math.sin(cam.phi) * 0.32)));
    const skyGrad = ctx.createLinearGradient(0, 0, 0, horizonY);
    if (pal === "day") {
      skyGrad.addColorStop(0, "#1d4ed8");
      skyGrad.addColorStop(0.65, "#60a5fa");
      skyGrad.addColorStop(1, "#bae6fd");
    } else if (pal === "night") {
      skyGrad.addColorStop(0, "#020409");
      skyGrad.addColorStop(0.7, "#070d19");
      skyGrad.addColorStop(1, "#111827");
    } else {
      skyGrad.addColorStop(0, "#06101e");
      skyGrad.addColorStop(0.55, "#152c4a");
      skyGrad.addColorStop(0.88, "#3b526b");
      skyGrad.addColorStop(1, "#c26d38");
    }
    ctx.fillStyle = skyGrad;
    ctx.fillRect(0, 0, w, horizonY + 2);

    // 2. OCÉANO PROFUNDO Y GRADIENTE DE FRESNEL
    const seaGrad = ctx.createLinearGradient(0, horizonY, 0, h);
    if (pal === "day") {
      seaGrad.addColorStop(0, "#0284c7");
      seaGrad.addColorStop(0.35, "#0369a1");
      seaGrad.addColorStop(1, "#082f49");
    } else if (pal === "night") {
      seaGrad.addColorStop(0, "#040b14");
      seaGrad.addColorStop(0.5, "#030712");
      seaGrad.addColorStop(1, "#010308");
    } else {
      seaGrad.addColorStop(0, "#163e5c");
      seaGrad.addColorStop(0.30, "#0a2942");
      seaGrad.addColorStop(0.75, "#061b2e");
      seaGrad.addColorStop(1, "#030e1a");
    }
    ctx.fillStyle = seaGrad;
    ctx.fillRect(0, horizonY, w, h - horizonY);

    // 3. BAHÍA DE SAN FRANCISCO Y PUENTE GOLDEN GATE AL HORIZONTE
    if (sfBayVisible) {
      // Colinas de Marin Headlands
      const hills = [
        [[-180, 0, -135], [-115, 28, -135], [-45, 0, -135]],
        [[-80, 0, -130], [-15, 22, -130], [55, 0, -130]],
        [[40, 0, -135], [115, 25, -135], [190, 0, -135]]
      ];
      hills.forEach(function (tri) {
        const p0 = projectWorld(tri[0][0], tri[0][1], tri[0][2], w, h);
        const p1 = projectWorld(tri[1][0], tri[1][1], tri[1][2], w, h);
        const p2 = projectWorld(tri[2][0], tri[2][1], tri[2][2], w, h);
        if (p0 && p1 && p2) {
          ctx.beginPath();
          ctx.moveTo(p0.x, p0.y); ctx.lineTo(p1.x, p1.y); ctx.lineTo(p2.x, p2.y);
          ctx.closePath();
          ctx.fillStyle = "rgba(18, 30, 46, 0.78)";
          ctx.fill();
        }
      });

      // Torres y cables del Golden Gate Bridge
      const ggZ = -115;
      const t1B = projectWorld(-38, 0, ggZ, w, h), t1T = projectWorld(-38, 28, ggZ, w, h);
      const t2B = projectWorld(38, 0, ggZ, w, h),  t2T = projectWorld(38, 28, ggZ, w, h);
      const dL = projectWorld(-95, 7.5, ggZ, w, h), dR = projectWorld(95, 7.5, ggZ, w, h);
      if (t1B && t1T && t2B && t2T && dL && dR) {
        ctx.strokeStyle = "rgba(205, 68, 46, 0.82)";
        ctx.lineWidth = Math.max(2.0, t1B.s * 1.1);
        ctx.beginPath();
        // Torres gemelas
        ctx.moveTo(t1B.x, t1B.y); ctx.lineTo(t1T.x, t1T.y);
        ctx.moveTo(t2B.x, t2B.y); ctx.lineTo(t2T.x, t2T.y);
        // Tablero vial
        ctx.moveTo(dL.x, dL.y);   ctx.lineTo(dR.x, dR.y);
        ctx.stroke();

        // Cable catenario principal
        ctx.beginPath();
        ctx.moveTo(dL.x, dL.y);
        ctx.quadraticCurveTo((dL.x + t1T.x) * 0.5, dL.y - 4, t1T.x, t1T.y);
        ctx.quadraticCurveTo((t1T.x + t2T.x) * 0.5, (t1B.y + dL.y) * 0.5 - 2, t2T.x, t2T.y);
        ctx.quadraticCurveTo((t2T.x + dR.x) * 0.5, dR.y - 4, dR.x, dR.y);
        ctx.lineWidth = Math.max(1.5, t1B.s * 0.7);
        ctx.stroke();
      }
    }

    // 4. MALLA DE OLAS GERSTNER 3D Y CONO SONAR SUBMARINO (AUV G3)
    for (let gz = -48; gz <= 48; gz += 4) {
      ctx.beginPath();
      let started = false;
      for (let gx = -64; gx <= 64; gx += 4) {
        const wy = Math.sin(gx * 0.13 + t * 2.2) * (0.14 * sim.seaState) +
                   Math.cos(gz * 0.19 - t * 1.7) * (0.11 * sim.seaState);
        const p = projectWorld(gx, wy, gz, w, h);
        if (p) {
          if (!started) { ctx.moveTo(p.x, p.y); started = true; }
          else ctx.lineTo(p.x, p.y);
        }
      }
      ctx.strokeStyle = "rgba(56, 189, 248, 0.13)";
      ctx.lineWidth = 1.0;
      ctx.stroke();
    }

    // Estela Hidrodinámica Kelvin V-Wake (19.47°) y Espuma de Popa
    [-1, 1].forEach(function (side) {
      ctx.beginPath();
      let started = false;
      for (let d = 0; d <= 52; d += 2.5) {
        const wx = 30.0 - d * 1.35;
        const wz = side * (1.1 + d * 0.23 + Math.sin(d * 0.45 - t * 5.5) * 0.28);
        const p = projectWorld(wx, 0.08, wz, w, h);
        if (p) {
          if (!started) { ctx.moveTo(p.x, p.y); started = true; }
          else ctx.lineTo(p.x, p.y);
        }
      }
      ctx.strokeStyle = "rgba(224, 242, 254, 0.52)";
      ctx.lineWidth = 2.2;
      ctx.stroke();
    });

    // Submarino AUV G3 bajo la superficie (X=-16.5, Y=-6.2, Z=6.5) + Cono Sonar Multihaz
    const auvX = -16.5 + Math.sin(t * 0.6) * 2.0;
    const auvY = -6.2;
    const auvZ = 6.5;
    const sonarBase1 = projectWorld(auvX - 3.5, -13.5, auvZ - 3.5, w, h);
    const sonarBase2 = projectWorld(auvX + 3.5, -13.5, auvZ + 3.5, w, h);
    const auvProj = projectWorld(auvX, auvY, auvZ, w, h);
    if (auvProj && sonarBase1 && sonarBase2) {
      ctx.beginPath();
      ctx.moveTo(auvProj.x, auvProj.y);
      ctx.lineTo(sonarBase1.x, sonarBase1.y);
      ctx.lineTo(sonarBase2.x, sonarBase2.y);
      ctx.closePath();
      ctx.fillStyle = sim.g3Mode === "HOLD_ACOUSTIC"
        ? "rgba(245, 158, 11, 0.24)"
        : "rgba(34, 211, 238, 0.22)";
      ctx.fill();
      ctx.strokeStyle = sim.g3Mode === "HOLD_ACOUSTIC" ? "#f59e0b" : "#22d3ee";
      ctx.lineWidth = 1.2;
      ctx.stroke();
    }

    // 5. CONSTRUCCIÓN DE LISTA DE POLÍGONOS 3D ORDENADOS POR PROFUNDIDAD (Z-BUFFER PAINTER'S)
    const renderList = [];

    // 5A. Casco Curvo Lofted Fincantieri 60m
    hullFaces.forEach(function (hf) {
      const worldPts = hf.pts.map(function (pt) { return shipToWorld(pt[0], pt[1], pt[2]); });
      // Normal de superficie para iluminación solar direccional
      const ux = worldPts[1][0] - worldPts[0][0], uy = worldPts[1][1] - worldPts[0][1], uz = worldPts[1][2] - worldPts[0][2];
      const vx = worldPts[2][0] - worldPts[0][0], vy = worldPts[2][1] - worldPts[0][1], vz = worldPts[2][2] - worldPts[0][2];
      let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
      const nLen = Math.hypot(nx, ny, nz) || 1.0;
      nx /= nLen; ny /= nLen; nz /= nLen;

      const lightDot = Math.max(0.0, nx * 0.52 + ny * 0.68 - nz * 0.51);
      const brightness = 0.68 + lightDot * 0.52;

      let fillCol = "#475569";
      let strokeCol = xrayActive ? "rgba(56, 189, 248, 0.42)" : "rgba(148, 163, 184, 0.22)";

      if (xrayActive) {
        fillCol = hf.zone === "lower" ? "rgba(127, 29, 29, 0.18)" : "rgba(30, 58, 95, 0.18)";
      } else {
        if (hf.zone === "lower") fillCol = shadeHex("#7f1d1d", brightness);
        else if (hf.zone === "boottop") fillCol = "#0f172a";
        else if (hf.zone === "stripe_orange") fillCol = "#f97316";
        else if (hf.zone === "stripe_white") fillCol = "#f8fafc";
        else if (hf.zone === "deck" || hf.zone === "foredeck") {
          fillCol = shadeHex("#27323d", brightness * 0.95);
          strokeCol = "rgba(148, 163, 184, 0.35)";
        } else {
          fillCol = shadeHex(navalLiveryActive ? "#4b5563" : "#1d4ed8", brightness);
        }
      }

      renderList.push({ pts: worldPts, fill: fillCol, stroke: strokeCol });
    });

    // 5B. Sistemas Internos (Visibles en Rayos X o Transparencia)
    if (xrayActive) {
      // DG1 (Port) & DG2 (Stbd)
      pushBoxFaces(renderList, -3.5, -1.0, -2.1, 4.0, 1.8, 1.7, "#10b981", "#059669", "#047857", "#a7f3d0", true);
      const dg2Col = sim.gen2Tripped ? "#ef4444" : "#10b981";
      pushBoxFaces(renderList, -3.5, -1.0,  2.1, 4.0, 1.8, 1.7, dg2Col, dg2Col, dg2Col, "#fecaca", true);
      // Rack MDC de 4 Nodos (PHY-08)
      for (let r = 0; r < 4; r++) {
        pushBoxFaces(renderList, 4.8, 1.6, -1.5 + r * 1.0, 1.2, 2.0, 0.75, "#0ea5e9", "#0284c7", "#0369a1", "#bae6fd", true);
      }
    }

    // 5C. Superestructura, Hangar, Puente 360° y Mástil Integrado de 3 Slots
    const supTop = xrayActive ? "rgba(148, 163, 184, 0.20)" : (navalLiveryActive ? "#64748b" : "#f1f5f9");
    const supSide = xrayActive ? "rgba(100, 116, 139, 0.18)" : (navalLiveryActive ? "#475569" : "#cbd5e1");
    const supFront = xrayActive ? "rgba(71, 85, 105, 0.20)" : (navalLiveryActive ? "#334155" : "#94a3b8");
    const supStroke = xrayActive ? "rgba(56, 189, 248, 0.55)" : "#1e293b";

    // Hangar G4 (-10m a -2m)
    pushBoxFaces(renderList, -6.2, 4.3, 0.0, 8.2, 3.1, 7.6, supTop, supSide, supFront, supStroke, true);
    // Superestructura Central (-2m a +11m)
    pushBoxFaces(renderList, 4.2, 4.7, 0.0, 13.0, 3.8, 6.6, supTop, supSide, supFront, supStroke, true);
    // Puente de Mando Inferior y Superior (+4m a +12.5m)
    pushBoxFaces(renderList, 8.0, 6.8, 0.0, 8.8, 2.2, 8.2, supTop, supSide, supFront, supStroke, true);
    if (!xrayActive) {
      // Ventanales Panorámicos 360° del Puente
      pushBoxFaces(renderList, 8.2, 8.4, 0.0, 7.4, 1.15, 8.4, "#0284c7", "#0369a1", "#075985", "#38bdf8", true);
    }
    pushBoxFaces(renderList, 8.0, 9.3, 0.0, 7.8, 0.5, 8.6, supTop, supSide, supFront, supStroke, true);
    // Chimenea / Funnel
    pushBoxFaces(renderList, -2.5, 7.2, 0.0, 4.2, 3.0, 3.2, supTop, supSide, supFront, supStroke, true);
    // Mástil Integrado (POS_mast_1, POS_mast_2, POS_mast_3)
    pushBoxFaces(renderList, 6.5, 13.2, 0.0, 1.8, 7.2, 1.8, supTop, supSide, supFront, supStroke, true);
    // Radomo SATCOM (mast_2)
    pushBoxFaces(renderList, 7.6, 13.4, 0.0, 1.4, 1.4, 1.4, "#f8fafc", "#e2e8f0", "#cbd5e1", "#64748b", true);
    // Sensor G1 en mast_3 (tope del mástil Y=17.3m, 45 kg)
    if (sim.g1OnMast3) {
      pushBoxFaces(renderList, 6.5, 17.3, 0.0, 1.2, 0.8, 1.2, "#10b981", "#059669", "#047857", "#d1fae5", true);
    }
    // Antena RF G5 en hangar_roof (Y=6.3m, 25 kg, estabilidad ×1.2) o en mast_3
    if (sim.g5OnHangarRoof) {
      pushBoxFaces(renderList, -7.2, 6.3, 0.0, 1.1, 0.75, 1.1, "#38bdf8", "#0284c7", "#0369a1", "#e0f2fe", true);
    } else {
      pushBoxFaces(renderList, 6.5, 17.3, 0.0, 1.2, 0.8, 1.2, "#38bdf8", "#0284c7", "#0369a1", "#e0f2fe", true);
    }

    // Proa Dual-Use: Torleta RWS (Naval) vs Cañón Monitor SAR (Civil)
    if (navalLiveryActive) {
      pushBoxFaces(renderList, 17.5, 5.4, 0.0, 2.2, 1.3, 1.8, "#475569", "#334155", "#1e293b", "#94a3b8", true);
    } else {
      pushBoxFaces(renderList, 17.5, 5.5, 0.0, 1.4, 1.6, 1.4, "#f97316", "#ea580c", "#c2410c", "#fed7aa", true);
    }

    // 5D. Contenedores de Misión en Bahías Oficiales (POS_bay1, POS_bay2) y Submarino AUV G3
    // POS_bay1 (20ft G3 Cradle)
    pushBoxFaces(renderList, -13.8, 3.9, -2.2, 6.06, 2.4, 2.44, "#d97706", "#b45309", "#92400e", "#fde68a", true);
    // POS_bay2 (10ft G5 Edge Compute & SATCOM)
    const g5Col = sim.g5Mode === "DEGRADED_70PCT_LOSS" ? "#f59e0b" : "#4f46e5";
    pushBoxFaces(renderList, -1.2, 3.9, -3.6, 2.99, 2.4, 2.44, g5Col, g5Col, g5Col, "#c7d2fe", true);
    // Submarino AUV G3 (en el agua)
    pushBoxFaces(renderList, auvX, auvY, auvZ, 4.4, 0.9, 0.9, "#facc15", "#eab308", "#ca8a04", "#fef08a", false);

    // 5E. Dron VTOL SAR G4 (Patrullando o Aterrizado en POS_flight_deck)
    let dronePos = [-22.5, 3.3, 0.0];
    if (!sim.g4Landed) {
      const orbitR = sim.simTimeS >= 480 ? 5.5 : 11.0;
      const orbitY = sim.simTimeS >= 480 ? 8.5 + Math.sin(t * 2.0) * 0.8 : 13.5 + Math.sin(t * 1.5) * 1.0;
      dronePos = [-20.0 + Math.cos(t * 0.7) * orbitR, orbitY, Math.sin(t * 0.7) * orbitR];
    } else {
      dronePos = shipToWorld(-22.5, 3.3, 0.0);
    }
    pushBoxFaces(renderList, dronePos[0], dronePos[1], dronePos[2], 2.8, 0.55, 0.8, "#f8fafc", "#e2e8f0", "#cbd5e1", "#38bdf8", false);
    pushBoxFaces(renderList, dronePos[0], dronePos[1] + 0.1, dronePos[2], 0.65, 0.18, 4.4, "#f97316", "#ea580c", "#c2410c", "#fed7aa", false);

    // 5F. Caja Ghost de Admisión D1 (si el usuario evaluó una tarjeta)
    if (sim.ghostSlot) {
      const slotCoords = {
        bay1: [-13.8, 4.1, 2.2],
        bay2: [-1.2, 4.1, 3.6],
        hangar: [-6.2, 4.4, 0.0],
        hangar_roof: [-6.2, 6.8, 0.0],
        mast_3: [6.5, 17.8, 0.0],
      };
      const sc = slotCoords[sim.ghostSlot] || slotCoords.bay2;
      const gCol = sim.ghostVerdict === "YES"
        ? "rgba(34, 197, 94, 0.55)"
        : sim.ghostVerdict === "YES_IF"
        ? "rgba(245, 158, 11, 0.55)"
        : "rgba(239, 68, 68, 0.55)";
      pushBoxFaces(renderList, sc[0], sc[1], sc[2], 3.2, 2.5, 2.5, gCol, gCol, gCol, "#ffffff", true);
    }

    // Proyectar y ordenar todos los polígonos por profundidad Z (de más lejano a más cercano)
    const projectedPolys = [];
    for (let i = 0; i < renderList.length; i++) {
      const item = renderList[i];
      const pPts = [];
      let sumZ = 0;
      let valid = true;
      for (let k = 0; k < item.pts.length; k++) {
        const pr = projectWorld(item.pts[k][0], item.pts[k][1], item.pts[k][2], w, h);
        if (!pr) { valid = false; break; }
        pPts.push(pr);
        sumZ += pr.z;
      }
      if (valid) {
        projectedPolys.push({
          pPts: pPts,
          depth: sumZ / pPts.length,
          fill: item.fill,
          stroke: item.stroke,
        });
      }
    }
    projectedPolys.sort(function (a, b) { return b.depth - a.depth; });

    // Dibujar todos los polígonos ordenados
    for (let i = 0; i < projectedPolys.length; i++) {
      const poly = projectedPolys[i];
      ctx.beginPath();
      ctx.moveTo(poly.pPts[0].x, poly.pPts[0].y);
      for (let k = 1; k < poly.pPts.length; k++) {
        ctx.lineTo(poly.pPts[k].x, poly.pPts[k].y);
      }
      ctx.closePath();
      if (poly.fill) {
        ctx.fillStyle = poly.fill;
        ctx.fill();
      }
      if (poly.stroke) {
        ctx.strokeStyle = poly.stroke;
        ctx.lineWidth = 0.85;
        ctx.stroke();
      }
    }

    // 6. DETALLES VECTORIALES DE ALTA PRECISIÓN SOBRE LA ESCENA (HELIPUERTO CAP 437, GRÚA, RADAR, LAZO TÉRMICO)
    // Círculo amarillo CAP 437 en cubierta de vuelo (X=-22.5, Y=2.82, Z=0)
    ctx.beginPath();
    let circleStarted = false;
    for (let a = 0; a <= 24; a++) {
      const ang = (a / 24) * Math.PI * 2;
      const wp = shipToWorld(-22.5 + Math.cos(ang) * 3.5, 2.82, Math.sin(ang) * 3.5);
      const pr = projectWorld(wp[0], wp[1], wp[2], w, h);
      if (pr) {
        if (!circleStarted) { ctx.moveTo(pr.x, pr.y); circleStarted = true; }
        else ctx.lineTo(pr.x, pr.y);
      }
    }
    ctx.strokeStyle = "#facc15";
    ctx.lineWidth = 2.0;
    ctx.stroke();

    // Radar X-Band rotando en mast_1 (X=6.5, Y=15.6, Z=0)
    const radAng = t * 2.8;
    const rA = shipToWorld(6.5 + Math.cos(radAng) * 2.2, 15.8, Math.sin(radAng) * 2.2);
    const rB = shipToWorld(6.5 - Math.cos(radAng) * 2.2, 15.8, -Math.sin(radAng) * 2.2);
    const prA = projectWorld(rA[0], rA[1], rA[2], w, h);
    const prB = projectWorld(rB[0], rB[1], rB[2], w, h);
    if (prA && prB) {
      ctx.beginPath();
      ctx.moveTo(prA.x, prA.y); ctx.lineTo(prB.x, prB.y);
      ctx.strokeStyle = "#f8fafc";
      ctx.lineWidth = 3.2;
      ctx.stroke();
    }

    // Grúa hidráulica articulada de estribor (X=-8.5, Z=+3.2)
    const cBase = projectWorld.apply(null, shipToWorld(-8.5, 2.8, 3.4).concat([w, h]));
    const cMid  = projectWorld.apply(null, shipToWorld(-8.5, 6.2, 3.4).concat([w, h]));
    const cTip  = projectWorld.apply(null, shipToWorld(-14.0, 7.4, 1.5).concat([w, h]));
    if (cBase && cMid && cTip) {
      ctx.beginPath();
      ctx.moveTo(cBase.x, cBase.y); ctx.lineTo(cMid.x, cMid.y); ctx.lineTo(cTip.x, cTip.y);
      ctx.strokeStyle = "#f97316";
      ctx.lineWidth = 3.5;
      ctx.stroke();
    }

    // Tuberías ISO 14726 Chilled-Water y Buses Eléctricos en modo Rayos X
    if (xrayActive) {
      const pStart = projectWorld.apply(null, shipToWorld(-22.0, 1.2, 0.0).concat([w, h]));
      const pEnd   = projectWorld.apply(null, shipToWorld(14.0, 1.2, 0.0).concat([w, h]));
      if (pStart && pEnd) {
        ctx.beginPath();
        ctx.moveTo(pStart.x, pStart.y); ctx.lineTo(pEnd.x, pEnd.y);
        ctx.strokeStyle = sim.coolingDegraded ? "#ef4444" : "#06b6d4";
        ctx.lineWidth = 3.5;
        ctx.stroke();
      }
    }

    // 7. ETIQUETAS FLOTANTES 3D DE SLOTS Y SUBSISTEMAS EN TIEMPO REAL
    const labels3D = [
      { id: sim.g1OnMast3 ? "POS_mast_3 · G1 Sensor (45kg)" : "POS_mast_3 · G5 Antenna (25kg)", pos: shipToWorld(6.5, 18.2, 0.0), col: sim.g1OnMast3 ? "#10b981" : "#38bdf8" },
      { id: "POS_bay1 · G3 Cradle (5.5t)",    pos: shipToWorld(-13.8, 5.8, -2.2), col: "#f59e0b" },
      { id: "POS_bay2 · G5 Edge (" + (sim.g5Mode === "FULL_SPECTRUM" ? "45kW" : "8kW") + ")", pos: shipToWorld(-1.2, 5.8, -3.6), col: "#818cf8" },
      { id: "G4 VTOL Drone (" + (sim.g4Landed ? "LANDED" : sim.g4Mode) + ")", pos: [dronePos[0], dronePos[1] + 1.5, dronePos[2]], col: sim.simTimeS >= 480 && !sim.g4Landed ? "#ef4444" : "#10b981" },
      { id: "G3 Seabed AUV (" + sim.g3Mode + ")", pos: [auvX, auvY + 1.6, auvZ], col: sim.g3Mode === "HOLD_ACOUSTIC" ? "#f59e0b" : "#22d3ee" },
    ];
    if (sim.g5OnHangarRoof) {
      labels3D.push({
        id: "POS_hangar_roof · G5 Antenna (25kg, ×1.2)",
        pos: shipToWorld(-7.2, 7.2, 0.0),
        col: "#38bdf8"
      });
    }
    if (xrayActive) {
      labels3D.push({
        id: "DG2 450kW (" + (sim.gen2Tripped ? "TRIPPED" : "ONLINE") + ")",
        pos: shipToWorld(-3.5, 0.8, 2.1),
        col: sim.gen2Tripped ? "#ef4444" : "#10b981"
      });
    }

    ctx.font = "bold 11px SFMono-Regular, Consolas, Menlo, monospace";
    labels3D.forEach(function (lb) {
      const pr = projectWorld(lb.pos[0], lb.pos[1], lb.pos[2], w, h);
      if (!pr) return;
      const tw = ctx.measureText(lb.id).width + 12;
      const bx = pr.x - tw * 0.5;
      const by = pr.y - 22;

      ctx.fillStyle = "rgba(7, 13, 20, 0.86)";
      ctx.fillRect(bx, by, tw, 18);
      ctx.strokeStyle = lb.col;
      ctx.lineWidth = 1.2;
      ctx.strokeRect(bx, by, tw, 18);
      ctx.fillStyle = lb.col;
      ctx.fillText(lb.id, bx + 6, by + 13);
    });
  }
  requestAnimationFrame(renderFrame);

  // ==========================================================================
  // 4. SINCRONIZACIÓN DE TICKS Y BOTONES DE CONSOLA
  // ==========================================================================
  function renderTimelineTick(tickIdx) {
    const replay = statePayload.scenario_B_replay;
    if (!replay || !replay.timeline_ticks || !replay.timeline_ticks.length) return;
    const ticks = replay.timeline_ticks;
    const clamped = Math.max(0, Math.min(ticks.length - 1, tickIdx));
    currentTickIndex = clamped;

    const slider = safeEl("replay-slider") || safeEl("scSlider");
    if (slider) {
      slider.value = slider.id === "scSlider" ? String(clamped * 20) : String(clamped);
    }

    const t = ticks[clamped];
    const simS = t.sim_time_s;
    const mins = Math.floor(simS / 60);
    const secs = simS % 60;
    if (safeEl("replay-tick-label")) {
      safeEl("replay-tick-label").textContent =
        "Tick " + String(t.tick).padStart(2, "0") + "/90 · Sim T=" + simS + "s (" + String(mins).padStart(2, "0") + ":" + String(secs).padStart(2, "0") + ")";
    }
    if (safeEl("scTimeLabel")) {
      safeEl("scTimeLabel").textContent = "T+" + simS + "s";
    }

    sim.simTimeS = simS;
    sim.vesselMode = t.vessel_mode;
    const gm = t.guest_modes || {};
    sim.g3Mode = (gm.G3 && gm.G3.mode) || "SURVEY_QUIET";
    sim.g4Mode = (gm.G4 && gm.G4.mode) || "FLIGHT_PATROL";
    sim.g5Mode = (gm.G5 && gm.G5.mode) || "FULL_SPECTRUM";

    const tdS = replay.executed_touchdown_time_s || 1095;
    sim.g4Landed = simS >= tdS;
    sim.windKn = simS >= 480 ? 22 : 14;
    sim.seaState = simS >= 480 ? 3 : 2;
    sim.speedKn = t.vessel_mode === "UAV_RECOVERY_HEADING" ? 7.0 : 3.0;
    sim.headingDeg = t.vessel_mode === "UAV_RECOVERY_HEADING" ? 270 : 90;

    if (safeEl("conning-mode")) safeEl("conning-mode").textContent = t.vessel_mode;
    if (safeEl("conning-spd")) safeEl("conning-spd").textContent = sim.speedKn.toFixed(1) + " kn";
    if (safeEl("conning-hdg")) safeEl("conning-hdg").textContent = String(sim.headingDeg).padStart(3, "0") + "°";
    if (safeEl("conning-wind")) safeEl("conning-wind").textContent = sim.windKn + " kn";
    if (safeEl("conning-sea")) safeEl("conning-sea").textContent = "SS " + sim.seaState;
    if (safeEl("conning-snap")) safeEl("conning-snap").textContent = (statePayload.snapshot_id || "SNAP_V0001") + "@T" + simS + "s";

    const alerts = [];
    if (simS >= 480 && simS < tdS) {
      alerts.push({
        level: "alarm",
        symbol: "▲",
        code: "BAM-D3-UAV",
        msg: "G4 Battery 18% (<20%) · Wind 22kn · Land before T+1200s (Touchdown @T+" + tdS + "s)",
        acked: simS > 520,
      });
    }
    if (sim.g3Mode === "HOLD_ACOUSTIC") {
      alerts.push({
        level: "warning",
        symbol: "◆",
        code: "BAM-G3-HOLD",
        msg: "G3 AUV in HOLD_ACOUSTIC station-keeping while ship turns into wind",
        acked: true,
      });
    }
    if (t.power_capacity_w <= 100000) {
      alerts.push({
        level: "alarm",
        symbol: "▲",
        code: "BAM-D4-GEN2",
        msg: "DG2 Genset Tripped (Reserve 200→100 kW) · G5 stepped down 45→8 kW (D4 ≤5s)",
        acked: simS > 760,
      });
    }
    sim.bamAlerts = alerts;
    renderBAMAlerts();

    const evText = t.events && t.events.length
      ? t.events.join(" · ")
      : "Sim T=" + simS + "s · G3:" + sim.g3Mode + " | G4:" + sim.g4Mode + " | G5:" + sim.g5Mode;
    if (safeEl("banner-event-text")) safeEl("banner-event-text").textContent = evText;

    const syncSnap = JSON.parse(JSON.stringify(statePayload.current_budget_snapshot || statePayload.initial_budget_snapshot));
    syncSnap.feasible = t.feasible;
    syncSnap.classes.power.actual = t.power_actual_w;
    syncSnap.classes.power.committed = t.power_committed_w;
    syncSnap.classes.power.degraded_capacity = t.power_capacity_w;
    syncSnap.classes.power.free = t.power_capacity_w - t.power_committed_w;
    syncSnap.classes.power.feasible = t.power_committed_w <= t.power_capacity_w;
    syncSnap.classes.cooling.actual = t.cooling_actual_w;
    syncSnap.classes.cooling.committed = t.cooling_actual_w;
    syncSnap.classes.cooling.degraded_capacity = t.cooling_capacity_w;
    syncSnap.classes.cooling.free = t.cooling_capacity_w - t.cooling_actual_w;

    const mastLim = (syncSnap.classes.mast && syncSnap.classes.mast.local_limits && syncSnap.classes.mast.local_limits.guest_slot_mast_3) || {};
    const posLim = (syncSnap.classes.positions && syncSnap.classes.positions.local_limits) || {};
    const roofList = posLim.hangar_roof || [];
    sim.g1OnMast3 = String(mastLim.owner || "").indexOf("G1") === 0;
    sim.g5OnHangarRoof = Array.isArray(roofList) && roofList.some(function (c) { return String(c).indexOf("G5") === 0; });

    renderPMSAndCooling(syncSnap);
    renderEightClassStrip(syncSnap);

    if (autoDirectorEnabled) {
      const isSurvey = statePayload.priority_profile_id !== "rescue_primary";
      const holdAckS = isSurvey ? 735 : 495;
      let nextShot = "ORBIT_HERO";
      if (simS >= 140 && simS < 460) nextShot = "SF_GOLDEN_GATE";
      else if (simS >= 460 && simS < holdAckS) nextShot = "FLIGHT_DECK_G4";
      else if (simS >= holdAckS && simS < 700) nextShot = "SUBSEA_AUV_G3";
      else if (simS >= 700 && simS < 840) nextShot = "XRAY_ENGINE_ROOM";
      else if (simS >= 840 && simS < tdS + 80) nextShot = "FLIGHT_DECK_G4";
      else if (simS >= 1460 && simS < 1640) nextShot = "MAST_SLOT_3";

      if (nextShot !== activeShotKey) {
        setCameraShotFn(nextShot);
        applyXRayFn(nextShot === "XRAY_ENGINE_ROOM");
      }
    }
  }

  // Enlazar controles interactivos
  document.querySelectorAll("[data-shot]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const shotKey = btn.getAttribute("data-shot");
      autoDirectorEnabled = false;
      if (safeEl("btn-auto-director")) safeEl("btn-auto-director").classList.remove("active");
      setCameraShotFn(shotKey);
      if (shotKey === "XRAY_ENGINE_ROOM") applyXRayFn(true);
    });
  });

  if (safeEl("btn-auto-director")) {
    safeEl("btn-auto-director").addEventListener("click", function (e) {
      autoDirectorEnabled = !autoDirectorEnabled;
      e.currentTarget.classList.toggle("active", autoDirectorEnabled);
    });
  }

  if (safeEl("btn-xray-toggle")) {
    safeEl("btn-xray-toggle").addEventListener("click", function () {
      applyXRayFn(!xrayActive);
    });
  }

  if (safeEl("btn-blueprint-cal")) {
    safeEl("btn-blueprint-cal").addEventListener("click", function (e) {
      const ov = safeEl("blueprint-overlay");
      if (!ov) return;
      const isVis = ov.classList.toggle("visible");
      e.currentTarget.classList.toggle("active", isVis);
      if (isVis) setCameraShotFn("TOP_GA_BLUEPRINT");
    });
  }

  if (safeEl("btn-livery-toggle")) {
    safeEl("btn-livery-toggle").addEventListener("click", function (e) {
      navalLiveryActive = !navalLiveryActive;
      e.currentTarget.textContent = navalLiveryActive ? "⚓ Naval / Dual-Use" : "🔬 Civil Survey";
    });
  }

  if (safeEl("btn-sfbay-toggle")) {
    safeEl("btn-sfbay-toggle").addEventListener("click", function (e) {
      sfBayVisible = !sfBayVisible;
      e.currentTarget.classList.toggle("active", sfBayVisible);
    });
  }

  if (safeEl("btn-palette-cycle")) {
    safeEl("btn-palette-cycle").addEventListener("click", function (e) {
      activePaletteIdx = (activePaletteIdx + 1) % PALETTES.length;
      const pal = PALETTES[activePaletteIdx];
      document.body.setAttribute("data-iec-palette", pal);
      const labels = { dusk: "◐ IEC Dusk", day: "☀ IEC Day", night: "☾ IEC Night" };
      e.currentTarget.textContent = labels[pal];
    });
  }

  if (safeEl("btn-quality-cycle")) {
    safeEl("btn-quality-cycle").addEventListener("click", function (e) {
      ecoBatteryMode = !ecoBatteryMode;
      resizeCanvas();
      e.currentTarget.textContent = ecoBatteryMode ? "🔋 Eco 60FPS" : "✨ Hi-DPI Retina";
    });
  }

  function getActiveProfileValue() {
    const sel = safeEl("select-priority-profile") || safeEl("profileSelect");
    return sel ? sel.value : "survey_primary";
  }

  if (safeEl("replay-slider")) {
    safeEl("replay-slider").addEventListener("input", async function (e) {
      const targetTick = parseInt(e.target.value, 10);
      if (!isPreviewSynthetic && targetTick * 20 > (statePayload.sim_time_s || 0)) {
        const live = await tryFetchBackend("POST", "/api/advance", {
          target_sim_time_s: targetTick * 20,
          from_sim_time_s: statePayload.sim_time_s,
          expected_state_version: statePayload.state_version,
          priority_profile_id: getActiveProfileValue(),
        });
        if (live && live.data) {
          statePayload = live.data;
          updateSourceBadge(live.mode, true);
        } else {
          updateSourceBadge("STALE / DESCONECTADO", false);
          return;
        }
      }
      renderTimelineTick(targetTick);
    });
  }

  if (safeEl("scSlider")) {
    safeEl("scSlider").addEventListener("change", async function (e) {
      const targetS = parseInt(e.target.value, 10);
      const targetTick = Math.round(targetS / 20);
      if (!isPreviewSynthetic && targetS > (statePayload.sim_time_s || 0)) {
        const live = await tryFetchBackend("POST", "/api/advance", {
          target_sim_time_s: targetS,
          from_sim_time_s: statePayload.sim_time_s,
          expected_state_version: statePayload.state_version,
          priority_profile_id: getActiveProfileValue(),
        });
        if (live && live.data) {
          statePayload = live.data;
          updateSourceBadge(live.mode, true);
        } else {
          updateSourceBadge("STALE / DESCONECTADO", false);
          return;
        }
      }
      renderTimelineTick(targetTick);
    });
  }

  const playBtn = safeEl("btn-play-replay") || safeEl("btnPlay");
  if (playBtn) {
    playBtn.addEventListener("click", function () {
      if (playInterval) {
        clearInterval(playInterval);
        playInterval = null;
        if (playBtn.id === "btnPlay") playBtn.textContent = "▶ Play";
        return;
      }
      if (playBtn.id === "btnPlay") playBtn.textContent = "⏸ Pause";
      playInterval = setInterval(async function () {
        if (currentTickIndex >= 90) {
          clearInterval(playInterval);
          playInterval = null;
          if (playBtn.id === "btnPlay") playBtn.textContent = "▶ Play";
          return;
        }
        const nextTick = currentTickIndex + 1;
        if (!isPreviewSynthetic) {
          const live = await tryFetchBackend("POST", "/api/advance", {
            step_s: 20,
            from_sim_time_s: statePayload.sim_time_s,
            expected_state_version: statePayload.state_version,
            priority_profile_id: getActiveProfileValue(),
          });
          if (live && live.data) {
            statePayload = live.data;
            updateSourceBadge(live.mode, true);
          } else {
            updateSourceBadge("STALE / DESCONECTADO", false);
            clearInterval(playInterval);
            playInterval = null;
            if (playBtn.id === "btnPlay") playBtn.textContent = "▶ Play";
            return;
          }
        }
        renderTimelineTick(nextTick);
      }, 1000);
    });
  }

  if (safeEl("btn-pause-replay")) {
    safeEl("btn-pause-replay").addEventListener("click", function () {
      if (playInterval) {
        clearInterval(playInterval);
        playInterval = null;
      }
    });
  }

  const resetBtn = safeEl("btn-reset-session") || safeEl("btnReset");
  if (resetBtn) {
    resetBtn.addEventListener("click", async function () {
      if (playInterval) {
        clearInterval(playInterval);
        playInterval = null;
      }
      sim.ghostSlot = null;
      sim.coolingDegraded = false;
      applyXRayFn(false);
      setCameraShotFn("ORBIT_HERO");
      const prof = getActiveProfileValue();
      if (!isPreviewSynthetic) {
        const live = await tryFetchBackend("POST", "/api/reset", { priority_profile_id: prof });
        if (live && live.data) {
          statePayload = live.data;
          updateSourceBadge(live.mode, true);
          renderTimelineTick(0);
        } else {
          updateSourceBadge("STALE / DESCONECTADO", false);
        }
      } else {
        statePayload = buildDeterministicState(prof);
        renderTimelineTick(0);
      }
    });
  }

  const profSelect = safeEl("select-priority-profile") || safeEl("profileSelect");
  if (profSelect) {
    profSelect.addEventListener("change", async function (e) {
      const prof = e.target.value;
      if (!isPreviewSynthetic) {
        const live = await tryFetchBackend("POST", "/api/profile", { priority_profile_id: prof });
        if (live && live.data) {
          statePayload = live.data;
          updateSourceBadge(live.mode, true);
          renderTimelineTick(currentTickIndex);
        } else {
          updateSourceBadge("STALE / DESCONECTADO", false);
        }
      } else {
        statePayload = buildDeterministicState(prof);
        renderTimelineTick(currentTickIndex);
      }
    });
  }

  async function triggerPerturbation(pType) {
    if (!isPreviewSynthetic) {
      const live = await tryFetchBackend("POST", "/api/perturb", {
        perturbation_type: pType,
        priority_profile_id: getActiveProfileValue(),
      });
      if (!live || !live.data) {
        updateSourceBadge("STALE / DESCONECTADO", false);
        return;
      }
      statePayload = live.data;
      updateSourceBadge(live.mode, true);
      const snap = live.data.current_budget_snapshot || (live.data.result && (live.data.result.final_budget_snapshot || live.data.result.budget_snapshot));
      if (snap) {
        renderPMSAndCooling(snap);
        renderEightClassStrip(snap);
      }
    }
    if (pType === "GENERATOR_LOSS_100KW") {
      sim.gen2Tripped = true;
      sim.g5Mode = "DEGRADED_70PCT_LOSS";
      applyXRayFn(true);
      setCameraShotFn("XRAY_ENGINE_ROOM");
      sim.bamAlerts.unshift({
        level: "alarm",
        symbol: "▲",
        code: "LIVE-D4-GEN",
        msg: "Generator Loss 200→100 kW · G5 Shed 45→8 kW",
        acked: false,
      });
      renderBAMAlerts();
    } else if (pType === "COOLING_LOSS_30C_40PCT") {
      sim.coolingDegraded = true;
      if (safeEl("conning-temp")) safeEl("conning-temp").textContent = "30.0 °C";
      applyXRayFn(true);
      setCameraShotFn("XRAY_ENGINE_ROOM");
      sim.bamAlerts.unshift({
        level: "alarm",
        symbol: "▲",
        code: "LIVE-D4-COOL",
        msg: "Seawater 30°C + 40% Loop Loss (Cap 72 kW) · G5 Shed 45→8 kW",
        acked: false,
      });
      renderBAMAlerts();
    } else if (pType === "URGENT_HOLD" || pType === "URGENT_STOP") {
      sim.g3Mode = "HOLD_ACOUSTIC";
      setCameraShotFn("SUBSEA_AUV_G3");
      sim.bamAlerts.unshift({
        level: "warning",
        symbol: "◆",
        code: "LIVE-" + pType,
        msg: pType + " executed on G3 in ≤200 ms",
        acked: false,
      });
      renderBAMAlerts();
    } else {
      sim.bamAlerts.unshift({
        level: "warning",
        symbol: "◆",
        code: "LIVE-" + pType,
        msg: pType + " handled by Resource Governor within D4 envelope",
        acked: false,
      });
      renderBAMAlerts();
    }
  }

  document.querySelectorAll("[data-perturb]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      triggerPerturbation(btn.getAttribute("data-perturb"));
    });
  });
  if (safeEl("btnGenLoss")) safeEl("btnGenLoss").addEventListener("click", function () { triggerPerturbation("GENERATOR_LOSS_100KW"); });
  if (safeEl("btnCoolLoss")) safeEl("btnCoolLoss").addEventListener("click", function () { triggerPerturbation("COOLING_LOSS_30C_40PCT"); });
  if (safeEl("btnHoldG3")) safeEl("btnHoldG3").addEventListener("click", function () { triggerPerturbation("URGENT_HOLD"); });
  if (safeEl("btnStopG3")) safeEl("btnStopG3").addEventListener("click", function () { triggerPerturbation("URGENT_STOP"); });

  if (safeEl("btn-ack-all")) {
    safeEl("btn-ack-all").addEventListener("click", function () {
      sim.bamAlerts.forEach(function (a) { a.acked = true; });
      renderBAMAlerts();
    });
  }

  if (safeEl("btn-run-admission")) {
    safeEl("btn-run-admission").addEventListener("click", async function () {
      const installedIds = safeEl("admit-installed")
        .value.split(",")
        .map(function (s) { return s.trim().toUpperCase(); })
        .filter(Boolean);
      const candId = safeEl("admit-candidate").value;
      const massRaw = safeEl("admit-mass").value.trim();
      const slotVal = safeEl("admit-slot").value.trim();
      const atPort = safeEl("admit-at-port").checked;
      const overrides = {};
      if (massRaw !== "") overrides.mass_kg = Number(massRaw);
      if (slotVal !== "") overrides.preferred_position = slotVal;

      if (!isPreviewSynthetic) {
        const live = await tryFetchBackend("POST", "/api/admit_live", {
          priority_profile_id: getActiveProfileValue(),
          installed_guest_ids: installedIds,
          candidate_guest_id: candId,
          at_port: atPort,
          overrides: overrides,
        });
        if (!live || !live.data) {
          updateSourceBadge("STALE / DESCONECTADO", false);
          if (safeEl("admission-verdict-box")) {
            safeEl("admission-verdict-box").textContent = "STALE / DESCONECTADO: Live admission requires authoritative backend connection.";
          }
          return;
        }
        updateSourceBadge(live.mode, true);
        const dec = live.data.decision || {};
        const verdict = dec.verdict || live.data.status || "ERROR";
        const chosenSlot = slotVal || (candId === "G1" ? "mast_3" : candId === "G4" ? "hangar" : candId === "G3" ? "bay1" : "bay2");
        sim.ghostSlot = chosenSlot;
        sim.ghostVerdict = verdict;
        sim.ghostCandidate = candId;
        const witnessLines = (dec.binding_witnesses || dec.binding_constraints || [])
          .map(function (w) { return "• [" + w.resource_class + "] " + (w.detail || w.constraint_id) + " (" + w.demand + "/" + w.capacity + " " + w.unit + ")"; })
          .join("\n");
        const condLines = (dec.conditions || []).map(function (c) { return "• Cond: " + c; }).join("\n");
        if (safeEl("admission-verdict-box")) {
          safeEl("admission-verdict-box").textContent =
            "VERDICT: " + verdict + " (" + candId + ", Total Mass=" + (live.data.candidate_total_mass_kg || "?") + " kg)\n" +
            condLines + (witnessLines ? "\n" + witnessLines : "");
        }
        return;
      }

      const massMap = { G1: 45, G2: 3500, G3: 5500, G4: 990, G5: 3025, G6: 420 };
      const baseMass = installedIds.reduce(function (a, g) { return a + (massMap[g] || 0); }, 0);
      const candMass = massRaw ? Number(massRaw) : massMap[candId] || 500;
      const totalMass = baseMass + candMass;

      let verdict = "YES";
      let detail = "All 8 resource classes feasible across 14-day mission envelope.";
      if (totalMass > 10000) {
        verdict = "NO";
        detail = "Mass violation: " + totalMass + " kg > 10,000 kg limit (Organizer Errata G4=990 kg).";
      } else if (!atPort && ["G1", "G2", "G5"].indexOf(candId) !== -1) {
        verdict = "NOT_NOW";
        detail = candId + " requires shore crane at port (cannot install at sea).";
      } else if (candId === "G5" && installedIds.indexOf("G1") !== -1) {
        verdict = "YES_IF";
        detail = "Mount G5 25kg antenna on hangar_roof (×1.2 stability) & verify G4 drone clearance.";
      }

      const chosenSlot = slotVal || (candId === "G1" ? "mast_3" : candId === "G4" ? "hangar" : candId === "G3" ? "bay1" : "bay2");
      sim.ghostSlot = chosenSlot;
      sim.ghostVerdict = verdict;
      sim.ghostCandidate = candId;
      if (safeEl("admission-verdict-box")) {
        safeEl("admission-verdict-box").textContent =
          "VERDICT: " + verdict + " (" + candId + ", Total Mass=" + totalMass + " kg)\n• " + detail;
      }
    });
  }

  document.querySelectorAll("[data-crew]").forEach(function (btn) {
    btn.addEventListener("click", async function () {
      document.querySelectorAll("[data-crew]").forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      const c = btn.getAttribute("data-crew");
      if (!isPreviewSynthetic) {
        const live = await tryFetchBackend("POST", "/api/crew", { crew_count: parseInt(c, 10) });
        if (live && live.data) {
          statePayload = live.data;
          updateSourceBadge(live.mode, true);
        } else {
          updateSourceBadge("STALE / DESCONECTADO", false);
        }
      }
      const map = { "14": "14 (FULL)", "6": "6 (REDUCED)", "0": "0 (UNCREWED)" };
      if (safeEl("conning-crew")) safeEl("conning-crew").textContent = map[c];
    });
  });

  if (safeEl("btn-hydro-exaggerate")) {
    safeEl("btn-hydro-exaggerate").addEventListener("click", function (e) {
      exaggerateHydro = !exaggerateHydro;
      e.currentTarget.classList.toggle("active", exaggerateHydro);
      e.currentTarget.textContent = exaggerateHydro ? "Hydro ×20 (VISUAL)" : "Hydro ×1 (Real)";
    });
  }

  let mediaRecorder = null;
  let recordedChunks = [];
  if (safeEl("btn-record-video")) {
    safeEl("btn-record-video").addEventListener("click", function (e) {
      const btn = e.currentTarget;
      if (mediaRecorder && mediaRecorder.state === "recording") {
        mediaRecorder.stop();
        btn.textContent = "⏺ Rec Video";
        btn.classList.remove("danger");
        return;
      }
      if (!canvas.captureStream || typeof MediaRecorder === "undefined") return;
      recordedChunks = [];
      const stream = canvas.captureStream(30);
      mediaRecorder = new MediaRecorder(stream);
      mediaRecorder.ondataavailable = function (ev) {
        if (ev.data && ev.data.size > 0) recordedChunks.push(ev.data);
      };
      mediaRecorder.onstop = function () {
        const blob = new Blob(recordedChunks, { type: "video/webm" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "fincantieri_sdv_digital_twin.webm";
        a.click();
      };
      mediaRecorder.start();
      btn.textContent = "⏹ Stop & Save";
      btn.classList.add("danger");
    });
  }

  // Inicialización conectada al backend autoritativo o preview_synthetic explícito
  renderTimelineTick(0);
  if (isPreviewSynthetic) {
    updateSourceBadge("PREVIEW SYNTHETIC FIXTURE", false);
  } else {
    tryFetchBackend("GET", "/api/state?priority_profile=survey_primary").then(function (live) {
      if (live && live.data) {
        statePayload = live.data;
        updateSourceBadge(live.mode, true);
        renderTimelineTick(currentTickIndex);
      } else {
        updateSourceBadge("STALE / DESCONECTADO", false);
      }
    });
  }
})();
