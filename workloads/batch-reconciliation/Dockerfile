# ============================================================
# Contoso Financial — Batch Reconciliation Worker
# Two build targets: reconciler (default) and shim
#
# To build and deploy to AWS ECS:
#   # Reconciler worker:
#   docker build --target reconciler -t contoso-batch .
#   aws ecr get-login-password --region us-east-1 | \
#     docker login --username AWS --password-stdin <account>.dkr.ecr.us-east-1.amazonaws.com
#   docker tag contoso-batch:latest <account>.dkr.ecr.us-east-1.amazonaws.com/contoso-batch:latest
#   docker push <account>.dkr.ecr.us-east-1.amazonaws.com/contoso-batch:latest
#
#   # Compatibility shim (optional separate ECS service):
#   docker build --target shim -t contoso-batch-shim .
# ============================================================

# ---- Stage 1: Builder ----
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Stage 2: Reconciler (default target) ----
FROM python:3.11-slim AS reconciler

# Runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Non-root user for security
RUN groupadd -r batchgroup && useradd -r -g batchgroup -u 1002 batchuser

WORKDIR /app
COPY reconciler.py shim.py ./
RUN chown -R batchuser:batchgroup /app

USER batchuser

# No port exposed — this is a background worker, not a server
CMD ["python", "reconciler.py"]

# ---- Stage 3: Shim (compatibility HTTP server) ----
FROM reconciler AS shim

EXPOSE 8081

CMD ["uvicorn", "shim:app", "--host", "0.0.0.0", "--port", "8081", \
     "--log-level", "info", "--access-log"]
