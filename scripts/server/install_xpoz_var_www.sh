#!/usr/bin/env bash
# Запуск на VPS от root после первого успешного SSH.
# Клонирует репозиторий xpoz в /var/www/xpoz, поднимает Xpoz Ops Console на 0.0.0.0:9000 через systemd.
#
# Использование:
#   curl -fsSL ... | bash
#   или: bash install_xpoz_var_www.sh
#
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/ruslansiraev-vibe/xpoz.git}"
TARGET="${TARGET:-/var/www/xpoz}"
PORT="${PORT:-9000}"
GIT_REF="${GIT_REF:-main}"

echo "==> Target: $TARGET  port: $PORT"

export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq git python3 python3-venv python3-pip
fi

mkdir -p "$(dirname "$TARGET")"
if [[ -d "$TARGET/.git" ]]; then
  echo "==> Git pull in $TARGET"
  git -C "$TARGET" fetch origin
  git -C "$TARGET" checkout "$GIT_REF"
  git -C "$TARGET" pull origin "$GIT_REF"
else
  rm -rf "$TARGET"
  echo "==> Clone $REPO_URL -> $TARGET"
  git clone --branch "$GIT_REF" "$REPO_URL" "$TARGET"
fi

XP="$TARGET"
cd "$XP"
git config --global --add safe.directory "$XP" 2>/dev/null || true

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip wheel -q
pip install -r ops_console/requirements.txt -q

ENV_FILE="$XP/systemd/xpoz-ops-console.env"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$XP/systemd/xpoz-ops-console.env.example" "$ENV_FILE"
  echo "==> Создан $ENV_FILE — заполните APP_USERS_JSON, APP_SECRET, ключи Xpoz/Anthropic"
fi

UNIT="/etc/systemd/system/xpoz-ops-console.service"
cat >"$UNIT" <<UNITEOF
[Unit]
Description=Xpoz Ops Console (/var/www/xpoz)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$XP
EnvironmentFile=$ENV_FILE
Environment=PATH=$XP/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
ExecStart=$XP/.venv/bin/python -m uvicorn ops_console.app:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNITEOF

systemctl daemon-reload
systemctl enable xpoz-ops-console.service
systemctl restart xpoz-ops-console.service

if command -v ufw >/dev/null 2>&1; then
  ufw allow "$PORT/tcp" comment 'xpoz ops' || true
fi

echo "==> Статус:"
systemctl --no-pager -l status xpoz-ops-console.service || true
echo "==> Готово: http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT/ (или внешний IP)"
