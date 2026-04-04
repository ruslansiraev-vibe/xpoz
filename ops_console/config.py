from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from sqlite_store import get_db_path


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
DEPLOY_DIR = PROJECT_DIR / "deploy"
DATA_DIR = BASE_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
UPLOAD_DIR = DATA_DIR / "uploads"
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
LOCAL_ENV_PATH = PROJECT_DIR / "ops_console.local.env"


def _load_local_env() -> None:
    if not LOCAL_ENV_PATH.exists():
        return

    for raw_line in LOCAL_ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        # Override process env so ops_console.local.env always wins over empty exports.
        os.environ[key] = value


_load_local_env()


def _parse_users() -> dict[str, str]:
    raw = os.environ.get("APP_USERS_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}

    users: dict[str, str] = {}
    for username, password in parsed.items():
        if username and isinstance(password, str):
            users[str(username)] = password
    return users


@dataclass(frozen=True)
class Settings:
    app_host: str
    app_port: int
    app_secret: str
    app_users: dict[str, str]
    app_title: str
    db_path: Path
    upload_dir: Path
    anthropic_key_present: bool


def get_settings() -> Settings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    app_secret = os.environ.get("APP_SECRET", "").strip() or secrets.token_urlsafe(32)

    return Settings(
        app_host=os.environ.get("APP_HOST", "0.0.0.0"),
        app_port=int(os.environ.get("APP_PORT", "9004")),
        app_secret=app_secret,
        app_users=_parse_users(),
        app_title=os.environ.get("APP_TITLE", "Xpoz Ops Console"),
        db_path=get_db_path(),
        upload_dir=UPLOAD_DIR,
        anthropic_key_present=bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
    )
