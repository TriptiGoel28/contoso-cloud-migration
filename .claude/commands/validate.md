# Validate Terraform and Docker configuration

Runs all static validation checks without applying anything:
- `terraform fmt -check` — flags unformatted HCL
- `terraform validate` — checks syntax and internal consistency
- `docker compose config` — validates compose file structure
- Python syntax check across all workload code

```bash
cd "$(git rev-parse --show-toplevel)"

PASS=0
FAIL=0

check() {
  local label="$1"
  shift
  if "$@" > /dev/null 2>&1; then
    echo "  PASS  $label"
    PASS=$((PASS+1))
  else
    echo "  FAIL  $label"
    "$@" 2>&1 | head -10
    FAIL=$((FAIL+1))
  fi
}

echo "=== Terraform ==="
if command -v terraform &>/dev/null; then
  check "terraform fmt"      terraform fmt -check infra/terraform/
  check "terraform validate" bash -c "cd infra/terraform && terraform validate"
else
  echo "  SKIP  terraform not installed"
fi

echo ""
echo "=== Docker Compose ==="
check "compose config"  docker compose config -q

echo ""
echo "=== Python syntax ==="
for f in $(find workloads -name "*.py"); do
  check "$f"  python3 -m py_compile "$f"
done

echo ""
echo "=== Summary ==="
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
[ "$FAIL" -eq 0 ] && echo "All checks passed." || exit 1
```
