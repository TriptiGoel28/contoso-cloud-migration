# =============================================================================
# Contoso Financial - Web Application Dockerfile
# Multi-stage build: builder installs Python deps, runtime runs the app.
# Non-root user (appuser, uid 1001) for container security best practices.
#
# To deploy to ECS:
#   docker build -t contoso-webapp .
#   docker tag contoso-webapp:latest <account>.dkr.ecr.<region>.amazonaws.com/contoso-webapp:latest
#   aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
#   docker push <account>.dkr.ecr.<region>.amazonaws.com/contoso-webapp:latest
#   aws ecs update-service --cluster contoso --service contoso-webapp --force-new-deployment
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Builder
# Install Python dependencies into /install so they can be copied cleanly
# to the runtime stage without build tools.
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies needed to compile psycopg2 and other C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker layer caching.
# Requirements are only reinstalled when requirements.txt changes.
COPY app/requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# -----------------------------------------------------------------------------
# Stage 2: Runtime
# Minimal image with only what is needed to run the application.
# Does not contain gcc, build headers, or any other build tooling.
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Install runtime OS dependencies only (curl for healthcheck, libpq for psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder stage
COPY --from=builder /install /usr/local

# Create a non-root user to run the application.
# UID 1001 avoids collision with common system users and is required by
# some ECS security configurations that block UID 0.
RUN groupadd --gid 1001 appuser && \
    useradd --uid 1001 --gid 1001 --no-create-home --shell /bin/false appuser

WORKDIR /app

# Copy application source code
COPY app/ .

# Change ownership of the app directory to appuser
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

# Healthcheck used by Docker Compose.
# In ECS, the ALB target group health check on /health takes precedence.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Run gunicorn with 2 workers.
# Worker count is kept low because ECS tasks are sized at 0.5 vCPU / 1 GB.
# Scale horizontally (more ECS tasks) rather than vertically (more workers per task).
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "30", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
