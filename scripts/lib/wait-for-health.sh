# Shared 90×2s health poll for container verifiers.
# Usage: wait_for_health command [args...]
# Sets ready=1 on success. Returns 0 when the command succeeds, 1 after 90 failures.

wait_for_health() {
  ready=0
  attempt=0
  while [ "$attempt" -lt 90 ]; do
    if "$@"; then
      ready=1
      break
    fi
    attempt=$((attempt + 1))
    sleep 2
  done
  [ "$ready" -eq 1 ]
}
