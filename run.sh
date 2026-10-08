#!/usr/bin/env bash
# One evaluator command:  ./run.sh <organisations.jsonl|txt> [output_dir]
# Writes <output_dir>/envelopes.jsonl, report.json and viewer.html. Keeps snapshots in ./store for refresh.
set -euo pipefail
IN="${1:?usage: ./run.sh organisations.jsonl [output_dir]}"
OUT="${2:-out}"
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
mkdir -p "$OUT"
.venv/bin/python -m fastpunkt run --organisations "$IN" --output "$OUT/envelopes.jsonl" \
  --report "$OUT/report.json" --store "${FASTPUNKT_STORE:-store}" \
  --max-requests "${FASTPUNKT_MAX_REQUESTS:-2000}" --time-budget-min "${FASTPUNKT_TIME_BUDGET_MIN:-45}"
.venv/bin/python -m fastpunkt render --input "$OUT/envelopes.jsonl" --output "$OUT/viewer.html"
