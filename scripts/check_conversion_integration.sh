#!/usr/bin/env bash
# Isolated two-image integration; paid calls only with explicit --models.
set -euo pipefail
api_image="${1:?API image required}"
converter_image="${2:?Converter image required}"
suffix="conversion-integration-$$"
temporary="$(mktemp -d)"
external=""
extra_network=()
tests=(tests/test_converter_integration.py)
if [[ "${3:-}" == "--models" ]]; then
  test -n "${ZHITIAN_INTEGRATION_MODEL_KEY:-}"
  tests=()
  external="$suffix-model"
  extra_network=(--network "$external")
fi
cleanup() {
  docker rm -f "$suffix" >/dev/null 2>&1 || true
  docker network rm "$suffix" >/dev/null 2>&1 || true
  if [[ -n "$external" ]]; then docker network rm "$external" >/dev/null 2>&1 || true; fi
  # Only this mktemp-created directory; never a business directory.
  rm -r -- "$temporary"
}
trap cleanup EXIT
docker network create --internal "$suffix"
if [[ -n "$external" ]]; then docker network create "$external"; fi
key='ci-only-conversion-integration-key-at-least-32-bytes'
docker run -d --name "$suffix" --network "$suffix" --init --read-only \
  --tmpfs /tmp:size=268435456,mode=1777 --memory 768m --cpus 1 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  -e CONVERSION_SERVICE_KEY="$key" "$converter_image"
docker exec "$suffix" python -m converter_service.integration_fixtures /tmp/integration-fixtures
mkdir "$temporary/fixtures"
# docker cp cannot archive some tmpfs mounts; copy only these synthetic fixtures via stdout.
docker exec "$suffix" tar -C /tmp/integration-fixtures -cf - . | tar -C "$temporary/fixtures" -xf -
docker run --rm --network "$suffix" "${extra_network[@]}" --entrypoint sh \
  --mount "type=bind,src=$PWD/tests,dst=/app/tests,readonly" \
  --mount "type=bind,src=$temporary/fixtures,dst=/fixtures,readonly" \
  -e CONVERSION_SERVICE_URL="http://$suffix:8001" -e CONVERSION_SERVICE_KEY="$key" \
  -e ZHITIAN_INTEGRATION_MODEL_KEY -e FILE_CONVERSION_FIXTURES_DIR=/fixtures "$api_image" -c \
  'python -m venv --system-site-packages /tmp/.venv && /tmp/.venv/bin/python -m pytest -q -m integration "$@"' sh "${tests[@]}"
