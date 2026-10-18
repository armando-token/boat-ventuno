/* viz3d/js/fmt.js — formatting + jury-readable sentence helpers (adapted from ui/app.js renderers).
   Every string coming from the server goes through esc() before reaching innerHTML. */
(function () {
  'use strict';
  const SDV = (window.SDV = window.SDV || {});
  const F = (SDV.fmt = {});

  F.esc = function (v) {
    return String(v === undefined || v === null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  };
  F.num = function (v, d) {
    if (typeof v !== 'number' || !isFinite(v)) return '—';
    return v.toLocaleString('en-US', { maximumFractionDigits: d === undefined ? 1 : d, minimumFractionDigits: 0 });
  };
  F.fix = function (v, d) {
    if (typeof v !== 'number' || !isFinite(v)) return '—';
    return v.toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: d });
  };
  F.T = function (s) {
    if (typeof s !== 'number' || !isFinite(s)) return 'T+—';
    s = Math.max(0, Math.round(s));
    return 'T+' + String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
  };

  F.RESOURCE_LABELS = {
    power: 'Power reserve', cooling: 'Chilled-water cooling', mass_stability: 'Guest mass / stability',
    positions: 'Deck / bay position', data: 'SATCOM / data link', compute: 'Compute', time: 'Install window',
    crew: 'Crew', dependencies: 'Dependency', environment: 'Sea / weather', mast: 'Mast'
  };
  const NON_NUMERIC_UNITS = { slot: 1, slots: 1, window: 1, clearance: 1, condition: 1, conflict: 1, mode_conflict: 1, guest: 1, '': 1 };

  F.qty = function (value, unit) {
    if (typeof value !== 'number' || !isFinite(value)) return String(value === undefined || value === null ? '?' : value) + ' ' + (unit || '');
    switch (unit) {
      case 'W': return Math.abs(value) >= 1000 ? F.num(value / 1000) + ' kW' : F.num(value, 0) + ' W';
      case 'bps':
        if (Math.abs(value) >= 1e6) return F.num(value / 1e6, 2) + ' Mbit/s';
        if (Math.abs(value) >= 1e3) return F.num(value / 1e3, 1) + ' kbit/s';
        return F.num(value, 0) + ' bit/s';
      case 'kg': return F.num(value, 0) + ' kg';
      case 'kg_eq': return F.num(value, 0) + ' kg-eq';
      case 'degC': return F.num(value) + ' °C';
      default: return (F.num(value, 2) + ' ' + (unit || '')).trim();
    }
  };
  F.isNumericConstraint = function (bc) {
    return bc && typeof bc.demand === 'number' && typeof bc.capacity === 'number' && !NON_NUMERIC_UNITS[bc.unit || ''];
  };
  F.plainConstraint = function (bc) {
    if (!bc) return '';
    const label = F.RESOURCE_LABELS[bc.resource_class] || bc.resource_class || bc.constraint_id || 'Constraint';
    if (!F.isNumericConstraint(bc)) return label + ': ' + (bc.detail || bc.constraint_id || 'blocked');
    const short = typeof bc.margin === 'number' ? Math.abs(bc.margin) : bc.demand - bc.capacity;
    return label + ' has ' + F.qty(bc.capacity, bc.unit) + ' available, needs ' + F.qty(bc.demand, bc.unit) + ' (' + F.qty(short, bc.unit) + ' short)';
  };
  F.primaryBinding = function (list) {
    if (!Array.isArray(list) || !list.length) return null;
    return list.find(F.isNumericConstraint) || list[0];
  };
  F.stripAdmissionPrefix = function (s) {
    if (!s) return '';
    const m = String(s).match(/->\s*[A-Z_]+:\s*(.*)$/);
    return m ? m[1] : String(s);
  };
  F.describeAction = function (rd) {
    const ids = (rd.displace_guest_ids || []).join(', ') || '?';
    switch (rd.action_type) {
      case 'REMOVE_AT_PORT': return 'remove ' + ids + ' at port';
      case 'DEACTIVATE_AND_REPLACE': return 'switch off and replace ' + ids;
      default: return String(rd.action_type || 'displace').toLowerCase().replace(/_/g, ' ') + ' ' + ids;
    }
  };
  F.admissionHeadline = function (payload) {
    const dec = (payload && payload.decision) || {};
    const explicit = payload.headline || dec.headline || dec.plain_sentence;
    if (explicit) return String(explicit);
    const verdict = dec.verdict || '';
    const cand = dec.candidate_guest_id || payload.candidate_guest_id || 'candidate';
    const bc = F.primaryBinding(dec.binding_constraints);
    const why = bc ? F.plainConstraint(bc) : F.stripAdmissionPrefix(dec.causal_sentence || payload.causal_sentence);
    if (verdict === 'YES') return 'Admitted: ' + cand + ' fits — ' + (F.stripAdmissionPrefix(dec.causal_sentence) || 'all resource classes feasible');
    if (verdict === 'YES_IF') {
      const first = (dec.ranked_displacements || [])[0];
      const cond = first ? F.describeAction(first) : (dec.required_conditions || [])[0];
      return cond ? 'Yes, if we ' + cond + ': ' + why + '.' : 'Yes, conditionally: ' + why + '.';
    }
    if (verdict === 'NOT_NOW') {
      const nw = dec.next_window;
      return 'Not now: ' + why + '.' + (nw && typeof nw.start_s === 'number' ? ' Next window opens at ' + F.T(nw.start_s) + '.' : '');
    }
    if (verdict === 'NO') return 'Refused: ' + why + '.';
    return why || 'No verdict returned.';
  };

  const ADMISSION_SENTENCE_RE = /^Admission of (\S+) \(([^)]*)\) -> ([A-Z_]+): \[([a-z_]+)\.([^\]]+)\] demand=(-?[\d.]+) (\S+) vs capacity=(-?[\d.]+) \S+ \(margin=(-?[\d.]+) \S+\)(?: — (.*?))?\.?$/;
  const VERDICT_PREFIX = { NO: 'refused', NOT_NOW: 'not now', YES_IF: 'yes, conditionally', YES: 'admitted' };
  F.plainEntrySentence = function (entry) {
    const raw = entry.causal_sentence || entry.rationale || (entry.decision && (entry.decision.causal_sentence || entry.decision.rationale));
    if (!raw) return '(no causal sentence)';
    const m = String(raw).match(ADMISSION_SENTENCE_RE);
    if (!m) return String(raw);
    const bc = { resource_class: m[4], constraint_id: m[5], demand: Number(m[6]), unit: m[7], capacity: Number(m[8]), margin: Number(m[9]), detail: m[10] };
    return m[1] + ' (' + m[2] + ') ' + (VERDICT_PREFIX[m[3]] || m[3]) + ': ' + F.plainConstraint(bc) + '.';
  };

  F.badge = function (verdict, big) {
    const v = String(verdict || '—');
    return '<span class="vbadge v-' + F.esc(v) + (big ? ' big' : '') + '">' + F.esc(v.replace(/_/g, ' ')) + '</span>';
  };
  F.GENSET = { YES: 'holds', YES_WITH_STEP_DOWN: 'holds with a step-down', NO: 'does not hold' };
  F.rawDetails = function (obj, label) {
    return '<details class="raw"><summary>' + F.esc(label || 'Raw JSON') + '</summary><pre>' + F.esc(JSON.stringify(obj, null, 2)) + '</pre></details>';
  };
})();
