# Contoso Financial — Cloud Migration
# Common development and validation operations
#
# Usage:
#   make up       — start the local Docker Compose stack
#   make down     — tear down stack and remove volumes
#   make test     — run all tests against local stack
#   make build    — rebuild all Docker images (no cache)
#   make validate — validate Terraform + Docker Compose config
#   make logs     — stream logs from all containers
#   make clean    — remove stopped containers and dangling images

.PHONY: up down test test-smoke test-contract test-integrity build validate logs clean

# ── Local Stack ────────────────────────────────────────────────────────────────

up:
	docker compose up -d
	@echo ""
	@echo "Waiting for services to become healthy..."
	@sleep 15
	@docker compose ps
	@echo ""
	@echo "Web app:       http://localhost:8080"
	@echo "Batch shim:    http://localhost:8081"
	@echo "MinIO console: http://localhost:9001  (minioadmin / minioadmin123)"

down:
	docker compose down -v
	@echo "Stack stopped and volumes removed."

restart: down up

build:
	docker compose build --no-cache

logs:
	docker compose logs -f --tail=50

# ── Tests ──────────────────────────────────────────────────────────────────────

# Install test dependencies before first run
deps:
	pip install pytest requests psycopg2-binary redis boto3

test: test-smoke test-contract test-integrity

test-smoke:
	@echo "=== Smoke Tests ==="
	python3 -m pytest tests/smoke/ -v --tb=short

test-contract:
	@echo "=== Contract Tests ==="
	python3 -m pytest tests/contract/ -v --tb=short

test-integrity:
	@echo "=== Data Integrity Tests ==="
	python3 -m pytest tests/data_integrity/ -v --tb=short

# Run tests against AWS post-cutover (set WEBAPP_URL, DB_HOST etc. first)
test-aws:
	@echo "=== Running tests against AWS (WEBAPP_URL=$(WEBAPP_URL)) ==="
	python3 -m pytest tests/ -v --tb=short

# ── Validation ────────────────────────────────────────────────────────────────

validate: validate-compose validate-python validate-terraform

validate-compose:
	@echo "=== Docker Compose ==="
	docker compose config -q && echo "  compose config: OK"

validate-python:
	@echo "=== Python syntax ==="
	@find workloads -name "*.py" -exec python3 -m py_compile {} \; && echo "  syntax: OK"

validate-terraform:
	@echo "=== Terraform ==="
	@cd infra/terraform && terraform fmt -check && echo "  fmt: OK"
	@cd infra/terraform && terraform validate && echo "  validate: OK"

# ── Utility ───────────────────────────────────────────────────────────────────

clean:
	docker compose down --remove-orphans
	docker image prune -f
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete

# Trigger a manual batch reconciliation run by uploading a test CSV to MinIO
trigger-batch:
	@echo "Uploading test CSV to trigger reconciliation..."
	docker compose exec minio-init mc alias set local http://minio:9000 minioadmin minioadmin123 2>/dev/null || true
	@echo "transaction_id,amount,timestamp,source_system" | \
		docker compose exec -T minio-init mc pipe local/reconciliation-input/trigger-$(shell date +%Y%m%d%H%M%S).csv
	@echo "File uploaded. Batch worker picks it up within POLL_INTERVAL seconds (default: 30s)."
	@echo "Check logs: make logs"

help:
	@echo "Contoso Financial — Cloud Migration"
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@echo "Stack:"
	@echo "  up               Start local Docker Compose stack"
	@echo "  down             Stop stack and remove volumes"
	@echo "  restart          down + up"
	@echo "  build            Rebuild all images (no cache)"
	@echo "  logs             Stream container logs"
	@echo ""
	@echo "Tests:"
	@echo "  test             Run all test suites"
	@echo "  test-smoke       Basic availability tests"
	@echo "  test-contract    API contract tests"
	@echo "  test-integrity   Data integrity tests"
	@echo "  test-aws         Run tests against AWS (requires env vars)"
	@echo ""
	@echo "Validation:"
	@echo "  validate         Validate Terraform + Compose + Python"
	@echo ""
	@echo "Utility:"
	@echo "  clean            Remove containers, caches, .pyc files"
	@echo "  trigger-batch    Upload test CSV to trigger batch worker"
