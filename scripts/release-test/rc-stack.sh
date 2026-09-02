#!/bin/bash
# Browsable release-candidate stack for the PostgreSQL-native runtime.
#
# Usage:
#   rc-stack.sh up <tag> [dump.sql] [--with-runtimes]
#   rc-stack.sh down <tag>
#
# A supplied dump must be a PostgreSQL SQL dump suitable for `psql`. Legacy
# SurrealDB data must first be converted with scripts/migrate_surreal_to_postgres.py.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
TAG="${2:?usage: rc-stack.sh <up|down> <tag> [dump.sql] [--with-runtimes]}"
IMAGE_REPO="${RC_IMAGE_REPO:-ghcr.io/sfmullins/open-notebook}"

WITH_RUNTIMES=""
DUMP=""
for arg in "${@:3}"; do
  if [ "$arg" = "--with-runtimes" ]; then WITH_RUNTIMES="true"; else DUMP="$arg"; fi
done

RC_DATA=/tmp/onrel-rc-data
KEY=$(grep '^OPEN_NOTEBOOK_ENCRYPTION_KEY' "$REPO/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
DB=$(grep '^POSTGRES_DB' "$REPO/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
DB="${DB:-open_notebook}"

compose() {
  APP_IMAGE="$IMAGE_REPO:$TAG" DATA_DIR="$RC_DATA" \
  API_PORT=15055 FE_PORT=18502 PROXY_PORT=18080 \
  RC_API_URL="http://localhost:15055" \
  RC_ENCRYPTION_KEY="${KEY:-release-test-key}" RC_POSTGRES_DB="$DB" \
  RC_ENABLE_DOCLING="${WITH_RUNTIMES:+true}" RC_ENABLE_CRAWL4AI="${WITH_RUNTIMES:+true}" \
  docker compose -p onrelrc -f "$DIR/docker-compose.release-test.yml" "$@"
}

case "$1" in
  down)
    compose down -v
    rm -rf "$RC_DATA"
    echo "RC stack removed."
    ;;
  up)
    docker pull "$IMAGE_REPO:$TAG" || \
      echo "WARNING: could not pull $IMAGE_REPO:$TAG; using a matching local image if present."
    [ -n "$WITH_RUNTIMES" ] && echo "Opt-in runtimes enabled; first boot may be slow."
    compose down -v >/dev/null 2>&1 || true
    rm -rf "$RC_DATA"
    mkdir -p "$RC_DATA/postgres" "$RC_DATA/notebook"

    compose up -d postgres
    echo "Waiting for PostgreSQL..."
    for _ in $(seq 1 30); do
      if docker exec onrelrc-postgres-1 pg_isready -U open_notebook -d "$DB" >/dev/null 2>&1; then break; fi
      sleep 2
    done

    if [ -n "$DUMP" ]; then
      [ -f "$DUMP" ] || { echo "dump not found: $DUMP" >&2; exit 2; }
      echo "Importing PostgreSQL dump: $DUMP"
      docker exec -i onrelrc-postgres-1 psql -v ON_ERROR_STOP=1 -U open_notebook -d "$DB" < "$DUMP"
    fi

    compose up -d
    echo "Waiting for API..."
    for _ in $(seq 1 30); do
      curl -sf -m 5 -o /dev/null http://localhost:15055/docs && break
      sleep 5
    done

    NB=$(curl -s http://localhost:15055/api/notebooks | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
    echo "RC stack up - image $IMAGE_REPO:$TAG"
    echo "  UI:        http://localhost:18502"
    echo "  via nginx: http://localhost:18080"
    echo "  API:       http://localhost:15055"
    echo "  notebooks: $NB"
    ;;
  *)
    echo "usage: rc-stack.sh <up|down> <tag> [dump.sql] [--with-runtimes]" >&2
    exit 2
    ;;
esac
