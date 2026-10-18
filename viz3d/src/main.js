/**
 * viz3d/src/main.js
 * Orquestador principal del Gemelo Digital 3D a escala 1:1 y Consola de Puente IEC 62288 / BAM
 * para el Software Defined Vessel (Fincantieri Maritime Dual-Use Hackathon).
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GovernorClient } from "./api/governorClient.js";
import {
  createOceanSystem,
  createSanFranciscoBayEnvironment,
  computeVessel6DOF,
} from "./scene/oceanAndBay.js";
import { buildProceduralVessel } from "./vessel/buildProceduralVessel.js";
import { CameraDirector, CanvasVideoRecorder } from "./scene/cameraDirector.js";

// ============================================================================
// 1. INICIALIZACIÓN DEL RENDERER (MODO ECO-BATTERY 60 FPS PARA MACBOOK PRO M1 PRO)
// ============================================================================
const canvas = document.getElementById("viewport-canvas");
const renderer = new THREE.WebGLRenderer({
  canvas,
  antialias: true,
  powerPreference: "high-performance",
  preserveDrawingBuffer: false,
});

let ecoBatteryMode = true; // Por defecto activado (DPR limitado a 1.25 para no drenar batería del M1 Pro)
function applyRendererPixelRatio() {
  const maxDpr = ecoBatteryMode ? 1.25 : Math.min(window.devicePixelRatio || 1, 2.0);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, maxDpr));
  renderer.setSize(window.innerWidth, window.innerHeight);
}
applyRendererPixelRatio();
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.08;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b192c);
scene.fog = new THREE.FogExp2(0x162b44, 0.0014);

const camera = new THREE.PerspectiveCamera(44, window.innerWidth / window.innerHeight, 0.25, 1600);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.06;
controls.maxDistance = 260;
controls.minDistance = 4;

const director = new CameraDirector(camera, controls);
director.setShot("ORBIT_HERO", false);

// Al arrastrar manualmente con el ratón o trackpad, pausamos el Auto-Director para no pelear con el usuario
canvas.addEventListener("pointerdown", () => {
  if (director.autoDirectorEnabled) {
    director.autoDirectorEnabled = false;
    const btn = document.getElementById("btn-auto-director");
    if (btn) btn.classList.remove("active");
  }
});

// ============================================================================
// 2. ILUMINACIÓN SOLAR FÍSICA, OCÉANO JONSWAP, BAHÍA DE SF Y BUQUE 60M
// ============================================================================
const hemiLight = new THREE.HemisphereLight(0x93c5fd, 0x0f172a, 1.15);
scene.add(hemiLight);

const sunLight = new THREE.DirectionalLight(0xfff1d6, 2.6);
sunLight.position.set(85, 62, -95);
scene.add(sunLight);

const fillLight = new THREE.DirectionalLight(0x38bdf8, 0.75);
fillLight.position.set(-65, 28, 75);
scene.add(fillLight);

const oceanSystem = createOceanSystem(scene);
const sfBayGroup = createSanFranciscoBayEnvironment(scene);
const vessel = buildProceduralVessel();
scene.add(vessel.root);

// ============================================================================
// 3. ESTADO REACTIVO DE LA CONSOLA Y CONEXIÓN CON EL BACKEND (:8765)
// ============================================================================
const client = new GovernorClient();
const videoRecorder = new CanvasVideoRecorder(canvas);

let statePayload = null;
let currentTickIndex = 0;
let playInterval = null;
let navalLiveryActive = true;
let xrayActive = false;
let exaggerateHydro = false;
let currentCrewRegime = 14;
let activePaletteIdx = 0;
const PALETTES = ["dusk", "day", "night"];

// Estado operativo derivado del tick o perturbación actual para animar el barco 3D
const visualSimState = {
  simTimeS: 0,
  vesselMode: "QUIET_SURVEY",
  speedKn: 3.0,
  headingDeg: 90.0,
  windKn: 14,
  seaState: 2,
  seawaterTempC: 24.0,
  powerActualW: 56000,
  powerCommittedW: 80000,
  powerCapacityW: 200000,
  coolingActualW: 54800,
  coolingCapacityW: 133333,
  gen2Tripped: false,
  coolingDegraded: false,
  emconSilence: false,
  g1Active: false,
  g2Active: false,
  g3Mode: "SURVEY_QUIET",
  g4Mode: "FLIGHT_PATROL",
  g4BatteryPct: 54.0,
  g4Landed: false,
  g5Mode: "FULL_SPECTRUM",
  g5AntennaOnRoof: false,
  g6Active: false,
  guestMassKg: 9515,
  stabilityKgEq: 9763,
  bamAlerts: [],
};

// ============================================================================
// 4. ACTUALIZACIÓN DE LA CONSOLA DE PUENTE (PMS, BAM, 8 CLASES, CONNING)
// ============================================================================
function updateSourceBadge(mode, staleInfo = null) {
  const badge = document.getElementById("badge-bridge-source");
  const isStale = mode === "STALE_DISCONNECTED";
  if (badge) {
    if (mode && mode.startsWith("LIVE")) {
      badge.textContent = `● ${mode} (:8765)`;
      badge.classList.remove("offline");
    } else if (mode === "PREVIEW_SYNTHETIC") {
      badge.textContent = "◉ PREVIEW SYNTHETIC (?mode=preview_synthetic)";
      badge.classList.add("offline");
    } else {
      const info = staleInfo || client.getStaleInfo();
      badge.textContent = `⚠ STALE / DESCONECTADO (Last v${info.lastVersion} @ T+${info.lastSimTimeS}s)`;
      badge.classList.add("offline");
    }
  }
  document.querySelectorAll("[data-perturb], #btn-run-admission, #btn-play-replay, #btn-reset-session, #replay-slider, #select-priority-profile, [data-crew]").forEach((el) => {
    el.disabled = isStale;
  });
}

function renderBAMAlerts() {
  const listEl = document.getElementById("bam-alert-list");
  if (!listEl) return;

  if (visualSimState.bamAlerts.length === 0) {
    listEl.innerHTML = `
      <div class="bam-item">
        <span>● NORMAL · All 8 physical resource classes within envelope</span>
        <span style="color: var(--bam-ok)">OK</span>
      </div>
    `;
    return;
  }

  listEl.innerHTML = visualSimState.bamAlerts
    .map(
      (a, idx) => `
      <div class="bam-item ${a.level} ${a.acked ? "" : "unacked"}" data-alert-idx="${idx}">
        <div>
          <strong>${a.symbol} ${a.code}</strong> · ${a.msg}
        </div>
        <span style="font-weight:700;">${a.acked ? "ACKED" : "ACTIVE"}</span>
      </div>
    `
    )
    .join("");
}

function renderPMSAndCooling(snapshot) {
  const pwr = snapshot.classes.power;
  const cool = snapshot.classes.cooling;

  const dg2Box = document.getElementById("gen-box-2");
  const dg2Status = document.getElementById("gen-box-2-status");
  const isGenDown = pwr.degraded_capacity <= 100000;
  visualSimState.gen2Tripped = isGenDown;

  if (dg2Box && dg2Status) {
    dg2Box.className = isGenDown ? "genset-box tripped" : "genset-box";
    dg2Status.textContent = isGenDown ? "TRIPPED (FAULT)" : "ONLINE (STBD)";
  }

  document.getElementById("pms-reserve-label").textContent = `${(pwr.degraded_capacity / 1000).toFixed(0)} kW RES`;
  document.getElementById("pwr-actual-txt").textContent = `${(pwr.actual / 1000).toFixed(1)} kW`;
  document.getElementById("pwr-comm-txt").textContent = `${(pwr.committed / 1000).toFixed(1)} kW`;

  const pwrPct = Math.min(100, Math.round((pwr.committed / Math.max(1, pwr.degraded_capacity)) * 100));
  const pwrFill = document.getElementById("pwr-meter-fill");
  pwrFill.style.width = `${pwrPct}%`;
  pwrFill.className = `meter-fill ${pwrPct > 95 ? "danger" : pwrPct > 80 ? "warn" : ""}`;

  // Líneas A y B
  const g5Kw = visualSimState.g5Mode === "FULL_SPECTRUM" ? 45.0 : 8.0;
  const g4Kw = visualSimState.g4Mode === "LAUNCH_RECOVERY_OPTICS" ? 35.0 : visualSimState.g4Mode === "FLIGHT_PATROL" ? 10.0 : 2.0;
  document.getElementById("line-a-val").textContent = `Actual: 2.2 kW | Firm: 26.2 kW`;
  document.getElementById("line-b-val").textContent = `Actual: ${(g4Kw + g5Kw).toFixed(1)} kW | Cap: 150 kW`;

  // Lazo de agua helada ISO 14726
  document.getElementById("cool-cap-label").textContent = `${(cool.degraded_capacity / 1000).toFixed(1)} kW CAP`;
  document.getElementById("cool-actual-txt").textContent = `${(cool.actual / 1000).toFixed(1)} kW`;
  const coolPct = Math.min(100, Math.round((cool.actual / Math.max(1, cool.degraded_capacity)) * 100));
  const coolFill = document.getElementById("cool-meter-fill");
  coolFill.style.width = `${coolPct}%`;
  coolFill.className = `meter-fill ${coolPct > 95 ? "danger" : coolPct > 80 ? "warn" : ""}`;
}

function renderTopologySummary(snapshot) {
  const pos = snapshot.classes.positions.local_limits || {};
  const mast = snapshot.classes.mast.local_limits || {};
  const mass = snapshot.classes.mass_stability.local_limits || {};
  const box = document.getElementById("topology-summary-box");

  const mastOwner = mast.guest_slot_mast_3?.owner || "G1 (G1_MAST_UNIT)";
  const mastMass = mast.guest_slot_mast_3?.mass_kg ?? 45;
  const bay1Occs = Array.isArray(pos.bay1_20ft) ? pos.bay1_20ft : (pos.bay1?.occupants || ["G3:G3_AUV_CRADLE"]);
  const bay1Mass = pos.bay1_total_mass_kg ?? pos.bay1?.mass_kg ?? 5500;
  const bay2Occs = Array.isArray(pos.bay2_10ft) ? pos.bay2_10ft : (pos.bay2?.occupants || ["G5:G5_COMPUTE_CONTAINER"]);
  const bay2Mass = pos.bay2_total_mass_kg ?? pos.bay2?.mass_kg ?? 3000;
  const hangarOccs = Array.isArray(pos.hangar) ? pos.hangar : (pos.hangar?.occupants || ["G4:G4_HANGAR_STATION"]);
  const roofOccs = Array.isArray(pos.hangar_roof) ? pos.hangar_roof : (pos.hangar_roof?.occupants || ["G5:G5_MAST_ANTENNA"]);

  // Sincronizar posición 3D real de G1 en mast_3 y Antena G5 en hangar_roof vs mast_3 (ALIGN-T37)
  const g1OnMast3 = String(mastOwner).includes("G1");
  vessel.animatedParts.g1Actor.visible = g1OnMast3;
  const g5OnRoof = roofOccs.some((o) => String(o).includes("G5")) || g1OnMast3;
  visualSimState.g5AntennaOnRoof = g5OnRoof;
  if (g5OnRoof) {
    vessel.animatedParts.g5Antenna.position.set(-7.2, 6.3, 0.0);
  } else {
    vessel.animatedParts.g5Antenna.position.set(6.5, 18.35, 0.0);
  }

  if (!box) return;
  const actualMassKg = mass.mass_margin_kg?.actual_kg ?? snapshot.classes.mass_stability.actual ?? 9535;
  const actualStabEq = mass.stability_equivalent_kg?.actual_kg_eq ?? mass.stability_equivalent_kg_eq ?? 9988;
  box.innerHTML = `
    <div><strong>POS_mast_3 (×3.0):</strong> <span style="color:var(--accent-nav)">${mastOwner}</span> (${mastMass}/60 kg)</div>
    <div><strong>POS_bay1 (20ft ≤8t):</strong> ${bay1Occs.join(", ") || "FREE"} (${bay1Mass}/8000 kg)</div>
    <div><strong>POS_bay2 (10ft ≤4t):</strong> ${bay2Occs.join(", ") || "FREE"} (${bay2Mass}/4000 kg)</div>
    <div><strong>POS_hangar (≤1t):</strong> ${hangarOccs.join(", ") || "G4_HANGAR (850 kg)"} | <strong>Roof (×1.2):</strong> <span style="color:var(--accent-nav)">${roofOccs.join(", ") || "CLEAR"}</span></div>
    <div><strong>Mass Margin:</strong> ${actualMassKg}/10,000 kg | <strong>Stab:</strong> ${actualStabEq}/12,000 kg-eq</div>
  `;
}

function renderEightClassStrip(snapshot) {
  const container = document.getElementById("eight-classes-container");
  if (!container) return;

  const labels = [
    ["power", "1. Power", "kW", 1000],
    ["cooling", "2. Cooling", "kW", 1000],
    ["positions", "3. Positions", "slots", 1],
    ["mast", "4. Mast Slot", "slot", 1],
    ["mass_stability", "5. Mass/Stab", "t", 1000],
    ["data_links", "6. Data LAN", "Gbps", 1e9],
    ["compute", "7. Compute", "cores", 1],
    ["time", "8. Deadlines", "ms", 1],
  ];

  container.innerHTML = labels
    .map(([key, title, unit, div]) => {
      const c = snapshot.classes[key] || snapshot.classes.data || { degraded_capacity: 1, committed: 0, actual: 0, feasible: true };
      const cap = (c.degraded_capacity / div).toFixed(div >= 1000 ? 1 : 0);
      const comm = (c.committed / div).toFixed(div >= 1000 ? 1 : 0);
      const act = (c.actual / div).toFixed(div >= 1000 ? 1 : 0);
      const pct = Math.min(100, Math.round((c.committed / Math.max(1, c.degraded_capacity)) * 100));
      return `
        <div class="res-gauge-card ${c.feasible ? "" : "infeasible"}">
          <div class="res-gauge-header">
            <span>${title}</span>
            <span style="color:${c.feasible ? "var(--bam-ok)" : "var(--bam-alarm)"}">${c.feasible ? "OK" : "BOUND"}</span>
          </div>
          <div class="res-gauge-values">
            Firm: <strong>${comm}</strong> / ${cap} ${unit}<br/>
            Actual: ${act} ${unit}
          </div>
          <div class="meter-bar">
            <div class="meter-fill ${!c.feasible ? "danger" : pct > 80 ? "warn" : ""}" style="width:${pct}%"></div>
          </div>
        </div>
      `;
    })
    .join("");
}

function renderTimelineTick(tickIdx) {
  if (!statePayload || !statePayload.scenario_B_replay) return;
  const replay = statePayload.scenario_B_replay;
  const ticks = replay.timeline_ticks;
  const clamped = Math.max(0, Math.min(ticks.length - 1, tickIdx));
  currentTickIndex = clamped;

  const slider = document.getElementById("replay-slider");
  if (slider) slider.value = String(clamped);

  const t = ticks[clamped];
  const simS = t.sim_time_s;
  const mins = Math.floor(simS / 60);
  const secs = simS % 60;
  document.getElementById("replay-tick-label").textContent =
    `Tick ${String(t.tick).padStart(2, "0")}/90 · Sim T=${simS}s (${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")})`;

  // Actualizar estado visual del buque a partir del tick real del motor
  visualSimState.simTimeS = simS;
  visualSimState.vesselMode = t.vessel_mode;
  visualSimState.powerActualW = t.power_actual_w;
  visualSimState.powerCommittedW = t.power_committed_w;
  visualSimState.powerCapacityW = t.power_capacity_w;
  visualSimState.coolingActualW = t.cooling_actual_w;
  visualSimState.coolingCapacityW = t.cooling_capacity_w;

  const gm = t.guest_modes || {};
  visualSimState.g3Mode = gm.G3?.mode || "SURVEY_QUIET";
  visualSimState.g4Mode = gm.G4?.mode || "FLIGHT_PATROL";
  visualSimState.g5Mode = gm.G5?.mode || "FULL_SPECTRUM";

  const tdS = replay.executed_touchdown_time_s || 1095;
  visualSimState.g4Landed = simS >= tdS;
  visualSimState.g4BatteryPct = simS < 480 ? 54 - (simS / 480) * 34 : simS < tdS ? 18.0 : Math.min(100, 20 + ((simS - tdS) / 1200) * 80);

  // Viento, estado de mar y rumbo según los hitos reales del Escenario B
  visualSimState.windKn = simS >= 480 ? 22 : 14;
  visualSimState.seaState = simS >= 480 ? 3 : 2;
  visualSimState.speedKn = t.vessel_mode === "UAV_RECOVERY_HEADING" ? 7.0 : 3.0;
  visualSimState.headingDeg = t.vessel_mode === "UAV_RECOVERY_HEADING" ? 270.0 : 90.0;

  // Conning strip
  document.getElementById("conning-mode").textContent = t.vessel_mode;
  document.getElementById("conning-spd").textContent = `${visualSimState.speedKn.toFixed(1)} kn`;
  document.getElementById("conning-hdg").textContent = `${visualSimState.headingDeg.toFixed(0).padStart(3, "0")}°`;
  document.getElementById("conning-wind").textContent = `${visualSimState.windKn} kn`;
  document.getElementById("conning-sea").textContent = `SS ${visualSimState.seaState}`;
  document.getElementById("conning-snap").textContent = `${statePayload.snapshot_id}@T${simS}s`;

  // Construir lista de alertas BAM según la cronología real
  const alerts = [];
  if (simS >= 480 && simS < tdS) {
    alerts.push({
      level: "alarm",
      symbol: "▲",
      code: "BAM-D3-UAV",
      msg: `G4 Battery 18% (<20%) · Wind 22kn · Land before T+1200s (Touchdown planned @T+${tdS}s)`,
      acked: simS > 520,
    });
  }
  if (visualSimState.g3Mode === "HOLD_ACOUSTIC") {
    alerts.push({
      level: "warning",
      symbol: "◆",
      code: "BAM-G3-HOLD",
      msg: "G3 AUV in HOLD_ACOUSTIC station-keeping while ship maneuvers into wind",
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
  visualSimState.bamAlerts = alerts;
  renderBAMAlerts();

  // Banner central flotante
  const evText = t.events && t.events.length
    ? t.events.join(" · ")
    : `Sim T=${simS}s · G3:${visualSimState.g3Mode} | G4:${visualSimState.g4Mode} | G5:${visualSimState.g5Mode}`;
  document.getElementById("banner-event-text").textContent = evText;

  // Sincronizar snapshot de las 8 clases con el tick actual
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
  syncSnap.classes.cooling.feasible = t.cooling_actual_w <= t.cooling_capacity_w;

  renderPMSAndCooling(syncSnap);
  renderTopologySummary(syncSnap);
  renderEightClassStrip(syncSnap);

  // Si en el minuto 12 cae el generador y el Auto-Director está activo, mostrar automáticamente Rayos X
  const newShot = director.evaluateScenarioBAutoShot(simS, statePayload.priority_profile_id);
  if (newShot) {
    highlightShotButton(newShot);
    if (newShot === "XRAY_ENGINE_ROOM" && !xrayActive) {
      setXRayMode(true);
    } else if (newShot !== "XRAY_ENGINE_ROOM" && xrayActive && director.autoDirectorEnabled) {
      setXRayMode(false);
    }
  }
}

// ============================================================================
// 5. MODOS VISUALES 3D (RAYOS X, LIBREA DUAL-USE, PALETAS IEC 62288)
// ============================================================================
function setXRayMode(enabled) {
  xrayActive = enabled;
  vessel.xrayGroup.visible = enabled;
  vessel.materials.matHullUpper.transparent = enabled;
  vessel.materials.matHullUpper.opacity = enabled ? 0.24 : 1.0;
  vessel.materials.matHullLower.transparent = enabled;
  vessel.materials.matHullLower.opacity = enabled ? 0.28 : 1.0;
  vessel.materials.matSuperstructure.transparent = enabled;
  vessel.materials.matSuperstructure.opacity = enabled ? 0.32 : 1.0;

  const btn = document.getElementById("btn-xray-toggle");
  if (btn) btn.classList.toggle("active", enabled);
}

function toggleDualUseLivery() {
  navalLiveryActive = !navalLiveryActive;
  const btn = document.getElementById("btn-livery-toggle");
  if (navalLiveryActive) {
    vessel.materials.matHullUpper.color.setHex(0x46525e); // Gris Naval Fincantieri
    vessel.materials.matSuperstructure.color.setHex(0x566370);
    vessel.animatedParts.bowMountNaval.visible = true;
    vessel.animatedParts.bowMountCivil.visible = false;
    if (btn) btn.textContent = "⚓ Naval / Dual-Use";
  } else {
    vessel.materials.matHullUpper.color.setHex(0x1d4ed8); // Azul Oceanográfico Civil
    vessel.materials.matSuperstructure.color.setHex(0xf1f5f9); // Superestructura Blanca Civil
    vessel.animatedParts.bowMountNaval.visible = false;
    vessel.animatedParts.bowMountCivil.visible = true;
    if (btn) btn.textContent = "🔬 Civil Survey";
  }
}

function highlightShotButton(shotKey) {
  document.querySelectorAll("[data-shot]").forEach((b) => {
    b.classList.toggle("active", b.getAttribute("data-shot") === shotKey);
  });
}

// ============================================================================
// 6. EVENT LISTENERS DE LA CONSOLA INTERACTIVA
// ============================================================================
document.querySelectorAll("[data-shot]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const shotKey = btn.getAttribute("data-shot");
    director.autoDirectorEnabled = false;
    document.getElementById("btn-auto-director").classList.remove("active");
    highlightShotButton(shotKey);
    director.setShot(shotKey, true);
    if (shotKey === "XRAY_ENGINE_ROOM") {
      setXRayMode(true);
    }
  });
});

document.getElementById("btn-auto-director").addEventListener("click", (e) => {
  director.autoDirectorEnabled = !director.autoDirectorEnabled;
  e.currentTarget.classList.toggle("active", director.autoDirectorEnabled);
});

document.getElementById("btn-xray-toggle").addEventListener("click", () => {
  setXRayMode(!xrayActive);
});

document.getElementById("btn-blueprint-cal").addEventListener("click", (e) => {
  const ov = document.getElementById("blueprint-overlay");
  const isVis = ov.classList.toggle("visible");
  e.currentTarget.classList.toggle("active", isVis);
  if (isVis) {
    director.setShot("TOP_GA_BLUEPRINT", true);
    highlightShotButton("TOP_GA_BLUEPRINT");
  }
});

document.getElementById("btn-livery-toggle").addEventListener("click", toggleDualUseLivery);

document.getElementById("btn-sfbay-toggle").addEventListener("click", (e) => {
  sfBayGroup.visible = !sfBayGroup.visible;
  e.currentTarget.classList.toggle("active", sfBayGroup.visible);
});

document.getElementById("btn-palette-cycle").addEventListener("click", (e) => {
  activePaletteIdx = (activePaletteIdx + 1) % PALETTES.length;
  const pal = PALETTES[activePaletteIdx];
  document.body.setAttribute("data-iec-palette", pal);
  const labels = { dusk: "◐ IEC Dusk", day: "☀ IEC Day", night: "☾ IEC Night" };
  e.currentTarget.textContent = labels[pal];

  if (pal === "day") {
    scene.background.setHex(0x8bb8e8);
    scene.fog.color.setHex(0x9ec5eb);
    oceanSystem.oceanUniforms.uSkyColor.value.setHex(0x7fb2e5);
    renderer.toneMappingExposure = 1.25;
  } else if (pal === "night") {
    scene.background.setHex(0x02060d);
    scene.fog.color.setHex(0x030811);
    oceanSystem.oceanUniforms.uSkyColor.value.setHex(0x0b192c);
    renderer.toneMappingExposure = 0.72;
  } else {
    scene.background.setHex(0x0b192c);
    scene.fog.color.setHex(0x162b44);
    oceanSystem.oceanUniforms.uSkyColor.value.setHex(0x4b79a1);
    renderer.toneMappingExposure = 1.08;
  }
});

document.getElementById("btn-quality-cycle").addEventListener("click", (e) => {
  ecoBatteryMode = !ecoBatteryMode;
  applyRendererPixelRatio();
  e.currentTarget.textContent = ecoBatteryMode ? "🔋 Eco 60FPS" : "✨ Hi-DPI Retina";
  e.currentTarget.classList.toggle("active", !ecoBatteryMode);
});

document.getElementById("btn-record-video").addEventListener("click", (e) => {
  const btn = e.currentTarget;
  if (!videoRecorder.isRecording) {
    videoRecorder.startRecording(30);
    btn.textContent = "⏹ Stop & Save";
    btn.classList.add("danger");
  } else {
    videoRecorder.stopRecording();
    btn.textContent = "⏺ Rec Video";
    btn.classList.remove("danger");
  }
});

document.getElementById("btn-ack-all").addEventListener("click", () => {
  visualSimState.bamAlerts.forEach((a) => {
    a.acked = true;
  });
  renderBAMAlerts();
});

// Controles de reproducción del Escenario B (90 ticks = 1800s simulados, 20x)
document.getElementById("replay-slider").addEventListener("input", async (e) => {
  const targetTick = parseInt(e.target.value, 10);
  if (client.mode === "LIVE_API" && targetTick * 20 > (statePayload?.sim_time_s || 0)) {
    const prof = document.getElementById("select-priority-profile").value;
    const { response, mode } = await client.advanceClock({
      targetSimTimeS: targetTick * 20,
      fromSimTimeS: statePayload?.sim_time_s,
      expectedStateVersion: statePayload?.state_version,
      priorityProfileId: prof,
    });
    updateSourceBadge(mode);
    if (mode !== "STALE_DISCONNECTED" && response) {
      statePayload = response;
    }
  }
  renderTimelineTick(targetTick);
});

document.getElementById("btn-play-replay").addEventListener("click", () => {
  if (playInterval) clearInterval(playInterval);
  playInterval = setInterval(async () => {
    if (currentTickIndex >= 90) {
      clearInterval(playInterval);
      playInterval = null;
      return;
    }
    const nextTick = currentTickIndex + 1;
    if (client.mode === "LIVE_API") {
      const prof = document.getElementById("select-priority-profile").value;
      const { response, mode } = await client.advanceClock({
        stepS: 20,
        fromSimTimeS: statePayload?.sim_time_s,
        expectedStateVersion: statePayload?.state_version,
        priorityProfileId: prof,
      });
      updateSourceBadge(mode);
      if (mode === "STALE_DISCONNECTED") {
        clearInterval(playInterval);
        playInterval = null;
        return;
      }
      if (response) {
        statePayload = response;
      }
    }
    renderTimelineTick(nextTick);
  }, 1000); // 1 Hz real (90s de pared = 1800s simulados, compresión 20x exacta)
});

document.getElementById("btn-pause-replay").addEventListener("click", () => {
  if (playInterval) {
    clearInterval(playInterval);
    playInterval = null;
  }
});

document.getElementById("btn-reset-session").addEventListener("click", async () => {
  if (playInterval) {
    clearInterval(playInterval);
    playInterval = null;
  }
  const prof = document.getElementById("select-priority-profile").value;
  const { payload, mode } = await client.resetSession(prof);
  if (payload) statePayload = payload;
  updateSourceBadge(mode);
  vessel.animatedParts.ghostBox.visible = false;
  renderTimelineTick(0);
});

document.getElementById("select-priority-profile").addEventListener("change", async (e) => {
  const { response, mode } = await client.switchProfile(e.target.value);
  if (response && mode !== "STALE_DISCONNECTED") {
    statePayload = response;
  }
  updateSourceBadge(mode);
  renderTimelineTick(currentTickIndex);
});

// Inyector de Perturbaciones en Vivo (D4 / ROB)
document.querySelectorAll("[data-perturb]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const pType = btn.getAttribute("data-perturb");
    const prof = document.getElementById("select-priority-profile").value;
    const { response, mode } = await client.injectPerturbation(pType, prof);
    updateSourceBadge(mode);
    if (mode === "STALE_DISCONNECTED" || response?.status === "UPSTREAM_UNAVAILABLE") {
      document.getElementById("banner-event-text").textContent =
        "STALE / DESCONECTADO: Perturbation rejected while backend is unreachable.";
      return;
    }

    if (response.snapshot_id) {
      document.getElementById("conning-snap").textContent = response.snapshot_id;
    }

    const snap = response.current_budget_snapshot || response.result?.final_budget_snapshot || response.result?.budget_snapshot;
    if (snap) {
      statePayload.current_budget_snapshot = snap;
      renderPMSAndCooling(snap);
      renderTopologySummary(snap);
      renderEightClassStrip(snap);
    }

    if (pType === "GENERATOR_LOSS_100KW") {
      visualSimState.gen2Tripped = true;
      visualSimState.g5Mode = "DEGRADED_70PCT_LOSS";
      setXRayMode(true);
      director.setShot("XRAY_ENGINE_ROOM", true);
      highlightShotButton("XRAY_ENGINE_ROOM");
      visualSimState.bamAlerts.unshift({
        level: "alarm",
        symbol: "▲",
        code: "LIVE-D4-GEN",
        msg: "LIVE INJECTION: Generator Loss 200→100 kW · G5 Shed 45→8 kW",
        acked: false,
      });
    } else if (pType === "COOLING_LOSS_30C_40PCT") {
      visualSimState.seawaterTempC = 30.0;
      visualSimState.coolingDegraded = true;
      visualSimState.g5Mode = "DEGRADED_70PCT_LOSS";
      document.getElementById("conning-temp").textContent = "30.0 °C";
      setXRayMode(true);
      director.setShot("XRAY_ENGINE_ROOM", true);
      visualSimState.bamAlerts.unshift({
        level: "alarm",
        symbol: "▲",
        code: "LIVE-D4-COOL",
        msg: "LIVE INJECTION: Seawater 30°C + 40% Loop Loss (Cap 72 kW) · G5 Shed",
        acked: false,
      });
    } else if (pType === "URGENT_HOLD" || pType === "URGENT_STOP") {
      visualSimState.g3Mode = "HOLD_ACOUSTIC";
      director.setShot("SUBSEA_AUV_G3", true);
      highlightShotButton("SUBSEA_AUV_G3");
      visualSimState.bamAlerts.unshift({
        level: "warning",
        symbol: "◆",
        code: `LIVE-${pType}`,
        msg: `Urgent command ${pType} executed on G3 in ≤200 ms`,
        acked: false,
      });
    }
    renderBAMAlerts();
    document.getElementById("banner-event-text").textContent =
      `LIVE PERTURBATION APPLIED: ${pType} -> Snapshot ${response.snapshot_id || "UPDATED"}`;
  });
});

// Evaluador de Admisión en Vivo D1 con Caja Fantasma 3D (Ghost Placement Preview)
document.getElementById("btn-run-admission").addEventListener("click", async () => {
  const installedRaw = document.getElementById("admit-installed").value;
  const installedIds = installedRaw
    .split(",")
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);
  const candId = document.getElementById("admit-candidate").value;
  const massVal = document.getElementById("admit-mass").value.trim();
  const slotVal = document.getElementById("admit-slot").value.trim();
  const atPort = document.getElementById("admit-at-port").checked;
  const prof = document.getElementById("select-priority-profile").value;

  const overrides = {};
  if (massVal !== "") overrides.mass_kg = Number(massVal);
  if (slotVal !== "") overrides.preferred_position = slotVal;

  const { response, mode } = await client.evaluateLiveAdmission({
    priority_profile_id: prof,
    installed_guest_ids: installedIds,
    candidate_guest_id: candId,
    at_port: atPort,
    overrides,
  });
  updateSourceBadge(mode);
  const box = document.getElementById("admission-verdict-box");
  if (mode === "STALE_DISCONNECTED" || response?.status === "UPSTREAM_UNAVAILABLE") {
    box.textContent = "STALE / DESCONECTADO: Live admission requires authoritative backend connection.";
    return;
  }

  const dec = response.decision || {};
  const verdict = dec.verdict || response.status || "ERROR";

  // Mostrar caja Ghost 3D en la posición objetivo con el color semántico del veredicto
  const ghost = vessel.animatedParts.ghostBox;
  ghost.visible = true;
  const slotCoords = {
    bay1: new THREE.Vector3(-13.8, 3.95, 0.0),
    bay2: new THREE.Vector3(-0.8, 3.95, -3.55),
    hangar: new THREE.Vector3(-6.2, 4.3, 0.0),
    hangar_roof: new THREE.Vector3(-7.2, 6.65, 0.0),
    mast_3: new THREE.Vector3(6.5, 18.4, 0.0),
  };
  const chosenSlot = slotVal || (candId === "G1" ? "mast_3" : candId === "G4" ? "hangar" : candId === "G3" ? "bay1" : "bay2");
  ghost.position.copy(slotCoords[chosenSlot] || slotCoords.bay2);

  if (verdict === "YES") {
    vessel.materials.ghostMat.color.setHex(0x22c55e);
  } else if (verdict === "YES_IF") {
    vessel.materials.ghostMat.color.setHex(0xf59e0b);
    if (candId === "G5") {
      vessel.animatedParts.g5Antenna.position.set(-7.2, 6.3, 0.0); // Mover antena G5 a hangar_roof (x1.2)
    }
  } else {
    vessel.materials.ghostMat.color.setHex(0xef4444);
  }

  const witnessLines = (dec.binding_witnesses || dec.binding_constraints || [])
    .map((w) => `• [${w.resource_class}] ${w.detail || w.constraint_id} (${w.demand}/${w.capacity} ${w.unit})`)
    .join("\n");
  const condLines = (dec.conditions || []).map((c) => `• Cond: ${c}`).join("\n");

  box.textContent = `VERDICT: ${verdict} (Candidate ${candId}, Mass=${response.candidate_total_mass_kg || "?"} kg)\n${condLines}${witnessLines ? "\n" + witnessLines : ""}`;
});

// Conmutadores de Régimen de Tripulación (Escenario C: 14 / 6 / 0) e Hidrostática
document.querySelectorAll("[data-crew]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    document.querySelectorAll("[data-crew]").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentCrewRegime = parseInt(btn.getAttribute("data-crew"), 10);
    if (client.mode === "LIVE_API") {
      try {
        const res = await fetch(`${client.apiBase}/api/crew`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ crew_count: currentCrewRegime }),
        });
        if (res.ok) {
          statePayload = await res.json();
        }
      } catch (_e) {
        updateSourceBadge("STALE_DISCONNECTED");
      }
    }
    const labelMap = { 14: "14 (FULL)", 6: "6 (REDUCED)", 0: "0 (UNCREWED)" };
    document.getElementById("conning-crew").textContent = labelMap[currentCrewRegime];
    document.getElementById("banner-event-text").textContent =
      currentCrewRegime === 0
        ? "SCENARIO C · UNCREWED (0 Personnel): Physical deck/port swaps locked; logical compute & shedding active."
        : `SCENARIO C · Crew Regime set to ${labelMap[currentCrewRegime]}`;
  });
});

document.getElementById("btn-hydro-exaggerate").addEventListener("click", (e) => {
  exaggerateHydro = !exaggerateHydro;
  e.currentTarget.classList.toggle("active", exaggerateHydro);
  e.currentTarget.textContent = exaggerateHydro ? "Hydro ×20 (VISUAL)" : "Hydro ×1 (Real)";
});

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  applyRendererPixelRatio();
});

// ============================================================================
// 7. BUCLE DE SIMULACIÓN FÍSICA 60 FPS (OCÉANO, 6DOF, HÉLICES, DRON G4, AUV G3)
// ============================================================================
const clock = new THREE.Clock();

function animate() {
  requestAnimationFrame(animate);
  const dt = Math.min(0.05, clock.getDelta());
  const elapsed = clock.getElapsedTime();

  // 1. Actualizar uniforms del océano JONSWAP
  oceanSystem.oceanUniforms.uTime.value = elapsed;
  oceanSystem.oceanUniforms.uSeaState.value = visualSimState.seaState;
  oceanSystem.oceanUniforms.uWindKn.value = visualSimState.windKn;
  oceanSystem.oceanUniforms.uShipSpeedKn.value = visualSimState.speedKn;

  // 2. Calcular dinámica 6DOF del buque sobre las 8 sondas hidrostáticas
  const motion = computeVessel6DOF(
    elapsed,
    visualSimState.seaState,
    visualSimState.windKn,
    visualSimState.speedKn,
    visualSimState.headingDeg,
    visualSimState.guestMassKg,
    visualSimState.stabilityKgEq,
    exaggerateHydro
  );

  vessel.root.position.y = motion.heaveM;
  vessel.root.rotation.z = motion.pitchRad;
  vessel.root.rotation.x = motion.rollRad;

  const sinkLabel = document.getElementById("hydro-sinkage-label");
  if (sinkLabel) {
    sinkLabel.textContent = exaggerateHydro
      ? `Sinkage: ${motion.sinkageRealCm.toFixed(2)} cm (Shown ×20 = ${motion.sinkageDisplayCm.toFixed(1)} cm)`
      : `Sinkage: ${motion.sinkageRealCm.toFixed(2)} cm (TPC 4.94 t/cm)`;
  }

  // 3. Animar hélices de los 2x Pods, radar X-band (mast_1) y estado de DG2
  const propOmega = (visualSimState.speedKn / 3.0) * 8.5;
  for (const spinner of vessel.animatedParts.podSpinners) {
    spinner.rotation.x += dt * propOmega;
  }
  vessel.animatedParts.mast1Radar.rotation.y += dt * 1.85;

  // Color de DG2 y lazo térmico en modo Rayos X
  vessel.materials.matGen2.color.setHex(visualSimState.gen2Tripped ? 0xef4444 : 0x10b981);
  vessel.materials.matGen2.emissive.setHex(visualSimState.gen2Tripped ? 0xb91c1c : 0x059669);
  vessel.materials.matCoolingLoop.color.setHex(visualSimState.coolingDegraded ? 0xf59e0b : 0x06b6d4);

  // 4. Animar Dron G4 VTOL SAR (Patrulla en el aire -> Aproximación -> Toma en cubierta de vuelo)
  const drone = vessel.animatedParts.g4Drone;
  for (const rotor of vessel.animatedParts.g4Rotors) {
    rotor.rotation.y += dt * (visualSimState.g4Landed ? 6.0 : 28.0);
  }
  if (visualSimState.g4Landed) {
    drone.position.lerp(new THREE.Vector3(-22.8, 2.95, 0.0), dt * 3.0);
    drone.rotation.set(0, 0, 0);
    vessel.materials.droneAlertRingMat.color.setHex(0x38bdf8); // Cargando a 35 kW
  } else if (visualSimState.simTimeS >= 480) {
    // Aproximación final con alerta de batería 18% (<20%)
    const approachTarget = new THREE.Vector3(
      -22.8 - Math.cos(elapsed * 0.9) * 6.5,
      6.8 + Math.sin(elapsed * 1.3) * 1.2,
      Math.sin(elapsed * 0.9) * 5.0
    );
    drone.position.lerp(approachTarget, dt * 2.2);
    vessel.materials.droneAlertRingMat.color.setHex(0xef4444); // Rojo alarma <20%
  } else {
    // Circuito de patrulla SAR sobre la popa
    drone.position.set(
      -20.0 + Math.cos(elapsed * 0.55) * 14.0,
      13.5 + Math.sin(elapsed * 1.1) * 1.1,
      Math.sin(elapsed * 0.55) * 12.0
    );
    drone.rotation.y = -elapsed * 0.55;
    vessel.materials.droneAlertRingMat.color.setHex(0x22c55e);
  }

  // 5. Animar AUV Submarino G3 y anillos de módem acústico (SURVEY_QUIET vs HOLD_ACOUSTIC)
  const auv = vessel.animatedParts.g3AuvSubmerged;
  const isHold = visualSimState.g3Mode === "HOLD_ACOUSTIC";
  if (isHold) {
    // En HOLD_ACOUSTIC mantiene posición fija estacionaria con anillos ámbar
    auv.position.lerp(new THREE.Vector3(-19.5, -8.8, 7.5), dt * 2.0);
    vessel.materials.sonarConeMat.color.setHex(0xf59e0b);
  } else {
    auv.position.set(
      -17.0 + Math.sin(elapsed * 0.35) * 4.5,
      -9.2 + Math.cos(elapsed * 0.7) * 0.4,
      8.0 + Math.cos(elapsed * 0.35) * 2.2
    );
    vessel.materials.sonarConeMat.color.setHex(0x22d3ee);
  }

  vessel.animatedParts.acousticRings.forEach((ring, idx) => {
    const pulse = ((elapsed * 1.2 + idx * 0.33) % 1.0);
    ring.position.y = pulse * 6.5;
    ring.scale.setScalar(1.0 + pulse * 0.85);
    ring.material.opacity = (1.0 - pulse) * 0.65;
    ring.material.color.setHex(isHold ? 0xf59e0b : 0x22d3ee);
  });

  // 6. Animar Haz SATCOM de G5 (45 kW FULL vs 8 kW DEGRADED_70PCT_LOSS)
  const isG5Degraded = visualSimState.g5Mode === "DEGRADED_70PCT_LOSS";
  vessel.materials.satBeamMat.opacity = isG5Degraded ? 0.08 : 0.28 + Math.sin(elapsed * 3.0) * 0.05;
  vessel.materials.satBeamMat.color.setHex(isG5Degraded ? 0xf59e0b : 0x818cf8);

  // 7. Niebla hidro-óptica submarina automática si la cámara baja de Y < 0.2m
  if (camera.position.y < 0.2) {
    scene.fog.color.setHex(0x041e30);
    scene.fog.density = 0.018;
  } else {
    scene.fog.density = 0.0014;
  }

  director.update(dt);
  renderer.render(scene, camera);
}

// Arranque inicial cargando el estado desde el backend (:8765) o el fixture determinista
(async function init() {
  const { payload, mode } = await client.getState("survey_primary");
  statePayload = payload;
  updateSourceBadge(mode);
  renderTimelineTick(0);
  animate();
})();
