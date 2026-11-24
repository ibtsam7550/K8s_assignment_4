#!/bin/bash
# Load generator (adapted from continuous-load.sh): one httperf run of POST /api/emi with random valid inputs.
#   scripts/load.sh 50 60            50 requests/second for 60 seconds
#   RATE=50 DURATION=60 scripts/load.sh      (how run_tests.sh calls it)
# Override the target with SERVER / PORT / URI (defaults: emi.local, 80, /api/emi). Prints the httperf report.

SERVER=${SERVER:-emi.local}
PORT=${PORT:-80}
URI=${URI:-/api/emi}
TIMEOUT=${TIMEOUT:-5}
RATE=${1:-${RATE:-10}}
DURATION=${2:-${DURATION:-60}}

case "$RATE$DURATION" in *[!0-9]* | "") echo "RATE and DURATION must be whole numbers"; exit 2 ;; esac
[ "$RATE" -ge 1 ] && [ "$DURATION" -ge 1 ] || { echo "RATE and DURATION must be at least 1"; exit 2; }
command -v httperf >/dev/null || { echo "httperf not found (brew install httperf)"; exit 2; }

echo "== $RATE req/s for ${DURATION}s -> http://$SERVER:$PORT$URI =="

# the target must answer (the app's /health also needs MongoDB); --resolve skips the slow macOS lookup of *.local names
curl -s -f --max-time "$TIMEOUT" \
  --resolve "$SERVER:$PORT:127.0.0.1" \
  "http://$SERVER:$PORT/health" -o /dev/null || {
    echo "ERROR: http://$SERVER:$PORT/health is not reachable"
    exit 1
}

# httperf replays a session file, so build 300 random valid POST bodies (the file wraps around)
FILE=$(mktemp /tmp/emi-sessions.XXXXXX) && trap 'rm -f "$FILE"' EXIT
for _ in $(seq 300); do
    printf "%s method=POST contents='principal=%d&rate=%d.%02d&months=%d'\n\n" "$URI" \
        $((10000 + RANDOM * 150)) $((1 + RANDOM % 29)) $((RANDOM % 100)) $((6 + RANDOM % 355)) >> "$FILE"
done

# --hog: use all local TCP ports (200 new connections/s would exhaust httperf's default port range)
httperf --hog --server "$SERVER" --port "$PORT" --timeout "$TIMEOUT" \
    --add-header 'Content-Type: application/x-www-form-urlencoded\n' \
    --wsesslog "$((RATE * DURATION)),0,$FILE" --rate "$RATE"
