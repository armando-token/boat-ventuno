let currentStatePayload = null;
let currentTickIndex = 0;
let playInterval = null;

function setStaleBanner(isStale, reason = "") {
  const banner = document.getElementById("stale-banner");
  const badge = document.getElementById("badge-connection");
  if (banner) {
    banner.style.display = isStale ? "block" : "none";
    if (reason) {
      banner.textContent = `⚠ STALE / DESCONECTADO — ${reason}`;
    }
  }
  if (badge) {
    badge.textContent = isStale ? "STALE / DESCONECTADO" : "LIVE: AUTHORITATIVE_GOVERNOR";
    badge.style.borderColor = isStale ? "#ef4444" : "#22c55e";
    badge.style.color = isStale ? "#fecaca" : "#86efac";
  }
}

async function fetchConsoleState(priorityProfile = "survey_primary") {
  try {
    const res = await fetch(`/api/state?priority_profile=${encodeURIComponent(priorityProfile)}`);
    if (!res.ok) {
      setStaleBanner(true, `HTTP ${res.status} on /api/state`);
      return;
    }
    const data = await res.json();
    if (data && data.status === "UPSTREAM_UNAVAILABLE") {
      setStaleBanner(true, "Upstream authoritative governor unavailable");
      return;
    }
    setStaleBanner(false);
    applyAuthoritativeState(data);
  } catch (err) {
    setStaleBanner(true, err.message || "Network error");
  }
}

function applyAuthoritativeState(data) {
  if (!data) return;
  currentStatePayload = data;
  if (typeof data.sim_time_s === "number") {
    currentTickIndex = Math.max(0, Math.min(90, Math.round(data.sim_time_s / 20)));
  }
  renderTopBadges(data);
  if (data.scenario_A_combinations) {
    renderCombinationsTable(data.scenario_A_combinations);
  }
  renderCrewControls(data);
  renderTimelineTick(currentTickIndex);
}

function renderCrewControls(data) {
  const crewCount = data.crew_count ?? (data.state && data.state.crew_count) ?? 14;
  const crewRegime = data.crew_regime || (data.state && data.state.crew_regime) || "FULL_CREW_14";
  const deckClear = data.deck_clear ?? (data.state && data.state.deck_clear) ?? true;
  const badge = document.getElementById("crew-regime-badge");
  if (badge) {
    badge.textContent = `${crewRegime} (${crewCount}) | Deck: ${deckClear ? "CLEAR" : "FOULED"}`;
  }
  document.querySelectorAll("[data-crew]").forEach((btn) => {
    const isAct = parseInt(btn.getAttribute("data-crew"), 10) === crewCount;
    btn.classList.toggle("primary", isAct);
  });
  const chkDeck = document.getElementById("chk-deck-clear");
  if (chkDeck) chkDeck.checked = Boolean(deckClear);
}

function renderTopBadges(data, activeSnapshotId = null) {
  const host = (data.doctor && data.doctor.local_host) || {};
  const snapEl = document.getElementById("badge-snapshot");
  if (snapEl) {
    const sid = activeSnapshotId || data.snapshot_id || "SNAP_V0001";
    snapEl.textContent = `Snapshot: ${sid} (v${data.state_version || 1})`;
  }
  const profShaEl = document.getElementById("badge-profile-sha256");
  if (profShaEl) {
    const psha = data.active_priority_profile_sha256 || "";
    profShaEl.textContent = `Profile (${data.priority_profile_id || "survey_primary"}): ${psha ? psha.slice(0, 12) + "..." : "canonical"}`;
  }
  if (host.hostname) {
    document.getElementById("badge-host").textContent =
      `Engine: ${host.hostname} (${host.system}/${host.machine}) PID=${host.pid}`;
  }
  if (data.scenario_B_replay && data.scenario_B_replay.semantic_sha256) {
    document.getElementById("badge-hash").textContent =
      `Semantic SHA-256: ${data.scenario_B_replay.semantic_sha256.slice(0, 16)}...`;
  }
  if (host.code_sha256) {
    document.getElementById("badge-board").textContent =
      `Standalone Ready: ${host.standalone_ready ? "PASS" : "NO"} | Code: ${host.code_sha256.slice(0, 10)}`;
  }
}

function renderEightClassBudgets(snapshot) {
  const container = document.getElementById("resource-cards-container");
  container.innerHTML = "";
  const pill = document.getElementById("overall-feasibility-pill");
  pill.textContent = snapshot.feasible ? "ALL 8 CLASSES FEASIBLE" : "CONSTRAINT VIOLATION";
  pill.className = snapshot.feasible ? "pill" : "pill infeasible";

  const order = ["power", "cooling", "positions", "mast", "mass_stability", "data", "compute", "time"];
  for (const key of order) {
    const cls = snapshot.classes[key];
    if (!cls) continue;
    const card = document.createElement("div");
    card.className = cls.feasible ? "res-card" : "res-card infeasible";
    card.innerHTML = `
      <div class="res-card-title">
        <span>${key.replace("_", " / ")}</span>
        <span style="color:${cls.feasible ? "#22c55e" : "#ef4444"}">${cls.feasible ? "OK" : "BOUND"}</span>
      </div>
      <div class="res-metrics">
        Cap: ${cls.degraded_capacity} ${cls.unit} (Nom: ${cls.capacity})<br/>
        Committed: ${cls.committed} ${cls.unit} | Actual: ${cls.actual} ${cls.unit}<br/>
        Free Margin: <strong>${cls.free} ${cls.unit}</strong> [${cls.provenance}]
      </div>
    `;
    container.appendChild(card);
  }
}

function render2DShipLayout(snapshot) {
  const pos = snapshot.classes.positions.local_limits;
  const mast = snapshot.classes.mast.local_limits;
  const comp = snapshot.classes.compute.local_limits.nodes;
  const mass = snapshot.classes.mass_stability.local_limits;
  const container = document.getElementById("ship-2d-container");

  const nodeLines = Object.entries(comp)
    .map(([nid, n]) => `${nid}: ${n.used_cores}/${n.allocatable_cores}c, ${n.used_accelerators}/${n.allocatable_accelerators} acc`)
    .join("<br/>");

  container.innerHTML = `
    <div class="zone-box">
      <strong>MAST (x3.0 Stability, 1 Guest Slot / 3 Total)</strong>
      mast_1, mast_2: FIXED SHIP<br/>
      mast_3: ${mast.guest_slot_mast_3.owner || "FREE"} (${mast.guest_slot_mast_3.mass_kg} kg, ${mast.guest_slot_mast_3.air_cooling_w} W air)
    </div>
    <div class="zone-box">
      <strong>HANGAR, ROOF &amp; FLIGHT DECK (x1.2)</strong>
      Hangar: ${(pos.hangar || []).join(", ") || "FREE"} (${pos.hangar_total_mass_kg || 0}/${pos.hangar_max_mass_kg || 1000} kg)<br/>
      Hangar Roof: ${(pos.hangar_roof || []).join(", ") || "FREE"}<br/>
      Flight Deck: ${(pos.flight_deck || []).join(", ") || "CLEAR"}
    </div>
    <div class="zone-box">
      <strong>PAYLOAD BAYS (x1.0 Stability)</strong>
      Bay1 (20ft ≤8t): ${(pos.bay1_20ft || []).join(", ") || "SPLIT/FREE"} (${pos.bay1_total_mass_kg || 0}/${pos.bay1_max_mass_kg || 8000} kg)<br/>
      Bay1_a / Bay1_b: ${(pos.bay1_a_10ft || []).join(",") || "-"} / ${(pos.bay1_b_10ft || []).join(",") || "-"}<br/>
      Bay2 (10ft ≤4t): ${(pos.bay2_10ft || []).join(", ") || "FREE"} (${pos.bay2_total_mass_kg || 0}/${pos.bay2_max_mass_kg || 4000} kg)
    </div>
    <div class="zone-box">
      <strong>4-NODE COMPUTE RACK (80% Quota)</strong>
      ${nodeLines}<br/>
      Mass: ${mass.mass_margin_kg.actual_kg}/10000 kg | Stab: ${mass.stability_equivalent_kg.actual_kg_eq}/12000 kg-eq
    </div>
  `;
}

function renderTimelineTick(tickIdx) {
  if (!currentStatePayload || !currentStatePayload.scenario_B_replay) return;
  const replay = currentStatePayload.scenario_B_replay;
  const ticks = replay.timeline_ticks;
  const clamped = Math.max(0, Math.min(ticks.length - 1, tickIdx));
  currentTickIndex = clamped;

  document.getElementById("timeline-slider").value = clamped;
  const t = ticks[clamped];
  const compFactor = currentStatePayload.time_compression ? currentStatePayload.time_compression.acceleration_factor : 20;
  const phaseTag = t.is_prediction ? "PREDICTION" : "EXECUTED";
  document.getElementById("timeline-label").textContent =
    `Tick ${String(t.tick).padStart(2, "0")}/90 (Sim T=${t.sim_time_s}s, ${compFactor}x, ${phaseTag})`;

  const tickSnapshotId = t.snapshot_id || currentStatePayload.snapshot_id;
  renderTopBadges(currentStatePayload, tickSnapshotId);

  const guestSummary = Object.entries(t.guest_modes)
    .map(([gid, g]) => {
      const posStr = g.placements ? Object.values(g.placements).join("+") : "";
      return `${gid}: ${g.mode} (${g.powered_on ? "ON" : "OFF"}, ${g.mode_elapsed_s || 0}s${posStr ? ", @" + posStr : ""})`;
    })
    .join(" | ");

  document.getElementById("tick-status-box").innerHTML = `
    <div><strong>Run:</strong> ${currentStatePayload.run_id || "RUN_01"} (${phaseTag}) | <strong>Snapshot:</strong> ${tickSnapshotId} | <strong>Plan Version:</strong> v${t.plan_version || 0} | <strong>Vessel Mode:</strong> ${t.vessel_mode} (${t.speed_kn ?? 3} kn, wind ${t.wind_kn ?? 14} kn, SS ${t.sea_state ?? 2}, ${t.seawater_temp_c ?? 24}°C) | <strong>Crew:</strong> ${currentStatePayload.crew_regime || "FULL_CREW_14"} | <strong>Feasible:</strong> ${t.feasible ? "YES" : "NO"}</div>
    <div><strong>Power:</strong> Actual=${t.power_actual_w} W | Firm Committed=${t.power_committed_w} W | Reserve Cap=${t.power_capacity_w} W</div>
    <div><strong>Chilled Water Cooling:</strong> Actual=${t.cooling_actual_w} W | Effective Cap=${t.cooling_capacity_w} W | <strong>Inertia:</strong> ${t.thermal_inertia_status || "NOMINAL"}</div>
    <div><strong>Active Guests:</strong> ${guestSummary}</div>
    <div><strong>Events at Tick:</strong> ${t.events.length ? t.events.join(", ") : "Nominal 1Hz monitoring"}</div>
  `;

  // Render full 8-class budgets AND 2D ship topology from the tick's exact budget_snapshot (R5.2, V2-F07)
  const tickSnap = t.budget_snapshot || currentStatePayload.current_budget_snapshot || currentStatePayload.initial_budget_snapshot;
  if (tickSnap) {
    renderEightClassBudgets(tickSnap);
    render2DShipLayout(tickSnap);
  }

  // Show decisions up to current tick + canonical decision_log causal sentences
  const relevantDecisions = (replay.decisions || []).filter((d) => d.tick <= clamped);
  const canonicalDecisionLog = (currentStatePayload.decision_log || []).filter((d) => d.tick <= clamped);
  document.getElementById("decision-log-box").textContent = JSON.stringify(
    {
      run_id: currentStatePayload.run_id,
      tick_state: phaseTag,
      snapshot_id: tickSnapshotId,
      priority_profile_id: replay.priority_profile_id,
      active_priority_profile_sha256: currentStatePayload.active_priority_profile_sha256,
      executed_touchdown_time_s: replay.executed_touchdown_time_s,
      semantic_sha256: replay.semantic_sha256,
      pending_transitions: currentStatePayload.pending_transitions || [],
      decision_log: canonicalDecisionLog,
      decisions_emitted_up_to_tick: relevantDecisions,
    },
    null,
    2
  );
}

function renderCombinationsTable(combosReport) {
  const container = document.getElementById("combinations-table-container");
  if (!container || !combosReport || !combosReport.combinations) return;
  const rows = combosReport.combinations
    .map(
      (c) => `
      <tr>
        <td>${c.combination.join("+")}</td>
        <td>${c.total_mass_kg} kg</td>
        <td style="color:${c.exceeds_10000_kg ? "#f59e0b" : "#22c55e"}">${c.exceeds_10000_kg ? "YES (>10t)" : "NO (≤10t)"}</td>
        <td>4th (${c.fourth_guest_id}): <strong>${c.fourth_guest_verdict}</strong></td>
      </tr>`
    )
    .join("");

  container.innerHTML = `
    <table class="combo-table">
      <thead>
        <tr>
          <th>4-Guest Set</th>
          <th>Mass (G4=990kg)</th>
          <th>&gt;10,000 kg?</th>
          <th>Sequential D1 Verdict</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

// Embedded 3D Viewport (/viz3d) toggle
const btnToggleViz3d = document.getElementById("btn-toggle-viz3d");
const btnCloseViz3d = document.getElementById("btn-close-viz3d");
if (btnToggleViz3d) {
  btnToggleViz3d.addEventListener("click", () => {
    const panel = document.getElementById("embedded-viz3d-panel");
    const iframe = document.getElementById("viz3d-iframe");
    if (!panel || !iframe) return;
    const isHidden = panel.style.display === "none";
    panel.style.display = isHidden ? "block" : "none";
    if (isHidden && iframe.getAttribute("src") === "about:blank") {
      iframe.setAttribute("src", iframe.getAttribute("data-src") || "/viz3d");
    }
  });
}
if (btnCloseViz3d) {
  btnCloseViz3d.addEventListener("click", () => {
    const panel = document.getElementById("embedded-viz3d-panel");
    if (panel) panel.style.display = "none";
  });
}

// Priority Profile Switcher & Custom YAML/JSON Editor (ALIGN-T27)
document.getElementById("select-priority").addEventListener("change", async (e) => {
  const prof = e.target.value;
  try {
    const res = await fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ priority_profile_id: prof }),
    });
    if (!res.ok) {
      setStaleBanner(true, `HTTP ${res.status} on /api/profile`);
      return;
    }
    const payload = await res.json();
    setStaleBanner(false);
    applyAuthoritativeState(payload);
  } catch (err) {
    setStaleBanner(true, err.message || "Network error");
  }
});

const btnLoadSampleProfile = document.getElementById("btn-load-sample-profile");
if (btnLoadSampleProfile) {
  btnLoadSampleProfile.addEventListener("click", () => {
    const ta = document.getElementById("textarea-custom-profile");
    if (!ta) return;
    ta.value = [
      'schema_version: "sdv-priority-profile/1.0"',
      'priority_profile_id: "CUSTOM_SAR_OVERRIDE"',
      'name: "Custom SAR Override Priority Profile"',
      'description: "Custom SAR-first priority profile with immediate G4 recovery turn and strict D4 shed order."',
      "capability_weights:",
      "  maritime_sar_rescue: 100.0",
      "  hydrographic_survey: 85.0",
      "  seabed_anomaly_detection: 80.0",
      "  environmental_sensing: 70.0",
      "  water_quality_monitoring: 65.0",
      "  subsea_intervention: 60.0",
      "  hull_and_cable_inspection: 55.0",
      "  surface_surveillance: 45.0",
      "  flight_weather_support: 50.0",
      "  wideband_spectrum_monitoring: 30.0",
      "  salvage_assistance: 25.0",
      "guest_priority_rank:",
      "  G4: 1",
      "  G3: 2",
      "  G1: 3",
      "  G6: 4",
      "  G2: 5",
      "  G5: 6",
      "arbitration_policy:",
      '  survey_vs_recovery_strategy: "IMMEDIATE_FULL_SUSPENSION"',
      "  pre_reserve_recovery_power: true",
      "  max_acceptable_survey_pause_min: 30",
      '  tie_break_order: "HIGHER_CAPABILITY_SCORE_THEN_LEXICOGRAPHIC"',
      "degradation_policy:",
      "  prefer_mode_downgrade_before_shed: true",
      "  protect_uninterruptible_tasks: true",
      '  shed_order_lowest_priority_first: ["G5", "G2", "G6", "G1", "G3", "G4"]',
    ].join("\n");
  });
}

const btnApplyCustomProfile = document.getElementById("btn-apply-custom-profile");
if (btnApplyCustomProfile) {
  btnApplyCustomProfile.addEventListener("click", async () => {
    const ta = document.getElementById("textarea-custom-profile");
    const raw = ta ? ta.value.trim() : "";
    if (!raw) return;
    try {
      const res = await fetch("/api/profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ priority_profile_yaml: raw }),
      });
      const payload = await res.json();
      if (res.status === 422) {
        document.getElementById("decision-log-box").textContent = JSON.stringify(payload, null, 2);
        return;
      }
      if (!res.ok) {
        setStaleBanner(true, `HTTP ${res.status} on /api/profile`);
        return;
      }
      setStaleBanner(false);
      const pid = payload.priority_profile_id;
      const sel = document.getElementById("select-priority");
      if (sel && pid) {
        let opt = Array.from(sel.options).find((o) => o.value === pid);
        if (!opt) {
          opt = document.createElement("option");
          opt.value = pid;
          opt.textContent = `${pid} (Custom Validated Profile)`;
          sel.appendChild(opt);
        }
        sel.value = pid;
      }
      applyAuthoritativeState(payload);
    } catch (err) {
      setStaleBanner(true, err.message || "Network error");
    }
  });
}

// Scenario C Crew Regime (14 / 6 / 0) & Deck Clear Interlock
document.querySelectorAll("[data-crew]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const crewCount = parseInt(btn.getAttribute("data-crew"), 10);
    const chkDeck = document.getElementById("chk-deck-clear");
    const deckClear = chkDeck ? chkDeck.checked : true;
    try {
      const res = await fetch("/api/crew", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ crew_count: crewCount, deck_clear: deckClear }),
      });
      if (!res.ok) {
        setStaleBanner(true, `HTTP ${res.status} on /api/crew`);
        return;
      }
      const payload = await res.json();
      setStaleBanner(false);
      applyAuthoritativeState(payload);
    } catch (err) {
      setStaleBanner(true, err.message || "Network error");
    }
  });
});

const chkDeckClear = document.getElementById("chk-deck-clear");
if (chkDeckClear) {
  chkDeckClear.addEventListener("change", async (e) => {
    const crewCount = (currentStatePayload && currentStatePayload.crew_count) ?? 14;
    try {
      const res = await fetch("/api/crew", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ crew_count: crewCount, deck_clear: e.target.checked }),
      });
      if (!res.ok) {
        setStaleBanner(true, `HTTP ${res.status} on /api/crew`);
        return;
      }
      const payload = await res.json();
      setStaleBanner(false);
      applyAuthoritativeState(payload);
    } catch (err) {
      setStaleBanner(true, err.message || "Network error");
    }
  });
}

document.getElementById("timeline-slider").addEventListener("input", async (e) => {
  const targetIdx = parseInt(e.target.value, 10);
  const targetSimS = targetIdx * 20;
  if (currentStatePayload && targetSimS > (currentStatePayload.sim_time_s || 0)) {
    const prof = document.getElementById("select-priority").value;
    try {
      const res = await fetch("/api/advance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target_sim_time_s: targetSimS,
          from_sim_time_s: currentStatePayload.sim_time_s,
          expected_state_version: currentStatePayload.state_version,
          priority_profile_id: prof,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setStaleBanner(false);
        applyAuthoritativeState(data);
        return;
      }
    } catch (err) {
      setStaleBanner(true, err.message || "Network error");
      return;
    }
  }
  renderTimelineTick(targetIdx);
});

document.getElementById("btn-play").addEventListener("click", () => {
  if (playInterval) clearInterval(playInterval);
  const wallIntervalMs =
    currentStatePayload &&
    currentStatePayload.time_compression &&
    currentStatePayload.time_compression.tick_interval_wall_ms
      ? currentStatePayload.time_compression.tick_interval_wall_ms
      : 1000;
  playInterval = setInterval(async () => {
    if (currentTickIndex >= 90) {
      clearInterval(playInterval);
      playInterval = null;
      return;
    }
    const nextIdx = currentTickIndex + 1;
    const targetSimS = nextIdx * 20;
    const prof = document.getElementById("select-priority").value;
    try {
      const res = await fetch("/api/advance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target_sim_time_s: targetSimS,
          from_sim_time_s: currentStatePayload ? currentStatePayload.sim_time_s : undefined,
          expected_state_version: currentStatePayload ? currentStatePayload.state_version : undefined,
          priority_profile_id: prof,
          runner_status: nextIdx >= 90 ? "COMPLETED" : "RUNNING",
        }),
      });
      if (!res.ok) {
        setStaleBanner(true, `HTTP ${res.status} on /api/advance`);
        clearInterval(playInterval);
        playInterval = null;
        return;
      }
      const data = await res.json();
      setStaleBanner(false);
      currentTickIndex = nextIdx;
      applyAuthoritativeState(data);
    } catch (err) {
      setStaleBanner(true, err.message || "Network error");
      clearInterval(playInterval);
      playInterval = null;
    }
  }, wallIntervalMs);
});

document.getElementById("btn-pause").addEventListener("click", async () => {
  if (playInterval) {
    clearInterval(playInterval);
    playInterval = null;
  }
  try {
    await fetch("/api/runner", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ delta_sim_s: 0, runner_status: "PAUSED" }),
    });
  } catch (err) {
    setStaleBanner(true, err.message || "Network error");
  }
});

document.getElementById("btn-reset").addEventListener("click", async () => {
  if (playInterval) {
    clearInterval(playInterval);
    playInterval = null;
  }
  const prof = document.getElementById("select-priority").value;
  try {
    const res = await fetch("/api/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ priority_profile_id: prof }),
    });
    if (!res.ok) {
      setStaleBanner(true, `HTTP ${res.status} on /api/reset`);
      return;
    }
    const data = await res.json();
    setStaleBanner(false);
    currentTickIndex = 0;
    applyAuthoritativeState(data);
  } catch (err) {
    setStaleBanner(true, err.message || "Network error");
  }
});

document.querySelectorAll("[data-perturb]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const pType = btn.getAttribute("data-perturb");
    const prof = document.getElementById("select-priority").value;
    const targetSimS = currentTickIndex * 20;
    try {
      const res = await fetch("/api/perturb", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          perturbation_type: pType,
          priority_profile_id: prof,
          sim_time_s: targetSimS,
        }),
      });
      if (!res.ok) {
        setStaleBanner(true, `HTTP ${res.status} on /api/perturb`);
        return;
      }
      const payload = await res.json();
      setStaleBanner(false);
      await fetchConsoleState(prof);
      document.getElementById("decision-log-box").textContent = JSON.stringify(payload, null, 2);
    } catch (err) {
      setStaleBanner(true, err.message || "Network error");
    }
  });
});

document.getElementById("btn-admit-live").addEventListener("click", async () => {
  const installedIds = document
    .getElementById("input-installed")
    .value.split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const candId = document.getElementById("select-candidate").value;
  const posVal = document.getElementById("select-pos-override").value;
  const atPort = document.getElementById("chk-at-port").checked;
  const prof = document.getElementById("select-priority").value;

  const overrides = {};
  if (posVal !== "") overrides.preferred_position = posVal;

  const numericOverrideMap = [
    ["input-mass-kg", "mass_kg"],
    ["input-power-w", "nominal_power_w"],
    ["input-sustained-power-w", "sustained_power_w"],
    ["input-peak-power-w", "peak_power_w"],
    ["input-cooling-water-w", "cooling_water_w"],
    ["input-cooling-air-mast-w", "cooling_air_mast_w"],
    ["input-compute-cores", "compute_cores"],
    ["input-compute-ram-mib", "compute_ram_mib"],
    ["input-compute-accelerators", "compute_accelerators"],
    ["input-satcom-bps", "satcom_bps"],
  ];
  for (const [elId, key] of numericOverrideMap) {
    const el = document.getElementById(elId);
    if (el && el.value.trim() !== "") {
      overrides[key] = Number(el.value.trim());
    }
  }

  try {
    const res = await fetch("/api/admit_live", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        installed_guest_ids: installedIds,
        candidate_guest_id: candId,
        priority_profile_id: prof,
        at_port: atPort,
        overrides: overrides,
      }),
    });
    if (!res.ok) {
      setStaleBanner(true, `HTTP ${res.status} on /api/admit_live`);
      return;
    }
    const payload = await res.json();
    setStaleBanner(false);
    document.getElementById("admission-result-box").textContent = JSON.stringify(payload, null, 2);
    if (payload.current_budget_snapshot) {
      await fetchConsoleState(prof);
      renderEightClassBudgets(payload.current_budget_snapshot);
      render2DShipLayout(payload.current_budget_snapshot);
    }
  } catch (err) {
    setStaleBanner(true, err.message || "Network error");
  }
});

// Golden Gate Environment Strip & Forecast Advisory Panel Fetchers (§7.2)
async function fetchEnvironmentState() {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 1500);
  try {
    const res = await fetch("/api/environment", { signal: controller.signal });
    clearTimeout(timeoutId);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderEnvironmentData(data);
  } catch (err) {
    clearTimeout(timeoutId);
    renderEnvironmentFallback(err.name === "AbortError" ? "Fetch timeout (1.5s limit)" : err.message);
  }
}

async function fetchWindowsForGuest(guestId = "G4", op = "VEHICLE_REPLACEMENT_AT_SEA") {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 1500);
  try {
    const res = await fetch(`/api/environment/windows?guest=${encodeURIComponent(guestId)}&op=${encodeURIComponent(op)}`, {
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    if (!res.ok) return;
    const winData = await res.json();
    renderWindowsData(winData);
  } catch (err) {
    clearTimeout(timeoutId);
  }
}

async function fetchForecastState() {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 1500);
  try {
    const res = await fetch("/api/forecast", { signal: controller.signal });
    clearTimeout(timeoutId);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderForecastData(data);
  } catch (err) {
    clearTimeout(timeoutId);
    renderForecastFallback(err.name === "AbortError" ? "Fetch timeout (1.5s limit)" : err.message);
  }
}

function renderEnvironmentData(data) {
  if (!data || !data.available) {
    renderEnvironmentFallback(data ? data.reason || "NO_ACTIVE_SNAPSHOT" : "No data");
    return;
  }
  const snapLbl = document.getElementById("env-snapshot-label");
  if (snapLbl) {
    snapLbl.textContent = `Recorded public data — snapshot ${data.snapshot_id || "Golden Gate"} (${data.anchor_utc ? data.anchor_utc.slice(0, 16) + 'Z' : 'UTC'})`;
  }
  const cur = data.current || {};
  const sstVal = cur.sst_c !== null && cur.sst_c !== undefined ? `${Number(cur.sst_c).toFixed(1)} °C` : "16.3 °C";
  const coolEff = data.cooling && data.cooling.effective_w ? `${(data.cooling.effective_w / 1000).toFixed(1)} kW` : "150.4 kW";

  const elSst = document.getElementById("env-sst-val");
  if (elSst) elSst.textContent = sstVal;
  const elCoolEff = document.getElementById("env-cooling-eff");
  if (elCoolEff) elCoolEff.textContent = coolEff;
  const elValSst = document.getElementById("env-val-sst");
  if (elValSst) elValSst.textContent = sstVal;
  const elSubSst = document.getElementById("env-sub-sst");
  if (elSubSst) elSubSst.textContent = `${cur.provenance || 'DERIVED'} ${cur.source_id || 'Open-Meteo GG_BAR'} | Age: ${cur.age_s ?? 0}s`;

  const windStr = cur.wind_kn !== null && cur.wind_kn !== undefined ? `${Number(cur.wind_kn).toFixed(1)} kn` : "-- kn";
  const gustStr = cur.gust_kn !== null && cur.gust_kn !== undefined ? ` (Gust ${Number(cur.gust_kn).toFixed(1)} kn)` : "";
  const elWind = document.getElementById("env-val-wind");
  if (elWind) elWind.textContent = `${windStr}${gustStr}`;
  const elSubWind = document.getElementById("env-sub-wind");
  if (elSubWind) {
    const gw = data.guard_bands ? data.guard_bands.guard_wind_kn : 3.0;
    elSubWind.textContent = `Guard band: +${gw} kn`;
  }

  const hsStr = cur.hs_m !== null && cur.hs_m !== undefined ? `${Number(cur.hs_m).toFixed(2)} m` : "1.42 m";
  const ssStr = cur.sea_state_wmo !== null && cur.sea_state_wmo !== undefined ? ` (Sea State ${cur.sea_state_wmo})` : " (Sea State 4)";
  const elHs = document.getElementById("env-val-hs");
  if (elHs) elHs.textContent = `${hsStr}${ssStr}`;
  const elSubHs = document.getElementById("env-sub-hs");
  if (elSubHs) {
    const gh = data.guard_bands ? data.guard_bands.guard_hs_m : 0.15;
    elSubHs.textContent = `WMO 3700 | Guard band: +${gh}m`;
  }

  const ribbonContainer = document.getElementById("outlook-ribbon-cells");
  if (ribbonContainer && data.outlook_48h && Array.isArray(data.outlook_48h)) {
    ribbonContainer.innerHTML = "";
    data.outlook_48h.slice(0, 48).forEach((cell, idx) => {
      const cellEl = document.createElement("div");
      const ss = cell.sea_state_wmo ?? 4;
      const wKn = cell.wind_kn ?? 16;
      const isOpen = ss <= 3 && wKn <= 20;
      cellEl.className = `outlook-cell ${isOpen ? "open" : "closed"}`;
      cellEl.title = `T+${idx}h (${cell.t_utc || ""}): Sea State ${ss}, Wind ${wKn} kn, SST ${cell.sst_c ?? "--"}°C -> ${isOpen ? "OPEN" : "CLOSED"}`;
      cellEl.dataset.hour = idx;
      ribbonContainer.appendChild(cellEl);
    });
  }

  fetchWindowsForGuest("G4", "VEHICLE_REPLACEMENT_AT_SEA");
}

function renderWindowsData(winData) {
  if (!winData) return;
  const elWin = document.getElementById("env-val-window");
  const elSubWin = document.getElementById("env-sub-window");
  if (elWin) {
    if (winData.permitted_now) {
      elWin.textContent = "PERMITTED (Window Open)";
      elWin.style.color = "var(--ok)";
    } else {
      const reason = (winData.blocking_reasons_now && winData.blocking_reasons_now[0]) || "SS > 3";
      elWin.textContent = `NOT_NOW (${reason.length > 25 ? reason.slice(0, 25) + '...' : reason})`;
      elWin.style.color = "var(--warn)";
    }
  }
  if (elSubWin && winData.next_window) {
    const nw = winData.next_window;
    elSubWin.textContent = `Next: T+${nw.start_s ?? 0}s (${nw.start_utc ? nw.start_utc.slice(11, 16) + 'Z' : ''})`;
  } else if (elSubWin) {
    elSubWin.textContent = "G4 At-Sea Replacement (A')";
  }
}

function renderEnvironmentFallback(reason = "Snapshot offline") {
  const snapLbl = document.getElementById("env-snapshot-label");
  if (snapLbl) snapLbl.textContent = `Snapshot offline: ${reason} (A.10 fallback active)`;
}

function renderForecastData(data) {
  const chip = document.getElementById("forecast-status-chip");
  const badge = document.getElementById("forecast-lead-badge");
  const sentenceBox = document.getElementById("forecast-sentence-box");
  const guestContainer = document.getElementById("forecast-guest-cards");

  if (!data || !data.enabled) {
    renderForecastFallback("Observer disabled / idle");
    return;
  }

  let highestAlert = null;
  if (data.alerts && data.alerts.length > 0) {
    highestAlert = data.alerts[0];
  }

  if (chip) {
    if (highestAlert && highestAlert.state === "WARN") {
      chip.textContent = "WARN";
      chip.className = "pill pill-warn";
    } else if (highestAlert && highestAlert.state === "WATCH") {
      chip.textContent = "WATCH";
      chip.className = "pill pill-watch";
    } else {
      chip.textContent = "QUIET";
      chip.className = "pill pill-quiet";
    }
  }

  if (badge) {
    if (highestAlert && highestAlert.h_star_s !== undefined) {
      badge.textContent = `Advance Warning: ~${highestAlert.h_star_s} s`;
    } else {
      badge.textContent = `Advance Warning: nominal`;
    }
  }

  if (sentenceBox) {
    if (highestAlert && highestAlert.sentence) {
      sentenceBox.textContent = highestAlert.sentence;
    } else {
      sentenceBox.textContent = "All guest telemetry nominal. Predictive observer running at 1 Hz across 10–60s horizons (Holt damped + conformal bands).";
    }
  }

  if (guestContainer && data.series) {
    guestContainer.innerHTML = "";
    Object.entries(data.series).forEach(([gid, sData]) => {
      const card = document.createElement("div");
      card.className = "forecast-guest-card";
      const lastY = sData.y && sData.y.length ? (sData.y[sData.y.length - 1] / 1000).toFixed(1) : "--";
      const st = (data.status_by_guest && data.status_by_guest[gid]) || "OK";
      card.innerHTML = `<strong>${gid}</strong> ${lastY} kW [${st}]`;
      guestContainer.appendChild(card);
    });
  }
}

function renderForecastFallback(reason = "Observer unavailable") {
  const chip = document.getElementById("forecast-status-chip");
  const badge = document.getElementById("forecast-lead-badge");
  const sentenceBox = document.getElementById("forecast-sentence-box");
  if (chip) {
    chip.textContent = "QUIET";
    chip.className = "pill pill-quiet";
  }
  if (badge) badge.textContent = "Advance Warning: -- s";
  if (sentenceBox) {
    sentenceBox.textContent = `Predictive observer idle: ${reason}. System operating under reactive degradation rules.`;
  }
}

fetchConsoleState("survey_primary");
fetchEnvironmentState();
fetchForecastState();
setInterval(() => {
  fetchEnvironmentState();
  fetchForecastState();
}, 3000);

