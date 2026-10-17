let currentStatePayload = null;
let currentTickIndex = 0;
let playInterval = null;

async function fetchConsoleState(priorityProfile = "survey_primary") {
  const res = await fetch(`/api/state?priority_profile=${encodeURIComponent(priorityProfile)}`);
  const data = await res.json();
  currentStatePayload = data;
  renderTopBadges(data);
  renderEightClassBudgets(data.current_budget_snapshot || data.initial_budget_snapshot);
  render2DShipLayout(data.current_budget_snapshot || data.initial_budget_snapshot);
  renderCombinationsTable(data.scenario_A_combinations);
  renderTimelineTick(currentTickIndex);
}

function renderTopBadges(data) {
  const host = data.doctor.local_host;
  const snapEl = document.getElementById("badge-snapshot");
  if (snapEl) {
    snapEl.textContent = `Snapshot: ${data.snapshot_id || "SNAP_V0001"} (v${data.state_version || 1})`;
  }
  document.getElementById("badge-host").textContent =
    `Engine: ${host.hostname} (${host.system}/${host.machine}) PID=${host.pid}`;
  document.getElementById("badge-hash").textContent =
    `Semantic SHA-256: ${data.scenario_B_replay.semantic_sha256.slice(0, 16)}...`;
  document.getElementById("badge-board").textContent =
    `Standalone Ready: ${host.standalone_ready ? "PASS" : "NO"} | Code: ${host.code_sha256.slice(0, 10)}`;
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
      <strong>MAST (x3.0 Stability)</strong>
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
  if (!currentStatePayload) return;
  const replay = currentStatePayload.scenario_B_replay;
  const ticks = replay.timeline_ticks;
  const clamped = Math.max(0, Math.min(ticks.length - 1, tickIdx));
  currentTickIndex = clamped;

  document.getElementById("timeline-slider").value = clamped;
  const t = ticks[clamped];
  const compFactor = currentStatePayload.time_compression ? currentStatePayload.time_compression.acceleration_factor : 20;
  document.getElementById("timeline-label").textContent =
    `Tick ${String(t.tick).padStart(2, "0")}/90 (Sim T=${t.sim_time_s}s, ${compFactor}x)`;

  const guestSummary = Object.entries(t.guest_modes)
    .map(([gid, g]) => `${gid}: ${g.mode} (${g.powered_on ? "ON" : "OFF"}, ${g.mode_elapsed_s || 0}s)`)
    .join(" | ");

  document.getElementById("tick-status-box").innerHTML = `
    <div><strong>Snapshot:</strong> ${currentStatePayload.snapshot_id} | <strong>Plan Version:</strong> v${t.plan_version || 0} | <strong>Vessel Mode:</strong> ${t.vessel_mode} | <strong>Feasible:</strong> ${t.feasible ? "YES" : "NO"}</div>
    <div><strong>Power:</strong> Actual=${t.power_actual_w} W | Firm Committed=${t.power_committed_w} W | Reserve Cap=${t.power_capacity_w} W</div>
    <div><strong>Chilled Water Cooling:</strong> Actual=${t.cooling_actual_w} W | Effective Cap=${t.cooling_capacity_w} W | <strong>Inertia:</strong> ${t.thermal_inertia_status || "NOMINAL"}</div>
    <div><strong>Active Guests:</strong> ${guestSummary}</div>
    <div><strong>Events at Tick:</strong> ${t.events.length ? t.events.join(", ") : "Nominal 1Hz monitoring"}</div>
  `;

  // Synchronize 8-class power/cooling cards with current replay tick when scrubbing
  if (currentStatePayload.current_budget_snapshot) {
    const syncSnap = JSON.parse(JSON.stringify(currentStatePayload.current_budget_snapshot));
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
    renderEightClassBudgets(syncSnap);
  }

  // Show decisions up to current tick
  const relevantDecisions = replay.decisions.filter((d) => d.tick <= clamped);
  document.getElementById("decision-log-box").textContent = JSON.stringify(
    {
      snapshot_id: currentStatePayload.snapshot_id,
      priority_profile_id: replay.priority_profile_id,
      executed_touchdown_time_s: replay.executed_touchdown_time_s,
      semantic_sha256: replay.semantic_sha256,
      decisions_emitted_up_to_tick: relevantDecisions,
    },
    null,
    2
  );
}

function renderCombinationsTable(combosReport) {
  const container = document.getElementById("combinations-table-container");
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

document.getElementById("select-priority").addEventListener("change", (e) => {
  fetchConsoleState(e.target.value);
});

document.getElementById("timeline-slider").addEventListener("input", (e) => {
  renderTimelineTick(parseInt(e.target.value, 10));
});

document.getElementById("btn-play").addEventListener("click", () => {
  if (playInterval) clearInterval(playInterval);
  playInterval = setInterval(() => {
    if (currentTickIndex >= 90) {
      clearInterval(playInterval);
      playInterval = null;
      return;
    }
    renderTimelineTick(currentTickIndex + 1);
  }, 250);
});

document.getElementById("btn-pause").addEventListener("click", () => {
  if (playInterval) {
    clearInterval(playInterval);
    playInterval = null;
  }
});

document.getElementById("btn-reset").addEventListener("click", async () => {
  if (playInterval) {
    clearInterval(playInterval);
    playInterval = null;
  }
  const prof = document.getElementById("select-priority").value;
  await fetch("/api/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ priority_profile_id: prof }),
  });
  currentTickIndex = 0;
  await fetchConsoleState(prof);
});

document.querySelectorAll("[data-perturb]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const pType = btn.getAttribute("data-perturb");
    const prof = document.getElementById("select-priority").value;
    const res = await fetch("/api/perturb", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ perturbation_type: pType, priority_profile_id: prof }),
    });
    const payload = await res.json();
    document.getElementById("decision-log-box").textContent = JSON.stringify(payload, null, 2);
    if (payload.snapshot_id && currentStatePayload) {
      currentStatePayload.snapshot_id = payload.snapshot_id;
      currentStatePayload.state_version = payload.state_version;
      renderTopBadges(currentStatePayload);
    }
    if (payload.result && payload.result.final_budget_snapshot) {
      currentStatePayload.current_budget_snapshot = payload.result.final_budget_snapshot;
      renderEightClassBudgets(payload.result.final_budget_snapshot);
      render2DShipLayout(payload.result.final_budget_snapshot);
    } else if (payload.result && payload.result.budget_snapshot) {
      currentStatePayload.current_budget_snapshot = payload.result.budget_snapshot;
      renderEightClassBudgets(payload.result.budget_snapshot);
      render2DShipLayout(payload.result.budget_snapshot);
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
  const massVal = document.getElementById("input-mass-kg").value;
  const powerVal = document.getElementById("input-power-w").value;
  const posVal = document.getElementById("select-pos-override").value;
  const atPort = document.getElementById("chk-at-port").checked;
  const prof = document.getElementById("select-priority").value;

  const overrides = {};
  if (massVal !== "") overrides.mass_kg = parseInt(massVal, 10);
  if (powerVal !== "") overrides.nominal_power_w = parseInt(powerVal, 10);
  if (posVal !== "") overrides.preferred_position = posVal;

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
  const payload = await res.json();
  document.getElementById("admission-result-box").textContent = JSON.stringify(payload, null, 2);
  if (payload.current_budget_snapshot) {
    currentStatePayload.current_budget_snapshot = payload.current_budget_snapshot;
    currentStatePayload.snapshot_id = payload.snapshot_id;
    currentStatePayload.state_version = payload.state_version;
    renderTopBadges(currentStatePayload);
    renderEightClassBudgets(payload.current_budget_snapshot);
    render2DShipLayout(payload.current_budget_snapshot);
  }
});

fetchConsoleState("survey_primary");
