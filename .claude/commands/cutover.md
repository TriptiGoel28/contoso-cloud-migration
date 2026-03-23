# Migration cutover checklist

Walks through the pre-cutover gate checks before shifting production traffic to AWS.
Each check must pass before proceeding. Based on docs/10-rollback.md.

```bash
cd "$(git rev-parse --show-toplevel)"

# Requires: WEBAPP_URL pointing to AWS ALB, DB_HOST pointing to RDS endpoint
WEBAPP_URL="${WEBAPP_URL:-http://localhost:8080}"

echo "========================================"
echo "  CONTOSO MIGRATION CUTOVER CHECKLIST"
echo "========================================"
echo "  Target: $WEBAPP_URL"
echo "  Time:   $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "========================================"
echo ""

PASS=0
FAIL=0

gate() {
  local label="$1"
  shift
  if "$@" > /dev/null 2>&1; then
    echo "  [PASS] $label"
    PASS=$((PASS+1))
  else
    echo "  [FAIL] $label  <-- BLOCKER"
    FAIL=$((FAIL+1))
  fi
}

echo "--- 1. Web Application ---"
gate "Health endpoint responds 200"   curl -sf "$WEBAPP_URL/health"
gate "Customer list endpoint works"   curl -sf "$WEBAPP_URL/api/customers"
gate "Smoke tests pass"               python3 -m pytest tests/smoke/ -q

echo ""
echo "--- 2. Data Integrity ---"
gate "Contract tests pass"            python3 -m pytest tests/contract/ -q
gate "Data integrity tests pass"      python3 -m pytest tests/data_integrity/ -q

echo ""
echo "--- 3. Rollback Readiness ---"
echo "  [ ] Route 53 weighted routing configured (ECS weight currently 0)"
echo "  [ ] On-prem web-01 still running and healthy"
echo "  [ ] On-prem batch-01 cron DISABLED (check: ssh batch-01.contoso.local crontab -l)"
echo "  [ ] DBA on standby for RDS promotion if needed"
echo "  [ ] Finance team notified of cutover window"
echo ""

echo "========================================"
echo "  Results: $PASS passed, $FAIL failed"
echo "========================================"

if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "  CUTOVER BLOCKED: Fix failures above before proceeding."
  echo "  Rollback procedures: docs/10-rollback.md"
  exit 1
else
  echo ""
  echo "  All automated gates PASSED."
  echo "  Complete manual checklist above, then shift Route 53 weight."
fi
```
