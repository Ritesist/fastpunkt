"""Write docs/SMOKE_TEST_REPORT.md from the run reports and envelopes (no hand-typed numbers)."""
import json
import sys
from collections import Counter
from pathlib import Path

first, refresh, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
r1 = json.loads((first / "report.json").read_text())
r2 = json.loads((refresh / "report.json").read_text()) if (refresh / "report.json").exists() else None
env = [json.loads(l) for l in (first / "envelopes.jsonl").read_text().splitlines() if l.strip()]

L = ["# 100-company smoke test", "",
     f"Agent {r1['agent']} {r1['version']}, commit `{r1.get('commit') or 'none (run from the working tree that became the first commit)'}`. "
     f"Input: `tests/fixtures/smoke-companies-100.jsonl`, produced by the starter kit's `select_entry_batch.py` "
     f"(seed 20260823) from the official 411,160-company universe.", "",
     "## First run", "",
     "| Metric | Value |", "|---|---|",
     f"| Run id | {r1['run_id']} |",
     f"| Started / completed (UTC) | {r1['started_at']} / {r1['completed_at']} |",
     f"| Inputs / terminal envelopes | {r1['inputs']} / {r1['envelopes']} |",
     f"| Outbound HTTP requests | {r1['requests']} of {r1['request_budget']} |",
     f"| Wall time | {r1['runtime_s']} s of {r1['time_budget_min']} min |",
     f"| Per-company time p50 / p95 | {r1['company_runtime_ms_p50']/1000:.1f} s / {r1['company_runtime_ms_p95']/1000:.1f} s |",
     f"| TypeSafe calls / input tokens | {r1['typesafe']['calls']} / {r1['typesafe']['input_tokens']:,} |",
     f"| Declared third-party cost | USD {r1['third_party_cost_usd']:.4f} |",
     f"| Claims (all states) | {r1['claims_total']:,} |",
     f"| Available claims without evidence | {r1['available_claims_without_evidence']} |", "",
     "## Section states", "", "| Section | " + " | ".join(["available", "not_available", "ambiguous", "blocked", "failed", "not_applicable"]) + " |",
     "|---|" + "---|" * 6]
for s, c in r1["section_states"].items():
    L.append(f"| {s} | " + " | ".join(str(c.get(k, 0)) for k in ["available", "not_available", "ambiguous", "blocked", "failed", "not_applicable"]) + " |")
L += ["", "## Companies with each field (available claims)", "", "| Field | Companies |", "|---|---|"]
for k, v in sorted(r1["companies_with_field"].items(), key=lambda kv: -kv[1]):
    L.append(f"| {k} | {v} |")
wv = r1.get("website_verification", {})
L += ["", "## Website identity", "",
      "A website is published only with hard identity evidence. Verification methods used: "
      + ", ".join(f"{k} {v}" for k, v in wv.items()) + ". "
      "Sites that could not be tied to this exact legal entity are listed as candidates with state `ambiguous` and are not published.", ""]
if r2:
    L += ["## Refresh run (same inputs, same store)", "",
          "| Metric | Value |", "|---|---|",
          f"| Run id | {r2['run_id']} |",
          f"| Requests / wall time | {r2['requests']} / {r2['runtime_s']} s |",
          f"| Declared third-party cost | USD {r2['third_party_cost_usd']:.4f} |",
          f"| Refresh status | " + ", ".join(f"{k} {v}" for k, v in r2["refresh_status"].items() if v) + " |",
          f"| Material changes reported | {r2['material_changes']} |", "",
          "Snapshots are content-addressed and `history.jsonl` is append-only, so the first run's snapshots are preserved. "
          "A section that failed in one run is not treated as a removal.", ""]
errs = Counter(e2.get("reason", "")[:80] for e in env for e2 in e.get("errors", []) if isinstance(e2, dict))
if errs:
    L += ["## Errors recorded in envelopes", ""] + [f"- {n} x {k}" for k, n in errs.most_common(10)] + [""]
out.write_text("\n".join(L) + "\n", encoding="utf-8")
print(f"wrote {out}")
