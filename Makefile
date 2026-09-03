.PHONY: run frontend check ruff database lint api start-all stop-all status clean-cache worker worker-start worker-stop worker-restart
.PHONY: docker-buildx-prepare docker-buildx-clean docker-buildx-reset
.PHONY: docker-push docker-push-latest docker-release docker-build-local tag export-docs
.PHONY: release-test release-stack release-stack-down native-check dev full

VERSION := $(shell grep -m1 version pyproject.toml | cut -d'"' -f2)
CONTAINER_IMAGE ?= ghcr.io/sfmullins/open-notebook
PLATFORMS := linux/amd64,linux/arm64

# === Native development/runtime ===

database:
	@command -v pg_isready >/dev/null 2>&1 || (echo "PostgreSQL client tools are not installed"; exit 1)
	@pg_isready -d "$${DATABASE_URL:-postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook}" >/dev/null || \
		(echo "PostgreSQL is not reachable. Start PostgreSQL and verify DATABASE_URL."; exit 1)
	@echo "PostgreSQL is reachable"

native-check: database
	@command -v uv >/dev/null 2>&1 || (echo "uv is not installed"; exit 1)
	@command -v npm >/dev/null 2>&1 || (echo "npm is not installed"; exit 1)
	@echo "Native prerequisites are available"

run:
	@echo "Starting frontend only. For full functionality, use 'make start-all'"
	cd frontend && npm run dev

frontend:
	cd frontend && npm run dev

api:
	uv run --env-file .env run_api.py

worker: worker-start

worker-start:
	@echo "Starting PostgreSQL-backed command worker..."
	uv run --env-file .env open-notebook-command-worker --import-modules commands --max-tasks "$${OPEN_NOTEBOOK_WORKER_MAX_TASKS:-5}"

worker-stop:
	@echo "Stopping Open Notebook command worker..."
	@pkill -f "open-notebook-command-worker" || true

worker-restart: worker-stop
	@sleep 2
	@$(MAKE) worker-start

start-all: native-check
	@echo "Starting Open Notebook (PostgreSQL + API + Worker + Frontend)..."
	@uv run --env-file .env run_api.py &
	@sleep 2
	@uv run --env-file .env open-notebook-command-worker --import-modules commands --max-tasks "$${OPEN_NOTEBOOK_WORKER_MAX_TASKS:-5}" &
	@sleep 1
	@echo "Frontend: http://localhost:3000"
	@echo "API is proxied through the frontend at /api/*"
	cd frontend && npm run dev

stop-all:
	@echo "Stopping Open Notebook application services..."
	@pkill -f "next dev" || true
	@pkill -f "next start" || true
	@pkill -f "open-notebook-command-worker" || true
	@pkill -f "run_api.py" || true
	@pkill -f "uvicorn api.main:app" || true
	@echo "PostgreSQL was not stopped because it is an operating-system database service."

status:
	@echo "Open Notebook Service Status:"
	@pg_isready -d "$${DATABASE_URL:-postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook}" >/dev/null 2>&1 && echo "PostgreSQL: running" || echo "PostgreSQL: not reachable"
	@pgrep -f "run_api.py\|uvicorn api.main:app" >/dev/null && echo "API: running" || echo "API: not running"
	@pgrep -f "open-notebook-command-worker" >/dev/null && echo "Worker: running" || echo "Worker: not running"
	@pgrep -f "next dev\|next start" >/dev/null && echo "Frontend: running" || echo "Frontend: not running"

lint:
	uv run python -m mypy .

ruff:
	uv run ruff check . --fix

# === Optional container tooling ===
# Docker is not required for development or native installation. If maintainers
# publish a container, it is an application-only image and requires an external
# PostgreSQL/pgvector service.

docker-buildx-prepare:
	@docker buildx inspect multi-platform-builder >/dev/null 2>&1 || \
		docker buildx create --use --name multi-platform-builder --driver docker-container
	@docker buildx use multi-platform-builder

docker-buildx-clean:
	@docker buildx rm multi-platform-builder 2>/dev/null || true
	@docker ps -a | grep buildx_buildkit | awk '{print $$1}' | xargs -r docker rm -f 2>/dev/null || true

docker-buildx-reset: docker-buildx-clean docker-buildx-prepare

release-test:
	@test -n "$(TAG)" || (echo "usage: make release-test TAG=<new> [OLD_TAG=<previous-postgres-native>]"; exit 1)
	bash scripts/release-test/release-image-test.sh all \
		"$(CONTAINER_IMAGE):$(TAG)" \
		$(if $(OLD_TAG),"$(CONTAINER_IMAGE):$(OLD_TAG)")

release-stack:
	@test -n "$(TAG)" || (echo "usage: make release-stack TAG=<tag> [DUMP=<dump.sql>]"; exit 1)
	RC_IMAGE_REPO="$(CONTAINER_IMAGE)" bash scripts/release-test/rc-stack.sh up "$(TAG)" $(DUMP)

release-stack-down:
	RC_IMAGE_REPO="$(CONTAINER_IMAGE)" bash scripts/release-test/rc-stack.sh down "$(or $(TAG),unused)"

docker-build-local:
	@echo "Building application image locally ($(shell uname -m))..."
	docker build --target runtime \
		-t $(CONTAINER_IMAGE):$(VERSION) \
		-t $(CONTAINER_IMAGE):local \
		.

docker-push: docker-buildx-prepare
	@echo "Building and pushing $(CONTAINER_IMAGE):$(VERSION)..."
	docker buildx build --pull \
		--target runtime \
		--platform $(PLATFORMS) \
		--progress=plain \
		-t $(CONTAINER_IMAGE):$(VERSION) \
		--push \
		.

docker-push-latest: docker-buildx-prepare
	@echo "Building and pushing version + latest tags..."
	docker buildx build --pull \
		--target runtime \
		--platform $(PLATFORMS) \
		--progress=plain \
		-t $(CONTAINER_IMAGE):$(VERSION) \
		-t $(CONTAINER_IMAGE):latest \
		--push \
		.

docker-release: docker-push-latest

tag:
	@version=$$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'); \
	echo "Creating tag v$$version"; \
	git tag "v$$version"; \
	git push origin "v$$version"

dev:
	docker compose -f examples/docker-compose-dev.yml --project-directory . up --build

full:
	docker compose -f examples/docker-compose-full-local.yml --project-directory . up --build

export-docs:
	@uv run python scripts/export_docs.py

clean-cache:
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".mypy_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".ruff_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".pytest_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -type f -delete 2>/dev/null || true
	@find . -name "*.pyo" -type f -delete 2>/dev/null || true
	@find . -name "*.pyd" -type f -delete 2>/dev/null || true
