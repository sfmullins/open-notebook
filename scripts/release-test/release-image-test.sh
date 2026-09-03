#!/bin/bash
# Release image gate for the PostgreSQL-native runtime.
#
# Usage: release-image-test.sh <fresh|upgrade|probe|all> <new-image> [old-image]
#
# `old-image`, when supplied, must itself be a PostgreSQL-native build. Migration
# from a legacy SurrealDB installation is validated separately with
# scripts/migrate_surreal_to_postgres.py; it cannot be modelled as a same-volume
# container upgrade.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="$DIR/docker-compose.release-test.yml"
NEW_IMAGE="${2:?usage: release-image-test.sh <fresh|upgrade|probe|all> <new-image> [old-postgres-image]}"
OLD_IMAGE="${3:-}"
PASS=0
FAIL=0

ok() { echo "  PASS $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL $1"; FAIL=$((FAIL+1)); }
check() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (expected=$2, got=$3)"; fi; }

set_ports() {
  API_PORT=$1
  FE_PORT=$2
  PROXY_PORT=$3
  API="http://localhost:$1"
  FE="http://localhost:$2"
  PROXY="http://localhost:$3"
}

compose_env() {
  env APP_IMAGE="$1" DATA_DIR="$2" API_PORT="$API_PORT" FE_PORT="$FE_PORT" \
    PROXY_PORT="$PROXY_PORT" docker compose -p "$3" -f "$COMPOSE" "${@:4}"
}

compose_up() {
  local out running
  out=$(compose_env "$1" "$2" "$3" up -d --quiet-pull 2>&1)
  if echo "$out" | grep -qi "error"; then
    bad "compose up ($3): $(echo "$out" | grep -i error | head -1)"
    return 1
  fi
  running=$(docker inspect "$3-app-1" --format '{{.State.Running}}' 2>/dev/null)
  check "container $3-app-1 running" "true" "$running"
}

compose_down() {
  compose_env unused /tmp/unused "$1" down -v >/dev/null 2>&1
  if docker ps --format '{{.Names}}' | grep -q "^$1-"; then
    bad "teardown of $1 left containers running"
  fi
  [ -n "${2:-}" ] && rm -rf "$2"
}

wait_api() {
  for _ in $(seq 1 30); do
    if curl -sf -m 5 -o /dev/null "$API/docs"; then return 0; fi
    sleep 5
  done
  return 1
}

seed_and_verify() {
  local nb src status full_text
  nb=$(curl -s -X POST "$API/api/notebooks" -H "Content-Type: application/json" \
    -d '{"name":"release-probe","description":"release test seed"}' \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])" 2>/dev/null)
  [ -z "$nb" ] && { bad "create notebook"; return 1; }
  ok "create notebook ($nb)"

  src=$(curl -s -X POST "$API/api/sources" -F "type=text" -F "notebooks=[\"$nb\"]" \
    -F "content=Release test content. The Turing test evaluates machine intelligence." \
    -F "title=release-probe-source" -F "async_processing=true" -F "embed=false" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])" 2>/dev/null)
  [ -z "$src" ] && { bad "create source"; return 1; }
  ok "create source ($src)"

  status=""
  for _ in $(seq 1 24); do
    status=$(curl -s "$API/api/sources/$src/status" \
      | python3 -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
    [ "$status" = "completed" ] && break
    sleep 5
  done
  check "in-image worker processed source" "completed" "$status"

  full_text=$(curl -s "$API/api/sources/$src" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print('yes' if d.get('full_text') else 'no')" 2>/dev/null)
  check "full_text present" "yes" "$full_text"
}

fresh_test() {
  echo "=== FRESH INSTALL - $NEW_IMAGE"
  set_ports 15055 18502 18080
  local dd n code config
  dd=$(mktemp -d /tmp/onrel-fresh-XXXX)
  compose_up "$NEW_IMAGE" "$dd" onrelfresh || { compose_down onrelfresh "$dd"; return 1; }
  if wait_api; then ok "API up (PostgreSQL schema initialized)"; else
    bad "API did not come up in 150s"
    docker logs onrelfresh-app-1 2>&1 | tail -30
    compose_down onrelfresh "$dd"
    return 1
  fi

  n=$(curl -s "$API/api/notebooks" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null)
  check "GET /api/notebooks on a virgin database" "0" "$n"
  seed_and_verify

  for field in type title created updated insights_count embedded; do
    code=$(curl -s -o /dev/null -w '%{http_code}' "$API/api/sources?sort_by=$field&limit=2")
    check "sort_by=$field" "200" "$code"
  done

  code=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "$FE")
  if [ "$code" = "200" ] || [ "$code" = "307" ]; then ok "frontend responds ($code)"; else bad "frontend ($code)"; fi
  config=$(curl -s -m 10 "$PROXY/config")
  echo "$config" | grep -q apiUrl && ok "/config via nginx" || bad "/config via nginx: $config"
  compose_down onrelfresh "$dd"
  echo
}

upgrade_test() {
  if [ -z "$OLD_IMAGE" ]; then
    echo "=== UPGRADE skipped: no previous PostgreSQL-native image supplied"
    return 0
  fi
  echo "=== UPGRADE - $OLD_IMAGE -> $NEW_IMAGE"
  set_ports 25055 28502 28080
  local dd image found code
  dd=$(mktemp -d /tmp/onrel-upg-XXXX)

  compose_up "$OLD_IMAGE" "$dd" onrelupg || { compose_down onrelupg "$dd"; return 1; }
  image=$(docker inspect onrelupg-app-1 --format '{{.Config.Image}}' 2>/dev/null)
  check "phase 1 runs previous image" "$OLD_IMAGE" "$image"
  if wait_api; then ok "previous image up"; else
    bad "previous image did not come up"
    compose_down onrelupg "$dd"
    return 1
  fi
  seed_and_verify

  compose_env unused /tmp/unused onrelupg stop app >/dev/null 2>&1
  compose_env unused /tmp/unused onrelupg rm -f app >/dev/null 2>&1
  compose_up "$NEW_IMAGE" "$dd" onrelupg || { compose_down onrelupg "$dd"; return 1; }
  image=$(docker inspect onrelupg-app-1 --format '{{.Config.Image}}' 2>/dev/null)
  check "phase 2 runs new image" "$NEW_IMAGE" "$image"
  if wait_api; then ok "new image up on existing PostgreSQL data"; else
    bad "new image did not come up on existing PostgreSQL data"
    docker logs onrelupg-app-1 2>&1 | tail -30
    compose_down onrelupg "$dd"
    return 1
  fi

  found=$(curl -s "$API/api/notebooks" | python3 -c "import json,sys; n=json.load(sys.stdin); print('yes' if any(x.get('name')=='release-probe' for x in n) else 'no')" 2>/dev/null)
  check "seeded notebook survived upgrade" "yes" "$found"
  code=$(curl -s -o /dev/null -w '%{http_code}' "$API/api/sources?sort_by=title&limit=2")
  check "sort_by=title after upgrade" "200" "$code"
  compose_down onrelupg "$dd"
  echo
}

probe_test() {
  echo "=== CONTAINER PROBES - $NEW_IMAGE"
  local cid concurrency
  cid=$(docker run -d --rm \
    -e OPEN_NOTEBOOK_WORKER_MAX_TASKS=2 \
    -e DATABASE_URL=postgresql://open_notebook:open_notebook@127.0.0.1:9/open_notebook \
    -e OPEN_NOTEBOOK_ENCRYPTION_KEY=probe \
    "$NEW_IMAGE" 2>/dev/null)
  if [ -z "$cid" ]; then
    bad "worker-concurrency probe: container did not start"
  else
    concurrency=""
    for _ in $(seq 1 20); do
      concurrency=$(docker logs "$cid" 2>&1 | grep -oiE "up to [0-9]+ concurrent tasks" | grep -oE "[0-9]+" | head -1)
      [ -n "$concurrency" ] && break
      sleep 3
    done
    check "OPEN_NOTEBOOK_WORKER_MAX_TASKS reaches worker" "2" "$concurrency"
    docker rm -f "$cid" >/dev/null 2>&1
  fi
  echo
}

case "${1:-all}" in
  fresh) fresh_test ;;
  upgrade) upgrade_test ;;
  probe) probe_test ;;
  all) fresh_test; upgrade_test; probe_test ;;
  *) echo "unknown mode: ${1:-}"; exit 2 ;;
esac

echo "=== RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
