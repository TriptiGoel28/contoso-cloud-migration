# Run the full test suite against the local Docker Compose stack

Runs all three test suites (smoke, contract, data_integrity) against the local stack.
Verifies Docker Compose is healthy before running tests.

```bash
cd "$(git rev-parse --show-toplevel)"

echo "=== Checking Docker Compose health ==="
if ! docker compose ps --format json 2>/dev/null | python3 -c "
import json, sys
services = [json.loads(l) for l in sys.stdin if l.strip()]
unhealthy = [s['Name'] for s in services if s.get('Health','') not in ('healthy','')]
if unhealthy:
    print(f'Unhealthy services: {unhealthy}')
    sys.exit(1)
print(f'{len(services)} services running')
" 2>/dev/null; then
  echo "Stack not healthy. Run: make up"
  echo "Then wait ~30s and retry."
  exit 1
fi

echo ""
echo "=== Running smoke tests ==="
python3 -m pytest tests/smoke/ -v --tb=short

echo ""
echo "=== Running contract tests ==="
python3 -m pytest tests/contract/ -v --tb=short

echo ""
echo "=== Running data integrity tests ==="
python3 -m pytest tests/data_integrity/ -v --tb=short
```

## Environment

Tests use these env vars (defaults point to local Docker Compose stack):

| Variable | Default | AWS Equivalent |
|---|---|---|
| `WEBAPP_URL` | `http://localhost:8080` | ALB DNS |
| `DB_HOST` | `localhost` | RDS endpoint |
| `MINIO_ENDPOINT` | `http://localhost:9000` | S3 (not needed post-cutover) |

To run against AWS post-cutover, export the AWS values before running.
