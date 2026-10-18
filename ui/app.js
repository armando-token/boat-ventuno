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

// ---------------------------------------------------------------------------
// Jury-readable renderers. All server strings go through textContent only.
// ---------------------------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function rawJsonDetails(obj, label = "Raw JSON") {
  const det = el("details", "raw-json");
  det.appendChild(el("summary", null, label));
  det.appendChild(el("pre", "raw-json-pre", JSON.stringify(obj, null, 2)));
  return det;
}

const VERDICT_CLASSES = {
  YES: "verdict-yes",
  YES_IF: "verdict-yesif",
  YES_WITH_STEP_DOWN: "verdict-yesif",
  NO: "verdict-no",
  NOT_NOW: "verdict-notnow",
  REJECTED: "verdict-no",
  VALIDATION_ERROR: "verdict-no",
};

function verdictBadge(verdict, big = false) {
  const v = String(verdict || "—");
  const cls = VERDICT_CLASSES[v] || "verdict-neutral";
  return el("span", `verdict-badge ${cls}${big ? " verdict-big" : ""}`, v);
}

const RESOURCE_LABELS = {
  power: "Power reserve",
  cooling: "Chilled-water cooling",
  mass_stability: "Guest mass / stability",
  positions: "Deck / bay position",
  data: "SATCOM / data link",
  compute: "Compute",
  time: "Install window",
  crew: "Crew",
  dependencies: "Dependency",
  environment: "Sea / weather",
  mast: "Mast",
};

const NON_NUMERIC_UNITS = new Set([
  "slot", "slots", "window", "clearance", "condition", "conflict", "mode_conflict", "guest", "",
]);

function fmtNumber(v, maxDigits = 1) {
  return Number(v).toLocaleString("en-US", { maximumFractionDigits: maxDigits });
}

function fmtQuantity(value, unit) {
  if (typeof value !== "number" || !Number.isFinite(value)) return `${value ?? "?"} ${unit || ""}`.trim();
  switch (unit) {
    case "W":
      return Math.abs(value) >= 1000 ? `${fmtNumber(value / 1000)} kW` : `${fmtNumber(value, 0)} W`;
    case "bps":
      if (Math.abs(value) >= 1e6) return `${fmtNumber(value / 1e6, 2)} Mbps`;
      if (Math.abs(value) >= 1e3) return `${fmtNumber(value / 1e3, 1)} kbps`;
      return `${fmtNumber(value, 0)} bps`;
    case "kg":
      return `${fmtNumber(value, 0)} kg`;
    case "kg_eq":
      return `${fmtNumber(value, 0)} kg-eq`;
    case "degC":
      return `${fmtNumber(value)} °C`;
    default:
      return `${fmtNumber(value, 2)} ${unit || ""}`.trim();
  }
}

function isNumericConstraint(bc) {
  return bc && typeof bc.demand === "number" && typeof bc.capacity === "number" && !NON_NUMERIC_UNITS.has(bc.unit || "");
}

function plainConstraint(bc) {
  if (!bc) return "";
  const label = RESOURCE_LABELS[bc.resource_class] || bc.resource_class || bc.constraint_id || "Constraint";
  if (!isNumericConstraint(bc)) {
    return `${label}: ${bc.detail || bc.constraint_id || "blocked"}`;
  }
  const short = typeof bc.margin === "number" ? Math.abs(bc.margin) : bc.demand - bc.capacity;
  return (
    `${label} has ${fmtQuantity(bc.capacity, bc.unit)} available, ` +
    `needs ${fmtQuantity(bc.demand, bc.unit)} (${fmtQuantity(short, bc.unit)} short)`
  );
}

function primaryBindingConstraint(constraints) {
  if (!Array.isArray(constraints) || constraints.length === 0) return null;
  return constraints.find(isNumericConstraint) || constraints[0];
}

function stripAdmissionPrefix(sentence) {
  if (!sentence) return "";
  const m = String(sentence).match(/->\s*[A-Z_]+:\s*(.*)$/);
  return m ? m[1] : String(sentence);
}

function describeAction(rd) {
  const ids = (rd.displace_guest_ids || []).join(", ") || "?";
  switch (rd.action_type) {
    case "REMOVE_AT_PORT":
      return `remove ${ids} at port`;
    case "DEACTIVATE_AND_REPLACE":
      return `switch off and replace ${ids}`;
    default:
      return `${String(rd.action_type || "displace").toLowerCase().replace(/_/g, " ")} ${ids}`;
  }
}

function admissionHeadline(payload) {
  const dec = (payload && payload.decision) || {};
  const explicit = payload.headline || payload.plain_sentence || dec.headline || dec.plain_sentence;
  if (explicit) return String(explicit);
  const verdict = dec.verdict || "";
  const cand = dec.candidate_guest_id || payload.candidate_guest_id || "candidate";
  const bc = primaryBindingConstraint(dec.binding_constraints);
  const why = bc ? plainConstraint(bc) : stripAdmissionPrefix(dec.causal_sentence || payload.causal_sentence);
  if (verdict === "YES") {
    return `Admitted: ${cand} fits — ${stripAdmissionPrefix(dec.causal_sentence || payload.causal_sentence) || "all resource classes feasible"}`;
  }
  if (verdict === "YES_IF") {
    const first = (dec.ranked_displacements || [])[0];
    const cond = first ? describeAction(first) : (dec.required_conditions || [])[0];
    return cond ? `Yes, if we ${cond}: ${why}.` : `Yes, conditionally: ${why}.`;
  }
  if (verdict === "NOT_NOW") {
    const nw = dec.next_window;
    const when = nw && typeof nw.start_s === "number" ? ` Next window opens at T+${nw.start_s}s.` : "";
    return `Not now: ${why}.${when}`;
  }
  if (verdict === "NO") return `Refused: ${why}.`;
  return why || "No verdict returned.";
}

function addSection(parent, title) {
  const sec = el("div", "rc-section");
  sec.appendChild(el("div", "rc-section-title", title));
  parent.appendChild(sec);
  return sec;
}

function fmtPositions(m) {
  return Object.entries(m || {})
    .map(([k, v]) => `${k} → ${v}`)
    .join(", ");
}

function renderAdmissionResult(payload, httpStatus = 200) {
  const box = document.getElementById("admission-result-box");
  if (!box) return;
  box.textContent = "";
  const card = el("div", "result-card");
  const dec = (payload && payload.decision) || null;

  if (!dec) {
    const head = el("div", "rc-head");
    head.appendChild(verdictBadge(payload && payload.status ? payload.status : `HTTP ${httpStatus}`, true));
    card.appendChild(head);
    card.appendChild(el("div", "rc-headline", (payload && (payload.message || payload.causal_sentence)) || "No decision returned."));
    card.appendChild(rawJsonDetails(payload));
    box.appendChild(card);
    return;
  }

  const head = el("div", "rc-head");
  head.appendChild(verdictBadge(dec.verdict, true));
  const meta = el("div", "rc-meta");
  meta.appendChild(el("strong", null, `Candidate ${dec.candidate_guest_id || payload.candidate_guest_id || "?"}`));
  if (typeof payload.candidate_total_mass_kg === "number") {
    meta.appendChild(el("span", null, ` · ${fmtQuantity(payload.candidate_total_mass_kg, "kg")}`));
  }
  if (typeof dec.calculation_elapsed_ms === "number") {
    const ok = dec.within_30s_deadline !== false;
    meta.appendChild(
      el("span", ok ? "rc-ok" : "rc-bad", ` · decided in ${fmtNumber(dec.calculation_elapsed_ms, 1)} ms${ok ? " (≤ 30 s ✓)" : " (> 30 s ✗)"}`)
    );
  }
  head.appendChild(meta);
  card.appendChild(head);

  card.appendChild(el("div", "rc-headline", admissionHeadline(payload)));

  const bcs = dec.binding_constraints || [];
  if (bcs.length) {
    const sec = addSection(card, "Binding constraints");
    const ul = el("ul", "rc-list");
    bcs.forEach((bc) => {
      const li = el("li", null, plainConstraint(bc));
      if (bc.state_label) li.appendChild(el("span", "rc-dim", ` @ ${bc.state_label}`));
      ul.appendChild(li);
    });
    sec.appendChild(ul);
  }

  const ranked = dec.ranked_displacements || [];
  const reqs = dec.required_conditions || [];
  if (dec.verdict === "YES_IF" && (ranked.length || reqs.length)) {
    const sec = addSection(card, "Conditions (ranked, cheapest first)");
    const ol = el("ol", "rc-list");
    const MAX_SHOWN = 3;
    ranked.slice(0, MAX_SHOWN).forEach((rd) => {
      const li = el("li");
      li.appendChild(el("strong", null, describeAction(rd)));
      const cost = [];
      if (typeof rd.total_score_lost === "number") cost.push(`capability lost ${fmtNumber(rd.total_score_lost)} pts`);
      if (typeof rd.net_capability_delta === "number") {
        cost.push(`net ${rd.net_capability_delta >= 0 ? "+" : ""}${fmtNumber(rd.net_capability_delta)}`);
      }
      if (cost.length) li.appendChild(el("span", null, ` — ${cost.join(", ")}`));
      (rd.capability_lost_breakdown || []).forEach((c) => {
        const mode = c.displaced_from_mode ? ` (was ${c.displaced_from_mode})` : "";
        li.appendChild(el("div", "rc-dim", `${c.capability_lost_description || c.guest_id}${mode}`));
      });
      ol.appendChild(li);
    });
    reqs.forEach((r) => ol.appendChild(el("li", null, String(r))));
    sec.appendChild(ol);
    if (ranked.length > MAX_SHOWN) {
      sec.appendChild(el("div", "rc-dim", `+${ranked.length - MAX_SHOWN} costlier option(s) — see Raw JSON`));
    }
  }

  if (dec.verdict === "NOT_NOW" && dec.next_window) {
    const sec = addSection(card, "Next window");
    const nw = dec.next_window;
    sec.appendChild(el("div", null, typeof nw.start_s === "number" ? `Opens at T+${nw.start_s}s` : nw.reason || "None in forecast horizon"));
  }

  const pl = dec.placement || {};
  const alts = dec.placement_alternatives || [];
  const rejected = dec.rejected_placements || [];
  if (pl.subcomponent_positions || alts.length || rejected.length) {
    const sec = addSection(card, "Placement (D2)");
    if (pl.subcomponent_positions) {
      const chosen = el("div");
      chosen.appendChild(el("strong", null, "Chosen: "));
      chosen.appendChild(
        document.createTextNode(
          `${fmtPositions(pl.subcomponent_positions)}${pl.compute_node ? ` · compute ${pl.compute_node}` : ""}${pl.initial_mode ? ` · starts in ${pl.initial_mode}` : ""}`
        )
      );
      sec.appendChild(chosen);
    }
    if (alts.length || rejected.length) {
      const ul = el("ul", "rc-list");
      alts.forEach((a) =>
        ul.appendChild(el("li", null, `Not chosen #${a.rank}: ${fmtPositions(a.subcomponent_positions)} — ${a.why_not_chosen || ""}`))
      );
      rejected.forEach((r) =>
        ul.appendChild(el("li", null, `Rejected: ${fmtPositions(r.subcomponent_positions)} — ${r.why_rejected || ""}`))
      );
      sec.appendChild(ul);
    }
  }

  const g = dec.one_genset_down;
  if (g) {
    const sec = addSection(card, "One genset down");
    const row = el("div");
    row.appendChild(verdictBadge(g.holds));
    row.appendChild(document.createTextNode(` ${gensetDownLabel(g.holds)} — ${g.sentence || ""}`));
    sec.appendChild(row);
  }

  if (payload.application_reason || payload.applied !== undefined) {
    card.appendChild(
      el("div", "rc-dim", `Plan ${payload.applied ? "applied" : "not applied"}${payload.application_reason ? ` (${payload.application_reason})` : ""}`)
    );
  }

  card.appendChild(rawJsonDetails(payload));
  box.appendChild(card);
}

function fmtSimTime(s) {
  return typeof s === "number" ? `T+${s}s` : "T+?";
}

function entryLabel(entry) {
  if (entry.type === "SESSION_ACTION" && entry.action) return String(entry.action);
  return String(entry.type || entry.phase || "EVENT");
}

const ADMISSION_SENTENCE_RE =
  /^Admission of (\S+) \(([^)]*)\) -> ([A-Z_]+): \[([a-z_]+)\.([^\]]+)\] demand=(-?[\d.]+) (\S+) vs capacity=(-?[\d.]+) \S+ \(margin=(-?[\d.]+) \S+\)(?: — (.*?))?\.?$/;

const VERDICT_PREFIX = { NO: "refused", NOT_NOW: "not now", YES_IF: "yes, conditionally" };

function plainEntrySentence(entry) {
  const raw = entry.causal_sentence || entry.rationale || (entry.decision && (entry.decision.causal_sentence || entry.decision.rationale));
  if (!raw) return "(no causal sentence)";
  const m = String(raw).match(ADMISSION_SENTENCE_RE);
  if (!m) return String(raw);
  const [, gid, mass, verdict, rclass, cid, demand, unit, capacity, margin, detail] = m;
  const bc = {
    resource_class: rclass,
    constraint_id: cid,
    demand: Number(demand),
    capacity: Number(capacity),
    margin: Number(margin),
    unit,
    detail,
  };
  const prefix = VERDICT_PREFIX[verdict] || verdict;
  return `${gid} (${mass}) ${prefix}: ${plainConstraint(bc)}.`;
}

function buildTimeline(entries) {
  const indexed = (entries || []).map((e, i) => ({ e, i }));
  indexed.sort((a, b) => {
    const ta = typeof a.e.sim_time_s === "number" ? a.e.sim_time_s : -1;
    const tb = typeof b.e.sim_time_s === "number" ? b.e.sim_time_s : -1;
    if (tb !== ta) return tb - ta;
    const va = a.e.state_version || 0;
    const vb = b.e.state_version || 0;
    if (vb !== va) return vb - va;
    return b.i - a.i;
  });
  const ol = el("ol", "timeline");
  if (!indexed.length) {
    ol.appendChild(el("li", "timeline-empty", "No decisions yet."));
    return ol;
  }
  indexed.forEach(({ e }) => {
    const li = el("li", "timeline-item");
    const head = el("div", "timeline-head");
    head.appendChild(el("span", "timeline-time", fmtSimTime(e.sim_time_s)));
    head.appendChild(el("span", "timeline-type", entryLabel(e)));
    if (e.verdict) head.appendChild(verdictBadge(e.verdict));
    li.appendChild(head);
    const sentenceEl = el("div", "timeline-sentence", plainEntrySentence(e));
    if (e.causal_sentence) sentenceEl.title = String(e.causal_sentence);
    li.appendChild(sentenceEl);
    ol.appendChild(li);
  });
  return ol;
}

const ELAPSED_LABELS = {
  first_shedding_command_elapsed_ms: "first shed command",
  total_sequence_elapsed_ms: "full sequence",
  replan_elapsed_ms: "replan",
  calculation_elapsed_ms: "decision",
  command_latency_ms: "command latency",
  elapsed_ms: "elapsed",
  latency_ms: "latency",
};

function buildActionCard(title, result, fallbackStatus) {
  const r = result || {};
  const card = el("div", "result-card");
  const head = el("div", "rc-head");
  head.appendChild(verdictBadge(r.verdict || r.status || fallbackStatus, true));
  const meta = el("div", "rc-meta");
  meta.appendChild(el("strong", null, title));
  if (r.status && r.verdict) meta.appendChild(el("span", "rc-dim", ` · ${r.status}`));
  const timings = Object.keys(ELAPSED_LABELS)
    .filter((k) => typeof r[k] === "number")
    .map((k) => `${ELAPSED_LABELS[k]} ${fmtNumber(r[k], 1)} ms`);
  if (timings.length) meta.appendChild(el("span", "rc-ok", ` · ${timings.join(", ")}`));
  head.appendChild(meta);
  card.appendChild(head);
  const sentence = r.causal_sentence || r.rationale || r.message;
  card.appendChild(el("div", "rc-headline", sentence || "No causal sentence returned."));
  if (r.rationale && r.rationale !== sentence) card.appendChild(el("div", "rc-rationale", r.rationale));
  const impacts = (r.guest_impacts || []).filter((gi) => gi && gi.guest_id);
  if (impacts.length) {
    const sec = addSection(card, "Guest impact");
    const ul = el("ul", "rc-list");
    impacts.forEach((gi) => {
      const modes = gi.mode_before && gi.mode_after ? `${gi.mode_before} → ${gi.mode_after}` : gi.mode_after || "";
      const loses = gi.loses ? `, loses ${gi.loses}` : "";
      ul.appendChild(el("li", null, `${gi.guest_id}: ${modes}${loses}`));
    });
    sec.appendChild(ul);
  }
  return card;
}

function renderDecisionLogBox({ card = null, summary = null, entries = [], raw = null, rawLabel = "Raw JSON" } = {}) {
  const box = document.getElementById("decision-log-box");
  if (!box) return;
  box.textContent = "";
  if (card) box.appendChild(card);
  if (summary) box.appendChild(el("div", "timeline-summary", summary));
  box.appendChild(el("div", "rc-section-title", "Decision log (newest first)"));
  box.appendChild(buildTimeline(entries));
  if (raw !== null) box.appendChild(rawJsonDetails(raw, rawLabel));
}

function renderActionResponse(title, payload) {
  const result = payload && (payload.result || payload);
  renderDecisionLogBox({
    card: buildActionCard(title, result, payload && payload.status),
    entries: (payload && payload.decision_log) || (currentStatePayload && currentStatePayload.decision_log) || [],
    raw: payload,
  });
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
  renderOwnRadarBadge(data);
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
  const visibleLog = (currentStatePayload.decision_log || []).filter((d) =>
    typeof d.tick === "number" ? d.tick <= clamped : (d.sim_time_s || 0) <= t.sim_time_s
  );
  const pendingCount = (currentStatePayload.pending_transitions || []).length;
  renderDecisionLogBox({
    summary:
      `${fmtSimTime(t.sim_time_s)} · ${phaseTag} · profile ${replay.priority_profile_id || "?"}` +
      (replay.executed_touchdown_time_s != null ? ` · G4 touchdown T+${replay.executed_touchdown_time_s}s` : "") +
      (pendingCount ? ` · ${pendingCount} pending transition(s)` : ""),
    entries: visibleLog,
    raw: {
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
  });
}

const GENSET_DOWN_LABELS = {
  YES: "SE MANTIENE",
  YES_WITH_STEP_DOWN: "SE MANTIENE CON BAJADA DE MODO",
  NO: "NO SE MANTIENE",
};

function gensetDownLabel(holds) {
  return Object.prototype.hasOwnProperty.call(GENSET_DOWN_LABELS, holds) ? GENSET_DOWN_LABELS[holds] : "n/d";
}

function renderGensetDownLine(payload) {
  const el = document.getElementById("genset-down-line");
  if (!el) return;
  const g = payload && payload.one_genset_down;
  el.textContent = g ? `⚡ Un generador caído: ${gensetDownLabel(g.holds)}. ${g.sentence}` : "";
}

function placementCell(row, text) {
  const td = document.createElement("td");
  td.textContent = text;
  row.appendChild(td);
}

function renderPlacementAlternatives(payload) {
  const box = document.getElementById("placement-alternatives-container");
  if (!box) return;
  box.textContent = "";
  const p = payload && payload.placement;
  if (!p || !p.subcomponent_positions) return;
  const table = document.createElement("table");
  const head = table.createTHead().insertRow();
  ["Estado", "Posición", "Nodo", "Motivo"].forEach((h) => {
    const th = document.createElement("th");
    th.textContent = h;
    head.appendChild(th);
  });
  const body = table.createTBody();
  const fmt = (m) => Object.entries(m || {}).map(([k, v]) => `${k}→${v}`).join(", ");
  const add = (status, positions, node, reason) => {
    const row = body.insertRow();
    [status, fmt(positions), node, reason].forEach((t) => placementCell(row, t));
  };
  add("Elegida", p.subcomponent_positions, p.compute_node, p.objective_id || "");
  (payload.placement_alternatives || []).forEach((a) =>
    add(`Alternativa ${a.rank}`, a.subcomponent_positions, a.compute_node, a.why_not_chosen)
  );
  (payload.rejected_placements || []).forEach((r) =>
    add("Rechazada", r.subcomponent_positions, r.compute_node, r.why_rejected)
  );
  box.appendChild(table);
}

function renderLogicalReconfiguration(payload) {
  const box = document.getElementById("logical-reconfig-container");
  if (!box) return;
  box.textContent = "";
  const lr = payload && payload.logical_reconfiguration;
  if (!lr) return;
  if (!lr.options || lr.options.length === 0) {
    box.textContent = lr.reason || "Sin reconfiguración lógica factible";
    return;
  }
  const table = document.createElement("table");
  const head = table.createTHead().insertRow();
  ["Opción", "Cobertura", "Capacidad perdida", ""].forEach((h) => {
    const th = document.createElement("th");
    th.textContent = h;
    head.appendChild(th);
  });
  const body = table.createTBody();
  lr.options.forEach((o) => {
    const row = body.insertRow();
    [o.summary, `${Math.round(o.coverage * 100)} %`, String(o.capability_lost_elsewhere)].forEach((t) => {
      row.insertCell().textContent = t;
    });
    const btn = document.createElement("button");
    btn.textContent = "Aplicar opción";
    btn.addEventListener("click", async () => {
      const res = await fetch("/api/apply_logical_reconfig", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          option_id: o.option_id,
          state_version: Number(currentStatePayload ? currentStatePayload.state_version : 0),
          candidate_guest_id: payload.candidate_guest_id,
          operator_confirmed: true,
        }),
      });
      const out = await res.json();
      btn.textContent = res.ok ? "Aplicada" : `Rechazada (${out.status || res.status})`;
      btn.disabled = true;
    });
    row.insertCell().appendChild(btn);
  });
  box.appendChild(table);
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
        <td>⚡ ${gensetDownLabel(c.fourth_guest_one_genset_down_holds)}</td>
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
          <th>One genset down</th>
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
        renderDecisionLogBox({
          card: buildActionCard("Custom priority profile rejected", payload, payload.status || "VALIDATION_ERROR"),
          entries: (currentStatePayload && currentStatePayload.decision_log) || [],
          raw: payload,
        });
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

const chkQuietRadio = document.getElementById("chk-quiet-radio");

function renderOwnRadarBadge(payload) {
  const badge = document.getElementById("own-radar-badge");
  const st = payload && payload.state;
  if (chkQuietRadio && st) chkQuietRadio.checked = st.own_radar_policy === "QUIET_RADIO";
  if (!badge || !st) return;
  const g1 = st.capability_effects && st.capability_effects.G1;
  const hit = Array.isArray(g1) && g1.includes("OWN_RADAR_TX_NEARBY");
  badge.textContent = hit ? "📡 −30 % radar (G1: radar propio transmitiendo)" : "";
}

if (chkQuietRadio) {
  chkQuietRadio.addEventListener("change", async (e) => {
    const value = e.target.checked ? "QUIET_RADIO" : "TRANSMIT";
    try {
      const res = await fetch("/api/perturb", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ perturbation_type: "OWN_RADAR_POLICY", value }),
      });
      if (!res.ok) {
        setStaleBanner(true, `HTTP ${res.status} on /api/perturb`);
        return;
      }
      const payload = await res.json();
      setStaleBanner(false);
      if (payload.status === "REJECTED") {
        e.target.checked = false;
        const badge = document.getElementById("own-radar-badge");
        if (badge) badge.textContent = payload.result.causal_sentence;
        return;
      }
      applyAuthoritativeState(payload);
    } catch (err) {
      setStaleBanner(true, err.message || "Network error");
    }
  });
}

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
      renderActionResponse(`Perturbation ${pType}`, payload);
    } catch (err) {
      setStaleBanner(true, err.message || "Network error");
    }
  });
});

let waterLayered = false;
document.getElementById("btn-layered-toggle").addEventListener("click", async () => {
  const prof = document.getElementById("select-priority").value;
  const nextValue = !waterLayered;
  try {
    const res = await fetch("/api/perturb", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        perturbation_type: "WATER_COLUMN_LAYERED",
        value: nextValue,
        priority_profile_id: prof,
        sim_time_s: currentTickIndex * 20,
      }),
    });
    if (!res.ok) {
      setStaleBanner(true, `HTTP ${res.status} on /api/perturb`);
      return;
    }
    const payload = await res.json();
    setStaleBanner(false);
    waterLayered = nextValue;
    const toggle = document.getElementById("btn-layered-toggle");
    toggle.textContent = `Agua estratificada (A.5): ${waterLayered ? "ON" : "OFF"}`;
    toggle.setAttribute("aria-pressed", waterLayered ? "true" : "false");
    const link = (payload.result || {}).acoustic_link_status || {};
    const causes = Array.isArray(link.causes) ? link.causes.join(", ") : "";
    document.getElementById("acoustic-link-indicator").textContent = link.available
      ? "Enlace acústico G3: OK"
      : `Enlace acústico G3: CAÍDO (${causes}) — hold autónomo por timeout`;
    await fetchConsoleState(prof);
    renderActionResponse(`Water column layered: ${waterLayered ? "ON" : "OFF"}`, payload);
  } catch (err) {
    setStaleBanner(true, err.message || "Network error");
  }
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
      if (res.status >= 400 && res.status < 500) {
        let errPayload = null;
        try {
          errPayload = await res.json();
        } catch (_) {
          errPayload = { status: `HTTP ${res.status}` };
        }
        renderAdmissionResult(errPayload, res.status);
        return;
      }
      setStaleBanner(true, `HTTP ${res.status} on /api/admit_live`);
      return;
    }
    const payload = await res.json();
    setStaleBanner(false);
    renderAdmissionResult(payload);
    renderGensetDownLine(payload);
    renderPlacementAlternatives(payload);
    renderLogicalReconfiguration(payload);
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

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
  return el;
}

function fmtNum(v, digits, unit) {
  return v !== null && v !== undefined && Number.isFinite(Number(v)) ? `${Number(v).toFixed(digits)} ${unit}` : "n/a";
}

function renderEnvironmentData(data) {
  if (!data || !data.available) {
    renderEnvironmentFallback(data ? `${data.reason || "NO_ACTIVE_SNAPSHOT"}${data.detail ? " — " + data.detail : ""}` : "No data");
    return;
  }
  setText("env-snapshot-label",
    `Recorded public data — snapshot ${data.snapshot_id} (anchor ${data.anchor_utc ? data.anchor_utc.slice(0, 16) + "Z" : "n/a"})`);
  const cur = data.current || {};
  const gb = data.guard_bands || {};
  const sstVal = fmtNum(cur.sst_c, 1, "°C");
  const cool = data.cooling || {};
  const coolEff = cool.effective_w !== null && cool.effective_w !== undefined ? fmtNum(cool.effective_w / 1000, 1, "kW") : "n/a";
  setText("env-sst-val", sstVal + (cool.sst_fallback ? ` (fallback ${cool.sst_fallback})` : ""));
  setText("env-cooling-eff", coolEff);
  setText("env-val-sst", sstVal);
  setText("env-sub-sst", `${cur.provenance || "UNKNOWN"} ${cur.source_id || ""} | lead ${cur.lead_s ?? "n/a"} s`);

  const gust = cur.gust_kn !== null && cur.gust_kn !== undefined ? ` (Gust ${Number(cur.gust_kn).toFixed(1)} kn)` : "";
  setText("env-val-wind", `${fmtNum(cur.wind_kn, 1, "kn")}${gust}`);
  setText("env-sub-wind", `Guard band ${gb.status || "n/a"}: +${gb.guard_wind_kn ?? "n/a"} kn`);
  const ss = cur.sea_state_wmo !== null && cur.sea_state_wmo !== undefined ? ` (Sea State ${cur.sea_state_wmo})` : "";
  setText("env-val-hs", `${fmtNum(cur.hs_m, 2, "m")}${ss}`);
  setText("env-sub-hs", `WMO 3700 | Guard band ${gb.status || "n/a"}: +${gb.guard_hs_m ?? "n/a"} m`);

  const ribbon = document.getElementById("outlook-ribbon-cells");
  if (ribbon && Array.isArray(data.outlook_48h)) {
    ribbon.replaceChildren();
    data.outlook_48h.forEach((cell) => {
      const el = document.createElement("div");
      el.className = "outlook-cell unavail";
      el.dataset.simTimeS = String(cell.sim_time_s);
      el.title = `${cell.t_utc || ""}: Hs ${cell.wave_hs_m ?? "n/a"} m (SS ${cell.sea_state ?? "n/a"}), wind ${cell.wind_kn ?? "n/a"} kn, SST ${cell.sst_c ?? "n/a"} °C — awaiting guarded window evaluation`;
      ribbon.appendChild(el);
    });
  }
  fetchWindowsForGuest("G4", "VEHICLE_REPLACEMENT_AT_SEA");
}

function renderWindowsData(winData) {
  if (!winData) return;
  const elWin = document.getElementById("env-val-window");
  if (!winData.available) {
    setText("env-val-window", `UNAVAILABLE (${winData.reason || "no snapshot"})`);
    return;
  }
  if (elWin) {
    if (winData.permitted_now) {
      elWin.textContent = "PERMITTED now";
      elWin.style.color = "var(--ok)";
    } else {
      const reason = (winData.blocking_reasons_now && winData.blocking_reasons_now[0]) || "not permitted";
      elWin.textContent = "NOT_NOW";
      elWin.title = (winData.blocking_reasons_now || []).join("\n") || reason;
      elWin.style.color = "var(--warn)";
    }
  }
  const nw = winData.next_window || {};
  if (nw.start_s !== null && nw.start_s !== undefined) {
    setText("env-sub-window", `Next window: T+${nw.start_s}s (${nw.start_utc ? nw.start_utc.slice(11, 16) + "Z" : ""}), min ${Math.round((winData.min_window_s || 0) / 60)} min`);
  } else {
    setText("env-sub-window", nw.reason ? `No window in 48 h: ${nw.reason}` : "G4 At-Sea Replacement (A')");
  }
  const ribbon = document.getElementById("outlook-ribbon-cells");
  if (ribbon && Array.isArray(winData.timeline)) {
    const bySim = new Map(winData.timeline.map((r) => [String(r.sim_time_s), r]));
    ribbon.querySelectorAll(".outlook-cell").forEach((el) => {
      const row = bySim.get(el.dataset.simTimeS);
      if (!row) return;
      const reasons = (row.reasons || []).join("; ");
      el.className = `outlook-cell ${row.permitted ? "open" : "closed"}`;
      el.title = `${row.t_utc}: guarded SS ${row.sea_state_guarded ?? "n/a"}, guarded wind ${row.wind_kn_guarded ?? "n/a"} kn -> ${row.permitted ? "OPEN" : "CLOSED"}${reasons ? " (" + reasons + ")" : ""}`;
    });
  }
}

function renderEnvironmentFallback(reason = "Snapshot offline") {
  setText("env-snapshot-label", `Snapshot unavailable: ${reason} (A.10 30 °C fallback active)`);
  ["env-sst-val", "env-cooling-eff", "env-val-sst", "env-val-wind", "env-val-hs", "env-val-window"].forEach((id) => setText(id, "n/a"));
  ["env-sub-sst", "env-sub-wind", "env-sub-hs"].forEach((id) => setText(id, "no validated public data"));
  const ribbon = document.getElementById("outlook-ribbon-cells");
  if (ribbon) ribbon.replaceChildren();
}

function renderForecastData(data) {
  const chip = document.getElementById("forecast-status-chip");
  const guestContainer = document.getElementById("forecast-guest-cards");

  if (!data || !data.enabled) {
    renderForecastFallback(data && data.reason ? data.reason : "Observer disabled");
    return;
  }
  const prov = data.telemetry_provenance || "UNKNOWN";
  setText("forecast-source-label",
    `source: ${data.source || "n/a"} | telemetry ${prov} | as of T+${data.as_of_sim_time_s ?? "n/a"} s`);

  const states = data.status_by_guest || {};
  const rank = { BREACH_OBSERVED: 4, WARN: 3, WATCH: 2, SUPPRESSED: 1, QUIET: 0 };
  let worst = "QUIET";
  Object.values(states).forEach((s) => { if ((rank[s] ?? 0) > (rank[worst] ?? 0)) worst = s; });
  if (chip) {
    chip.textContent = worst;
    chip.className = `pill ${worst === "WARN" || worst === "BREACH_OBSERVED" ? "pill-warn" : worst === "WATCH" ? "pill-watch" : "pill-quiet"}`;
  }

  const alerts = Array.isArray(data.alerts) ? data.alerts.slice() : [];
  alerts.sort((a, b) => (b.sim_time_s ?? 0) - (a.sim_time_s ?? 0));
  const latest = alerts[0] || null;
  if (latest) {
    const band = Array.isArray(latest.band_s) ? `${latest.band_s[0]}–${latest.band_s[1]} s` : "n/a";
    setText("forecast-lead-badge", `Latest WARN at T+${latest.sim_time_s} s (${latest.guest_id}): h* band ${band}`);
    setText("forecast-sentence-box", `[${prov} telemetry replay] ${latest.sentence}`);
  } else {
    setText("forecast-lead-badge", "No WARN in replay");
    setText("forecast-sentence-box", `No advisory raised in ${data.source || "replay"} (${prov} telemetry).`);
  }

  if (guestContainer && data.series) {
    guestContainer.replaceChildren();
    Object.entries(data.series).forEach(([gid, sData]) => {
      const card = document.createElement("div");
      card.className = "forecast-guest-card";
      const lastY = sData.y && sData.y.length ? (sData.y[sData.y.length - 1] / 1000).toFixed(1) : "n/a";
      const strong = document.createElement("strong");
      strong.textContent = gid;
      card.appendChild(strong);
      card.appendChild(document.createTextNode(` ${lastY} kW [${states[gid] || "n/a"}]`));
      guestContainer.appendChild(card);
    });
  }
}

function renderForecastFallback(reason = "Observer unavailable") {
  const chip = document.getElementById("forecast-status-chip");
  if (chip) {
    chip.textContent = "UNAVAILABLE";
    chip.className = "pill pill-quiet";
  }
  setText("forecast-lead-badge", "Advance Warning: n/a");
  setText("forecast-source-label", "source: none");
  setText("forecast-sentence-box", `Predictive observer unavailable: ${reason}. Governor operates under its reactive rules (unchanged).`);
  const gc = document.getElementById("forecast-guest-cards");
  if (gc) gc.replaceChildren();
}

fetchConsoleState("survey_primary");
fetchEnvironmentState();
fetchForecastState();
setInterval(() => {
  fetchEnvironmentState();
  fetchForecastState();
}, 3000);

