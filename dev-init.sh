#!/bin/bash
# Development environment startup for PostgreSQL-native Open Notebook.

set -e

echo "=== Open Notebook Dev Startup ==="

POSTGRES_PORT=${POSTGRES_PORT:-5432}
echo "Checking PostgreSQL on port $POSTGRES_PORT..."
if ! nc -z localhost "$POSTGRES_PORT" 2>/dev/null; then
  echo "PostgreSQL not reachable on port $POSTGRES_PORT. Start PostgreSQL/pgvector first."
  exit 1
fi
echo "PostgreSQL is reachable"

echo "Syncing Python dependencies..."
uv sync

echo "Syncing frontend dependencies..."
cd frontend && npm install && cd ..

echo "Starting API backend (port 5055)..."
uv run --env-file .env run_api.py &
sleep 3

echo "Starting background worker..."
uv run --env-file .env open-notebook-command-worker --import-modules commands --max-tasks "${OPEN_NOTEBOOK_WORKER_MAX_TASKS:-5}" &
sleep 2

echo "Starting Next.js frontend (port 3000)..."
echo "  Frontend: http://localhost:3000"
echo "  API:      http://localhost:5055"
echo "  API Docs: http://localhost:5055/docs"
cd frontend && npm run dev
