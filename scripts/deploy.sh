#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROOT_DIR="$(cd "${PROJECT_DIR}/.." && pwd)"
LIVE_ROOT="${DEPLOY_LIVE_ROOT:-${ROOT_DIR}}"
LIVE_PROJECT_DIR="${LIVE_ROOT}/xpoz"
SOURCE_DIR="${DEPLOY_SOURCE_DIR:-${LIVE_PROJECT_DIR}}"

# shellcheck source=/root/projects/scripts/lib/deploy_common.sh
source "${ROOT_DIR}/scripts/lib/deploy_common.sh"

log "Deploying xpoz from ${SOURCE_DIR}"

sync_project_tree "${SOURCE_DIR}" "${LIVE_PROJECT_DIR}" "data.db" "data.db-shm" "data.db-wal" "data/uploads/"
install_python_target "${LIVE_PROJECT_DIR}/ops_console/requirements.txt" "${LIVE_PROJECT_DIR}/.deps"
restart_systemd_service "xpoz-ops-console"
bash "${PROJECT_DIR}/scripts/smoke.sh"
