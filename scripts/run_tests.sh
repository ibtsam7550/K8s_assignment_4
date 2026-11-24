#!/usr/bin/env bash
# Runs the Milestone 4 experiments around scripts/load.sh.
#
#   scripts/run_tests.sh matrix   # 15 runs: 1/2/3 replicas x 10..200 req/s, 60 s each
#   scripts/run_tests.sh hpa      # 10-minute autoscaling test (2 + 5 + 3 minutes)
#
# The load comes from scripts/load.sh (override with LOAD_CMD='...'; it gets RATE and DURATION in its environment).
# Each run's httperf report is saved in results/httperf/, profiling CSVs in results/run_r<replicas>_rate<rate>/
# (matrix) or results/autoscale/ (hpa, plus phases.csv with the real start/end time of each load phase).
set -euo pipefail
cd "$(dirname "$0")/.."
NS=emi
LOAD_CMD=${LOAD_CMD:-scripts/load.sh}
RUN_SECONDS=${RUN_SECONDS:-60}                 # length of each matrix run (assignment: 60 s)
SETTLE=${SETTLE:-5}                            # pause before each run so pods and metrics settle
PHASES=${PHASES:-"10:120 100:300 10:180"}      # rate:seconds, the assignment's autoscaling profile
REPLICAS=${REPLICAS:-"1 2 3"}                  # to resume after an interruption: REPLICAS=1 RATES="50 100 200"
RATES=${RATES:-"10 25 50 100 200"}
mkdir -p results/httperf
trap '[ -n "${MON:-}" ] && kill -TERM "$MON" 2>/dev/null; exit 130' INT TERM   # Ctrl+C also stops the monitor

start_monitor() {  # a monitor left over from an interrupted run would write into the same CSV files, so stop it first
  pkill -f scripts/monitor.py 2>/dev/null || true
  python3 scripts/monitor.py --out "$@" >/dev/null & MON=$!
}
stop_monitor() { kill -TERM "$MON" 2>/dev/null || true; wait "$MON" 2>/dev/null || true; }
load() {  # load <rate> <seconds> <output file>
  RATE=$1 DURATION=$2 bash -c "$LOAD_CMD" > "$3" 2>&1 || echo "  (load command exited non-zero - check $3)"
}

matrix() {
  if kubectl get hpa emi-web -n "$NS" >/dev/null 2>&1; then
    echo "An HPA exists and would override kubectl scale. Run: kubectl delete -f k8s/hpa/hpa.yaml"; exit 1
  fi
  for n in $REPLICAS; do
    kubectl scale deployment emi-web -n "$NS" --replicas="$n"
    kubectl rollout status deployment/emi-web -n "$NS" --timeout=180s
    for r in $RATES; do
      echo "=== replicas=$n rate=$r req/s for ${RUN_SECONDS}s ==="
      sleep "$SETTLE"
      start_monitor "results/run_r${n}_rate${r}"
      load "$r" "$RUN_SECONDS" "results/httperf/r${n}_rate${r}.txt"
      stop_monitor
      grep -hE "Reply time|Reply status|Errors: total" "results/httperf/r${n}_rate${r}.txt" | sed 's/^/    /' || true
    done
  done
  kubectl scale deployment emi-web -n "$NS" --replicas=3
  echo "Done."
}

hpa_test() {
  kubectl scale deployment emi-web -n "$NS" --replicas=1
  kubectl rollout status deployment/emi-web -n "$NS" --timeout=180s
  kubectl apply -f k8s/hpa/hpa.yaml
  sleep "$SETTLE"
  start_monitor results/autoscale --hpa
  echo "start,end,rate" > results/autoscale/phases.csv
  for phase in $PHASES; do
    echo "=== ${phase%:*} req/s for ${phase#*:}s ==="
    t0=$(date +%Y-%m-%dT%H:%M:%S%z)
    load "${phase%:*}" "${phase#*:}" "results/httperf/autoscale_rate${phase%:*}_$(date +%H%M%S).txt"
    echo "$t0,$(date +%Y-%m-%dT%H:%M:%S%z),${phase%:*}" >> results/autoscale/phases.csv
  done
  stop_monitor
  kubectl get hpa emi-web -n "$NS"
  echo "Done. (delete the HPA before any fixed-replica run)"
}

case "${1:-}" in
  matrix) matrix ;;
  hpa) hpa_test ;;
  *) sed -n '2,9p' "$0"; exit 1 ;;
esac