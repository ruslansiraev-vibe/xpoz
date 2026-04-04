#!/usr/bin/env bash
# Обычное обновление: git pull + зависимости + перезапуск systemd.
#
# Запуск на VPS от root:
#   TARGET=/var/www/xpoz/xpoz bash scripts/server/git_pull_deploy.sh
# (если репозиторий вложен в монорепо; иначе по умолчанию TARGET=/var/www/xpoz)
#
# Переменные:
#   TARGET  — корень клона git (где лежит .git и ops_console/)
#   GIT_REF — main
#   GIT_PULL_DEPLOY_RESET=1 — после fetch: reset --hard к origin и git clean -fd (убирает
#     локальные правки в отслеживаемых файлах и неотслеживаемые дубликаты). Игнорируемые
#     git файлы (.env, data.db, ops_console/data/) не удаляются.
#
set -euo pipefail

TARGET="${TARGET:-/var/www/xpoz}"
GIT_REF="${GIT_REF:-main}"

if [[ ! -d "$TARGET/.git" ]]; then
  echo "В $TARGET нет .git — сначала выполните scripts/server/setup_git_deploy.sh (или install_xpoz_var_www.sh)." >&2
  exit 1
fi

# После rsync владелец файлов может не совпадать с пользователем git → «dubious ownership».
_ensure_git_safe_directory() {
  local d="$1"
  if git config --global --get-all safe.directory 2>/dev/null | grep -Fxq "$d"; then
    return 0
  fi
  git config --global --add safe.directory "$d"
  echo "==> git: safe.directory += $d"
}
_ensure_git_safe_directory "$TARGET"

echo "==> git fetch"
git -C "$TARGET" fetch origin

if [[ "${GIT_PULL_DEPLOY_RESET:-0}" == "1" ]]; then
  echo "==> GIT_PULL_DEPLOY_RESET=1: reset --hard origin/$GIT_REF && git clean -fd"
  git -C "$TARGET" reset --hard "origin/$GIT_REF"
  git -C "$TARGET" clean -fd
else
  echo "==> checkout $GIT_REF && pull"
  git -C "$TARGET" checkout "$GIT_REF"
  git -C "$TARGET" pull origin "$GIT_REF"
fi

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
