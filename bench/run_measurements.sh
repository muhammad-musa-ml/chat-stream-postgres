#!/usr/bin/env bash
# The measurements in the README, three runs of each, in this order.
#
# Expects: Postgres from docker compose; the backend on :8000 talking to Ollama
# and a second backend on :8001 talking to bench/fake_upstream.py on :9100,
# both started with MAX_OUTPUT_TOKENS=256 TEMPERATURE=0. See the README.
#
#   PY=backend/.venv/bin/python MACHINE="..." MODEL_SERVER="..." bench/run_measurements.sh
set -euo pipefail

PY=${PY:-python}
MACHINE=${MACHINE:-}
MODEL_SERVER=${MODEL_SERVER:-}

for run in 1 2 3; do
  "$PY" bench/latency.py --base-url http://127.0.0.1:8000 --repeats 3 --warmup 1 \
    --label "llama3.2:3b on Ollama, 1 in flight" --model-server "$MODEL_SERVER" --machine "$MACHINE" \
    --out "results/latency/1-ollama-run$run.json"
done

for run in 1 2 3; do
  "$PY" bench/latency.py --base-url http://127.0.0.1:8001 --repeats 3 --warmup 1 \
    --label "fake model, 1 in flight" --model-server "bench/fake_upstream.py defaults" --machine "$MACHINE" \
    --out "results/latency/2-fake-1-in-flight-run$run.json"
done

for run in 1 2 3; do
  "$PY" bench/latency.py --base-url http://127.0.0.1:8001 --repeats 3 --warmup 1 --concurrency 30 \
    --label "fake model, 30 in flight" --model-server "bench/fake_upstream.py defaults" --machine "$MACHINE" \
    --out "results/latency/3-fake-30-in-flight-run$run.json"
done

for run in 1 2 3; do
  "$PY" bench/index_read_path.py --seed "$run" --machine "$MACHINE" --out "results/index/run$run.json"
done
