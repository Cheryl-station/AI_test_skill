#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

SERVICE_CMD="python3 samples/backend/app.py"
HEALTH_URL="http://localhost:5000/health"

echo "Starting sample backend service..."
$SERVICE_CMD &
SERVICE_PID=$!

cleanup() {
  echo "Stopping sample backend service..."
  kill "$SERVICE_PID" 2>/dev/null || true
}
trap cleanup EXIT

for i in $(seq 1 30); do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    echo "Service is healthy."
    break
  fi
  echo "Waiting for service to become healthy... ($i/30)"
  sleep 1
  if [ "$i" -eq 30 ]; then
    echo "Service failed to start within 30 seconds." >&2
    exit 1
  fi
done

echo "Running API Test Kit demo..."
python3 api_test_kit.py --frontend-dir samples/frontend --backend-dir samples/backend --mode api --api-base-url http://localhost:5000

echo "Demo complete. Generated files are in artifacts/."
