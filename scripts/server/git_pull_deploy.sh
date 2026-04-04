#!/usr/bin/env bash
# Обычное обновление: git pull + зависимости + перезапуск systemd.
#
# Запуск на VPS от root:
#   bash /var/www/xpoz/scripts/server/git_pull_deploy.sh
#
# Переменные:
#   TARGET  — /var/www/xpoz
#   GIT_REF — main
#
set -euo pipefail

TARGET="${TARGET:-/var/www/xpoz}"
GIT_REF="${GIT_REF:-main}"

if [[ ! -d "$TARGET/.git" ]]; then
  echo "В $TARGET нет .git — сначала выполните scripts/server/setup_git_deploy.sh (или install_xpoz_var_www.sh)." >&2
  exit 1
fi

echo "==> git fetch / checkout $GIT_REF / pull"
git -C "$TARGET" fetch origin
git -C "$TARGET" checkout "$GIT_REF"
git -C "$TARGET" pull origin "$GIT_REF"

if [[ ! -x "$TARGET/.venv/bin/pip" ]]; then
  echo "==> Создание venv"
  python3 -m venv "$TARGET/.venv"
fi

echo "==> pip install"
# shellcheck disable=SC1091
source "$TARGET/.venv/bin/activate"
pip install -U pip wheel -q
pip install -r "$TARGET/ops_console/requirements.txt" -q

echo "==> restart xpoz-ops-console"
systemctl restart xpoz-ops-console.service
systemctl --no-pager -l status xpoz-ops-console.service | head -25
