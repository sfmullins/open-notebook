.PHONY: run frontend check ruff database lint api start-all stop-all status clean-cache worker worker-start worker-stop worker-restart
.PHONY: docker-buildx-prepare docker-buildx-clean docker-buildx-reset
.PHONY: docker-push docker-push-latest docker-release docker-build-local tag export-docs
.PHONY: release-test release-stack release-stack-down native-check

# Get version from pyproject.toml
VERSION := $(shell grep -m1 version pyproject.toml | cut -d'"' -f2)

# Image names for optional container release artifacts. Docker is no longer
# required for development or native installation.
DOCKERHUB_IMAGE := lfnovo/open_notebook
GHCR_IMAGE := ghcr.io/lfnovo/open-notebook
PLATFORMS := linux/amd64,linux/arm64

# === Native development/runtime ===

database:
	@command -v pg_isready >/dev/null 2>&1 || (echo "PostgreSQL client tools are not installed"; exit 1)
	@pg_isready -d "$${DATABASE_URL:-postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook}" >/dev/null || \
		(echo "PostgreSQL is not reachable. Start the local PostgreSQL service and verify DATABASE_URL."; exit 1)
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
	uv run --env-file .env surreal-commands-worker --import-modules commands --max-tasks "$${OPEN_NOTEBOOK_WORKER_MAX_TASKS:-5}"

worker-stop:
	@echo "Stopping Open Notebook command worker..."
	@pkill -f "surreal-commands-worker" || true

worker-restart: worker-stop
	@sleep 2
	@$(MAKE) worker-start

start-all: native-check
	@echo "Starting Open Notebook (PostgreSQL + API + Worker + Frontend)..."
	@echo "Starting API backend..."
	@uv run --env-file .env run_api.py &
	@sleep 2
	@echo "Starting background worker..."
	@uv run --env-file .env surreal-commands-worker --import-modules commands --max-tasks "$${OPEN_NOTEBOOK_WORKER_MAX_TASKS:-5}" &
	@sleep 1
	@echo "Starting Next.js frontend..."
	@echo "Frontend: http://localhost:3000"
	@echo "API is proxied through the frontend at /api/*"
	cd frontend && npm run dev

stop-all:
	@echo "Stopping Open Notebook application services..."
	@pkill -f "next dev" || true
	@pkill -f "next start" || true
	@pkill -f "surreal-commands-worker" || true
	@pkill -f "run_api.py" || true
	@pkill -f "uvicorn api.main:app" || true
	@echo "PostgreSQL was not stopped because it is an operating-system database service."

status:
	@echo "Open Notebook Service Status:"
	@echo "PostgreSQL:"
	@pg_isready -d "$${DATABASE_URL:-postgresql://open_notebook:open_notebook@127.0.0.1:5432/open_notebook}" >/dev/null 2>&1 && echo "  running" || echo "  not reachable"
	@echo "API Backend:"
	@pgrep -f "run_api.py\|uvicorn api.main:app" >/dev/null && echo "  running" || echo "  not running"
	@echo "Background Worker:"
	@pgrep -f "surreal-commands-worker" >/dev/null && echo "  running" || echo "  not running"
	@echo "Next.js Frontend:"
	@pgrep -f "next dev\|next start" >/dev/null && echo "  running" || echo "  not running"

lint:
	uv run python -m mypy .

ruff:
	uv run ruff check . --fix

# === Optional container build/release tooling ===
# Kept for maintainers who publish container artifacts. These targets are not
# used by the native runtime and are not prerequisites for development.

docker-buildx-prepare:
	@docker buildx inspect multi-platform-builder >/dev/null 2>&1 || \
		docker buildx create --use --name multi-platform-builder --driver docker-container
	@docker buildx use multi-platform-builder

docker-buildx-clean:
	@echo "Cleaning up buildx builders..."
	@docker buildx rm multi-platform-builder 2>/dev/null || true
	@docker ps -a | grep buildx_buildkit | awk '{print $$1}' | xargs -r docker rm -f 2>/dev/null || true
	@echo "Buildx cleanup complete"

docker-buildx-reset: docker-buildx-clean docker-buildx-prepare
	@echo "Buildx reset complete"

# Automated image gate: fresh install + upgrade against real images.
# Usage: make release-test TAG=1.12.0 OLD_TAG=1.11.0
release-test:
	@test -n "$(TAG)" || (echo "usage: make release-test TAG=<new> [OLD_TAG=<previous>]"; exit 1)
	bash scripts/release-test/release-image-test.sh all \
		"$(DOCKERHUB_IMAGE):$(TAG)" \
		$(if $(OLD_TAG),"$(DOCKERHUB_IMAGE):$(OLD_TAG)")

# Browsable RC stack for container release verification.
release-stack:
	@test -n "$(TAG)" || (echo "usage: make release-stack TAG=<tag> [DUMP=<dump.surql>]"; exit 1)
	bash scripts/release-test/rc-stack.sh up "$(TAG)" $(DUMP)

release-stack-down:
	bash scripts/release-test/rc-stack.sh down "$(or $(TAG),unused)"

docker-build-local:
	@echo "Building production image locally ($(shell uname -m))..."
	docker build \
		-t $(DOCKERHUB_IMAGE):$(VERSION) \
		-t $(DOCKERHUB_IMAGE):local \
		.
	@echo "Built $(DOCKERHUB_IMAGE):$(VERSION) and $(DOCKERHUB_IMAGE):local"

docker-push: docker-buildx-prepare
	@echo "Building and pushing version $(VERSION) to both registries..."
	docker buildx build --pull \
		--platform $(PLATFORMS) \
		--progress=plain \
		-t $(DOCKERHUB_IMAGE):$(VERSION) \
		-t $(GHCR_IMAGE):$(VERSION) \
		--push \
		.
	docker buildx build --pull \
		--platform $(PLATFORMS) \
		--progress=plain \
		--target single \
		-t $(DOCKERHUB_IMAGE):$(VERSION)-single \
		-t $(GHCR_IMAGE):$(VERSION)-single \
		--push \
		.
	@echo "Pushed version $(VERSION) to both registries"

docker-push-latest: docker-buildx-prepare
	@echo "Updating v1-latest tags to version $(VERSION)..."
	docker buildx build --pull \
		--platform $(PLATFORMS) \
		--progress=plain \
		-t $(DOCKERHUB_IMAGE):$(VERSION) \
		-t $(DOCKERHUB_IMAGE):v1-latest \
		-t $(GHCR_IMAGE):$(VERSION) \
		-t $(GHCR_IMAGE):v1-latest \
		--push \
		.
	docker buildx build --pull \
		--platform $(PLATFORMS) \
		--progress=plain \
		--target single \
		-t $(DOCKERHUB_IMAGE):$(VERSION)-single \
		-t $(DOCKERHUB_IMAGE):v1-latest-single \
		-t $(GHCR_IMAGE):$(VERSION)-single \
		-t $(GHCR_IMAGE):v1-latest-single \
		--push \
		.
	@echo "Updated v1-latest to version $(VERSION)"

docker-release: docker-push-latest
	@echo "Full container release complete for version $(VERSION)"

tag:
	@version=$$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'); \
	echo "Creating tag v$$version"; \
	git tag "v$$version"; \
	git push origin "v$$version"

# Legacy explicit container development helpers remain opt-in.
dev:
	docker compose -f examples/docker-compose-dev.yml --project-directory . up --build

full:
	docker compose -f examples/docker-compose-full-local.yml --project-directory . up --build

# === Documentation Export ===
export-docs:
	@echo "Exporting documentation..."
	@uv run python scripts/export_docs.py
	@echo "Documentation export complete"

# === Cleanup ===
clean-cache:
	@echo "Cleaning cache directories..."
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".mypy_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".ruff_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".pytest_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -type f -delete 2>/dev/null || true
	@find . -name "*.pyo" -type f -delete 2>/dev/null || true
	@find . -name "*.pyd" -type f -delete 2>/dev/null || true
	@echo "Cache directories cleaned"
