"""Batch runner: one terminal envelope per input line, within a request, time and cost budget."""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import __version__, brreg
from . import orgnr as on
from .agent import SECTIONS, research
from .fetch import BudgetExhausted, Fetcher, utcnow
from .judge import MODEL, Judge
from .store import Store


def read_batch(path: str) -> list[str]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            d = json.loads(line)
            out.append(str(d.get("organisation_number") or d.get("orgnr") or d.get("organisasjonsnummer") or ""))
        else:
            out.append(line)
    return out


def failed_envelope(raw: str, run_id: str, reason: str, state="failed") -> dict:
    now = utcnow()
    return {"organisation_number": on.normalise(raw) or raw, "input": raw,
            "run": {"run_id": run_id, "started_at": now, "completed_at": now, "terminal_status": "completed"},
            "legal_identity": {}, "sections": {s: {"state": state, "reason": reason} for s in SECTIONS},
            "summary": {"sentences": [], "text": "", "unknowns": [{"state": state, "reason": reason}]},
            "signals": [], "claims": [], "evidence": [], "website_candidates": [], "changes": [],
            "errors": [{"error": state, "message": reason}],
            "operations": {"requests": 0, "runtime_ms": 0, "typesafe_input_tokens": 0, "third_party_cost_usd": 0}}


def git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, stderr=subprocess.DEVNULL).decode().strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def run(args) -> dict:
    t0 = time.time()
    raw = read_batch(args.organisations)
    run_id = args.run_id or time.strftime("run-%Y%m%dT%H%M%S")
    deadline = t0 + args.time_budget_min * 60 - 45
    f = Fetcher(max_requests=args.max_requests, deadline=deadline,
                record_dir=Path(args.record) if args.record else None, replay_dir=Path(args.replay) if args.replay else None)
    judge = (Judge(max_calls=args.max_typesafe_calls, record_dir=args.record, replay_dir=args.replay)
             if not args.no_typesafe else Judge(api_key=""))
    store = Store(args.store) if args.store else None

    valid = sorted({on.normalise(x) for x in raw if on.is_valid(on.normalise(x))})
    hits = {}
    try:
        hits = brreg.fetch_entities(f, valid)
    except BudgetExhausted:
        pass
    except Exception as e:  # noqa: BLE001 - fall back to single lookups
        print(f"batch lookup failed: {e}", file=sys.stderr)
    # Spread what is left evenly; at 2 requests per company the registry sections still fit.
    cap = max(2, min(args.per_company_cap, (f.remaining()) // max(1, len(valid))))

    def one(o):
        try:
            return research(o, hits.get(o), f, judge, cap, run_id, deep=not args.shallow)
        except BudgetExhausted as e:
            return failed_envelope(o, run_id, f"budget exhausted before research: {e}")
        except Exception as e:  # noqa: BLE001
            return failed_envelope(o, run_id, f"{type(e).__name__}: {e}")

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = dict(zip(valid, ex.map(one, valid)))

    envelopes = []
    for x in raw:
        o = on.normalise(x)
        if o in results:
            env = json.loads(json.dumps(results[o]))  # one independent envelope per input line
        else:
            env = failed_envelope(x, run_id, "Invalid Norwegian organisation number (MOD11 check failed).", state="not_applicable")
        if store and env.get("claims"):
            store.commit(env)
        elif "refresh" not in env:
            env["refresh"] = {"status": "not_stored"}
        envelopes.append(env)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for env in envelopes:
            fh.write(json.dumps(env, ensure_ascii=False) + "\n")

    rt = [e["operations"]["runtime_ms"] for e in envelopes if e["operations"]["runtime_ms"]]
    states = {s: {} for s in SECTIONS}
    for e in envelopes:
        for s in SECTIONS:
            st = e["sections"][s]["state"]
            states[s][st] = states[s].get(st, 0) + 1
    field_cov = {}
    for e in envelopes:
        for fld in {c["field"] for c in e["claims"] if c["availability"] == "available"}:
            field_cov[fld] = field_cov.get(fld, 0) + 1
    verif = {}
    for e in envelopes:
        for c in e["claims"]:
            if c["field"] == "official_website" and c["availability"] == "available":
                verif[c["verification"]] = verif.get(c["verification"], 0) + 1
    report = {
        "agent": "Fastpunkt", "version": __version__, "commit": git_commit(), "run_id": run_id,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)), "completed_at": utcnow(),
        "inputs": len(raw), "envelopes": len(envelopes), "one_envelope_per_input": len(raw) == len(envelopes),
        "valid_organisation_numbers": len(valid), "terminal_status_completed": sum(e["run"]["terminal_status"] == "completed" for e in envelopes),
        "requests": f.requests_made, "request_budget": args.max_requests, "per_company_cap": cap,
        "runtime_s": round(time.time() - t0, 1), "time_budget_min": args.time_budget_min,
        "company_runtime_ms_p50": int(statistics.median(rt)) if rt else None,
        "company_runtime_ms_p95": int(sorted(rt)[int(0.95 * (len(rt) - 1))]) if rt else None,
        "typesafe": {"enabled": judge.enabled, "model": MODEL, "calls": judge.calls, "input_tokens": judge.input_tokens,
                     "errors": judge.errors, "cost_usd": judge.cost_usd},
        "third_party_cost_usd": judge.cost_usd,
        "section_states": states, "companies_with_field": dict(sorted(field_cov.items(), key=lambda kv: -kv[1])),
        "website_verification": verif,
        "claims_total": sum(len(e["claims"]) for e in envelopes),
        "available_claims_without_evidence": sum(1 for e in envelopes for c in e["claims"] if c["availability"] == "available" and not c["evidence_ids"]),
        "refresh_status": {k: sum(1 for e in envelopes if e.get("refresh", {}).get("status") == k)
                           for k in ("first_run", "unchanged", "unchanged_material", "changed", "not_stored")},
        "material_changes": sum(len(e["changes"]) for e in envelopes),
        "replay": bool(args.replay),
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=1, ensure_ascii=False))
    return report


def main(argv=None):
    p = argparse.ArgumentParser(prog="fastpunkt", description="Research Norwegian companies from organisation numbers.")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="research a JSONL/plain list of organisation numbers")
    r.add_argument("--organisations", required=True)
    r.add_argument("--output", required=True, help="envelopes JSONL (one per input line)")
    r.add_argument("--report", required=True, help="machine-readable run report JSON")
    r.add_argument("--store", default="store", help="snapshot store for refresh/change detection")
    r.add_argument("--run-id")
    r.add_argument("--max-requests", type=int, default=2000)
    r.add_argument("--time-budget-min", type=float, default=45)
    r.add_argument("--per-company-cap", type=int, default=14)
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--max-typesafe-calls", type=int, default=3000)
    r.add_argument("--no-typesafe", action="store_true", help="keyword fallbacks only; unverifiable sites stay unpublished")
    r.add_argument("--shallow", action="store_true", help="homepage only: skip careers/news pages and subunits")
    r.add_argument("--record", help="save every HTTP response here (for replay)")
    r.add_argument("--replay", help="answer HTTP requests from a recorded directory (offline, deterministic)")
    v = sub.add_parser("render", help="build the static, mobile-friendly profile viewer")
    v.add_argument("--input", required=True)
    v.add_argument("--output", required=True)
    a = p.parse_args(argv)
    if a.cmd == "run":
        rep = run(a)
        print(json.dumps({k: rep[k] for k in ("run_id", "inputs", "envelopes", "requests", "runtime_s", "third_party_cost_usd")}))
    else:
        from .render import render
        render(a.input, a.output)


if __name__ == "__main__":
    main()
