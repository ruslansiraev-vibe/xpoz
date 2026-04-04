#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROOT_DIR="$(cd "${PROJECT_DIR}/.." && pwd)"

# shellcheck source=/root/projects/scripts/lib/deploy_common.sh
source "${ROOT_DIR}/scripts/lib/deploy_common.sh"

PORT="${APP_PORT:-9004}"
URL="http://127.0.0.1:${PORT}/healthz"

log "Smoke checking xpoz at ${URL}"
wait_for_json_flag "${URL}" "ok" 20 2
