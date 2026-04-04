#!/usr/bin/env bash
# Одноразовая настройка: приложение в TARGET ведётся через git (clone/pull).
# Если сейчас развёрнуто через rsync без .git — сохраняем секреты и ops_console/data,
# переименовываем дерево, затем install_xpoz_var_www.sh клонирует репозиторий; секреты и data возвращаются.
#
# Запуск на VPS от root (скрипты должны лежать на диске — возьмите их из клона):
#   git clone --depth 1 --branch main https://github.com/ruslansiraev-vibe/xpoz.git /tmp/xpoz-deploy
#   bash /tmp/xpoz-deploy/scripts/server/setup_git_deploy.sh
#   rm -rf /tmp/xpoz-deploy
#
# Переменные окружения:
#   REPO_URL   — URL репозитория (по умолчанию https://github.com/ruslansiraev-vibe/xpoz.git)
#   TARGET     — /var/www/xpoz
#   GIT_REF    — main
#   PORT       — 9000
#
# Приватный репозиторий: Deploy keys (read-only) в GitHub → REPO_URL=git@github.com:ORG/xpoz.git
#
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/ruslansiraev-vibe/xpoz.git}"
TARGET="${TARGET:-/var/www/xpoz}"
PORT="${PORT:-9000}"
GIT_REF="${GIT_REF:-main}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_SH="${SCRIPT_DIR}/install_xpoz_var_www.sh"

export REPO_URL TARGET PORT GIT_REF

LAST_BACKUP=""

backup_and_migrate_tree() {
  if [[ ! -d "$TARGET" ]] || [[ -d "$TARGET/.git" ]]; then
    return 0
  fi

  local b="/root/xpoz-backup-$(date +%s)"
  mkdir -p "$b"
  echo "==> Каталог без .git — резервная копия в $b"

  if [[ -f "$TARGET/ops_console.local.env" ]]; then
    cp -a "$TARGET/ops_console.local.env" "$b/"
  fi
  if [[ -f "$TARGET/systemd/xpoz-ops-console.env" ]]; then
    cp -a "$TARGET/systemd/xpoz-ops-console.env" "$b/xpoz-ops-console.env"
  fi
  if [[ -d "$TARGET/ops_console/data" ]]; then
    cp -a "$TARGET/ops_console/data" "$b/ops_console_data"
  fi

  echo "==> Перенос текущего дерева в ${TARGET}.pre-git"
  mv "$TARGET" "${TARGET}.pre-git"
  echo "==> После проверки можно удалить: rm -rf ${TARGET}.pre-git"
  LAST_BACKUP="$b"
}

restore_from_backup_dir() {
  local b="$1"
  [[ -d "$b" ]] || return 0
  echo "==> Восстановление секретов и данных из $b"
  if [[ -f "$b/ops_console.local.env" ]]; then
    cp -a "$b/ops_console.local.env" "$TARGET/"
  fi
  if [[ -f "$b/xpoz-ops-console.env" ]]; then
    mkdir -p "$TARGET/systemd"
    cp -a "$b/xpoz-ops-console.env" "$TARGET/systemd/xpoz-ops-console.env"
  fi
  if [[ -d "$b/ops_console_data" ]]; then
    mkdir -p "$TARGET/ops_console/data"
    cp -a "$b/ops_console_data/." "$TARGET/ops_console/data/"
  fi
}

if [[ -d "$TARGET" ]] && [[ ! -d "$TARGET/.git" ]]; then
  backup_and_migrate_tree
fi

if [[ ! -f "$INSTALL_SH" ]]; then
  echo "Не найден $INSTALL_SH — клонируйте репозиторий и запустите: bash scripts/server/setup_git_deploy.sh" >&2
  exit 1
fi

bash "$INSTALL_SH"

git config --global --add safe.directory "$TARGET" 2>/dev/null || true

if [[ -n "${LAST_BACKUP}" ]]; then
  restore_from_backup_dir "$LAST_BACKUP"
  systemctl restart xpoz-ops-console.service || true
  echo "==> Сервис перезапущен после восстановления env/data"
fi

echo "==> Обновления с GitHub: bash $SCRIPT_DIR/git_pull_deploy.sh"
