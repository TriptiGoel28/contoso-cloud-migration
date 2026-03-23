# Start the local Docker Compose stack

Brings up the full local development environment:
- PostgreSQL 15 (mirrors RDS)
- Redis 7 (mirrors ElastiCache)
- MinIO + bucket init (mirrors S3)
- Web application (Flask)
- Batch reconciliation worker
- Compatibility shim

```bash
cd "$(git rev-parse --show-toplevel)"

echo "=== Starting Contoso local stack ==="
docker compose up -d

echo ""
echo "=== Waiting for services to become healthy (~30s) ==="
sleep 10

for i in 1 2 3 4 5; do
  UNHEALTHY=$(docker compose ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null | grep -v "healthy\|running" | grep -v "NAME" | wc -l)
  if [ "$UNHEALTHY" -le 1 ]; then
    break
  fi
  echo "  Waiting... ($i/5)"
  sleep 5
done

echo ""
echo "=== Service Status ==="
docker compose ps

echo ""
echo "=== Quick health check ==="
curl -sf http://localhost:8080/health && echo " Web app: OK" || echo " Web app: NOT READY"
curl -sf http://localhost:9000/minio/health/live && echo " MinIO: OK" || echo " MinIO: NOT READY"

echo ""
echo "Endpoints:"
echo "  Web app:      http://localhost:8080"
echo "  Batch shim:   http://localhost:8081"
echo "  MinIO console: http://localhost:9001  (minioadmin / minioadmin123)"
echo ""
echo "Run tests with: /test"
```
