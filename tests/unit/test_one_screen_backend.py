"""Backend support for the single operator screen: jury card as YAML, cached Scenario A table."""
from pathlib import Path

import yaml

from sdv_governor import server

ROOT = Path(__file__).resolve().parents[2]


def _session():
    return server.GovernorSession(ROOT)


def test_admit_live_accepts_jury_card_as_yaml_text():
    sess = _session()
    card_text = (ROOT / "docs" / "examples" / "G7_xbt_launcher.yaml").read_text(encoding="utf-8")
    body = {"installed_guest_ids": ["G1", "G2", "G5"], "candidate_manifest_yaml": card_text, "apply_if_admitted": False}
    code, payload = server.dispatch_api_request(ROOT, "POST", "/api/admit_live", body, session=sess)
    assert code == 200, payload
    as_obj = dict(body)
    as_obj.pop("candidate_manifest_yaml")
    as_obj["candidate_manifest"] = yaml.safe_load(card_text)
    code2, payload2 = server.dispatch_api_request(ROOT, "POST", "/api/admit_live", as_obj, session=_session())
    assert code2 == 200
    assert payload["decision"]["verdict"] == payload2["decision"]["verdict"]
    assert payload["decision"]["headline"]


def test_admit_live_rejects_invalid_yaml_card():
    code, payload = server.dispatch_api_request(
        ROOT, "POST", "/api/admit_live",
        {"installed_guest_ids": ["G1"], "candidate_manifest_yaml": "id: [unclosed", "apply_if_admitted": False},
        session=_session(),
    )
    assert code in (400, 422)
    assert "YAML" in str(payload)


def test_state_scenario_a_table_cached_and_identical():
    sess = _session()
    _, first = server.dispatch_api_request(ROOT, "GET", "/api/state?priority_profile=survey_primary", session=sess)
    _, second = server.dispatch_api_request(ROOT, "GET", "/api/state?priority_profile=survey_primary", session=sess)
    assert first["scenario_A_combinations"] == second["scenario_A_combinations"]
    assert len(getattr(sess, "_combos_cache", {})) >= 1


def test_jury_generator_offline_changes_verdict_over_whole_envelope():
    base = {"installed_guest_ids": ["G1", "G2", "G5"], "candidate_guest_id": "G4", "apply_if_admitted": False}
    _, nominal = server.dispatch_api_request(ROOT, "POST", "/api/admit_live", base, session=_session())
    _, one_gen = server.dispatch_api_request(
        ROOT, "POST", "/api/admit_live", dict(base, power_reserve_w=100000), session=_session()
    )
    assert "jury_ship_overrides" not in nominal["decision"]
    ov = one_gen["decision"]["jury_ship_overrides"]
    assert ov["power_reserve_w"] == 100000 and "A.10" in ov["note"]
    assert "power" in one_gen["decision"]["headline"]
    assert one_gen["decision"]["headline"] != nominal["decision"]["headline"]
