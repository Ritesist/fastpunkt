"""End-to-end on recorded real responses (offline): contract, refresh idempotency and change detection."""
import json
import shutil
import gzip
import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastpunkt.runner import run
from fastpunkt.claims import STATES

FX = Path(__file__).parent / "fixtures"


def args(tmp, replay, run_id, store):
    return SimpleNamespace(organisations=str(FX / "replay-batch.txt"), output=str(tmp / f"{run_id}.jsonl"), report=str(tmp / f"{run_id}.json"),
                           store=str(store), run_id=run_id, max_requests=500, time_budget_min=10, per_company_cap=14, workers=4,
                           max_typesafe_calls=100, no_typesafe=False, shallow=False, record=None, replay=str(replay))


def envs(p):
    return [json.loads(l) for l in Path(p).read_text().splitlines()]


@pytest.fixture
def first(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    rep = run(args(tmp_path, FX / "replay", "r1", tmp_path / "store"))
    return tmp_path, rep, envs(tmp_path / "r1.jsonl")


def test_one_terminal_envelope_per_input(first):
    tmp, rep, E = first
    lines = [l for l in (FX / "replay-batch.txt").read_text().splitlines() if l.strip()]
    assert len(E) == len(lines) and rep["one_envelope_per_input"]
    for e, raw in zip(E, lines):
        assert e["organisation_number"] == raw and e["run"]["terminal_status"] == "completed"
        for k in ("claims", "evidence", "changes", "errors", "operations", "sections", "summary"):
            assert k in e
        assert all(s["state"] in STATES for s in e["sections"].values())


def test_every_available_claim_has_resolvable_evidence(first):
    _, rep, E = first
    assert rep["available_claims_without_evidence"] == 0
    for e in E:
        ids = {x["id"] for x in e["evidence"]}
        for c in e["claims"]:
            assert c["availability"] in STATES
            if c["availability"] == "available":
                assert c["evidence_ids"] and set(c["evidence_ids"]) <= ids
            else:
                assert c["value"] is None  # absence is never a zero
        for x in e["evidence"]:
            assert x["source_url"].startswith("https://") and x["retrieved_at"] and x["content_sha256"]


def test_invalid_number_gets_explicit_state(first):
    E = first[2]
    bad = [e for e in E if e["organisation_number"] == "123456789"][0]
    assert all(s["state"] == "not_applicable" for s in bad["sections"].values()) and not bad["claims"]


def test_accounts_carry_reporting_periods_and_no_birth_dates(first):
    E = first[2]
    for e in E:
        for c in e["claims"]:
            if c["section"] == "annual_accounts" and c["availability"] == "available":
                assert c["reporting_period"]
        assert "fodselsdato" not in json.dumps(e)


def test_website_published_only_with_identity_evidence(first):
    for e in first[2]:
        for c in e["claims"]:
            if c["field"] == "official_website" and c["availability"] == "available":
                assert c["verification"] in ("orgnr_on_site", "registry_listed", "name_match_and_typesafe_identity")
        for cand in e["website_candidates"]:
            if cand.get("status") == "ambiguous":
                assert not any(c["field"] == "official_website" and c["value"] == cand.get("final_url") for c in e["claims"])


def test_refresh_is_idempotent_and_preserves_history(first, tmp_path):
    tmp, _, _ = first
    rep2 = run(args(tmp, FX / "replay", "r2", tmp / "store"))
    assert rep2["material_changes"] == 0 and rep2["refresh_status"]["changed"] == 0
    for d in (tmp / "store").iterdir():
        hist = (d / "history.jsonl").read_text().splitlines()
        assert len(hist) == 2 and len(list(d.glob("*.json"))) == 1  # same snapshot, two runs logged


def test_refresh_detects_material_change(first, tmp_path):
    tmp, _, _ = first
    changed = tmp / "replay-changed"
    shutil.copytree(FX / "replay", changed)
    for f in changed.glob("*.json"):
        d = json.loads(f.read_text())
        if d.get("url", "").endswith("/810034882/roller"):
            body = json.loads(gzip.decompress(base64.b64decode(d["body_b64gz"])))
            for g in body["rollegrupper"]:
                for r in g["roller"]:
                    if r["type"]["kode"] == "DAGL":
                        r["person"]["navn"] = {"fornavn": "Kari", "etternavn": "Nordmann"}
            d["body_b64gz"] = base64.b64encode(gzip.compress(json.dumps(body).encode(), mtime=0)).decode()
            f.write_text(json.dumps(d))
    rep = run(args(tmp, changed, "r3", tmp / "store"))
    e = [x for x in envs(tmp / "r3.jsonl") if x["organisation_number"] == "810034882"][0]
    ch = [c for c in e["changes"] if c["field"] == "ceo"]
    assert ch and ch[0]["after"] == ["Kari Nordmann"] and ch[0]["kind"] == "changed" and ch[0]["evidence_ids"]
    assert e["refresh"]["previous_snapshot_id"] and e["refresh"]["status"] == "changed"
    assert len(list((tmp / "store" / "810034882").glob("*.json"))) == 2  # old snapshot kept
    assert rep["refresh_status"]["changed"] == 1


def test_brand_or_group_site_is_labelled_not_collapsed(first):
    """APPELX AS (914561302): the registry lists a site that shows another organisation number.
    The link is published with a relationship label; the site's own content is not attributed."""
    e = next(x for x in first[2] if x["organisation_number"] == "914561302")
    site = [c for c in e["claims"] if c["field"] == "official_website" and c["availability"] == "available"]
    assert site and site[0]["verification"] == "registry_listed" and "group or brand" in site[0]["relationship"]
    for fld in ("business_description", "social_profile", "website_email", "website_phone"):
        assert not any(c["field"] == fld and c["availability"] == "available" for c in e["claims"])
    assert e["sections"]["hiring_activity"]["state"] == "ambiguous"
    assert any("not attributed" in s["text"] for s in e["summary"]["sentences"])
