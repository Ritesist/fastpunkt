"""Snapshot store and refresh: idempotent, history-preserving change detection.

store/<orgnr>/<snapshot_id>.json   one file per distinct profile state (never overwritten or deleted)
store/<orgnr>/history.jsonl        append-only log of runs: run id, snapshot id, changes
A snapshot id is the hash of the normalised (field, value) content, so re-running on unchanged sources
produces the same id, writes nothing new and reports no changes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MATERIAL = ["legal_name", "legal_form", "business_address", "postal_address", "industry", "registered_employees", "bankrupt",
            "under_liquidation", "under_forced_dissolution", "parent_entity", "share_capital", "ceo", "board_chair", "board_member",
            "auditor", "registry_website", "registry_email", "registry_phone", "latest_submitted_accounts_year", "revenue",
            "operating_result", "net_result", "equity", "registered_workplace_count", "official_website", "social_profile",
            "careers_page", "job_postings_count", "job_posting", "dated_activity", "business_description", "deleted_date"]


def _canon(v) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False)


def content_map(env: dict) -> dict[str, list]:
    m: dict[str, set] = {}
    for c in env.get("claims", []):
        if c["availability"] == "available":
            key = c["field"] + (f"@{c['reporting_period']}" if c.get("reporting_period") else "")
            m.setdefault(key, set()).add(_canon(c["value"]))
    return {k: sorted(v) for k, v in sorted(m.items())}


def snapshot_id(env: dict) -> str:
    return hashlib.sha256(_canon(content_map(env)).encode()).hexdigest()[:16]


def diff(prev: dict, cur: dict, prev_meta: dict, detected_at: str) -> list[dict]:
    a, b = content_map(prev), content_map(cur)
    changes = []
    for key in sorted(set(a) | set(b)):
        field = key.split("@")[0]
        if field not in MATERIAL:
            continue
        before, after = set(a.get(key, [])), set(b.get(key, []))
        if before == after:
            continue
        kind = "added" if not before else "removed" if not after else "changed"
        cur_ev = [c["evidence_ids"] for c in cur.get("claims", []) if c["field"] == field and c["availability"] == "available"]
        changes.append({"field": field, "key": key, "kind": kind,
                        "before": [json.loads(x) for x in sorted(before)], "after": [json.loads(x) for x in sorted(after)],
                        "previous_snapshot": prev_meta.get("snapshot_id"), "previous_run": prev_meta.get("run_id"),
                        "evidence_ids": sorted({e for ids in cur_ev for e in ids}), "detected_at": detected_at})
    return changes


class Store:
    def __init__(self, root):
        self.root = Path(root)

    def _dir(self, orgnr):
        d = self.root / orgnr
        d.mkdir(parents=True, exist_ok=True)
        return d

    def latest(self, orgnr):
        h = self.root / orgnr / "history.jsonl"
        if not h.exists():
            return None, None
        last = json.loads(h.read_text().strip().splitlines()[-1])
        snap = self.root / orgnr / f"{last['snapshot_id']}.json"
        return (json.loads(snap.read_text()) if snap.exists() else None), last

    def commit(self, env: dict) -> dict:
        """Attach refresh metadata and changes to `env`, persist it, and return the refresh record."""
        orgnr = env["organisation_number"]
        sid = snapshot_id(env)
        prev, meta = self.latest(orgnr)
        detected = env["run"]["completed_at"]
        if prev is None:
            changes, status = [], "first_run"
        elif meta["snapshot_id"] == sid:
            changes, status = [], "unchanged"
        else:
            # Only judge fields both runs could observe; a section that failed this time is not a removal.
            ok_now = {k for k, v in env["sections"].items() if v["state"] == "available"}
            ok_before = {k for k, v in prev["sections"].items() if v["state"] == "available"}
            changes = [c for c in diff(prev, env, meta, detected)
                       if _section_of(prev, env, c["field"]) in (ok_now & ok_before) or c["kind"] == "added"]
            status = "changed" if changes else "unchanged_material"
        env["changes"] = changes
        env["refresh"] = {"snapshot_id": sid, "previous_snapshot_id": meta["snapshot_id"] if meta else None,
                          "previous_run_id": meta["run_id"] if meta else None, "status": status,
                          "material_changes": len(changes), "history_preserved": True}
        d = self._dir(orgnr)
        snap = d / f"{sid}.json"
        if not snap.exists():
            snap.write_text(json.dumps(env, ensure_ascii=False))
        if not (meta and meta["snapshot_id"] == sid and meta["run_id"] == env["run"]["run_id"]):
            with (d / "history.jsonl").open("a") as fh:
                fh.write(json.dumps({"run_id": env["run"]["run_id"], "completed_at": detected, "snapshot_id": sid,
                                     "status": status, "changes": [c["key"] for c in changes]}) + "\n")
        return env["refresh"]


def _section_of(prev, cur, field):
    for env in (cur, prev):
        for c in env.get("claims", []):
            if c["field"] == field:
                return c["section"]
    return None
