# Xpoz Ops Console

Internal web UI for `xpoz/deploy`.

## What it does

- login-protected operator UI
- batch and single-account runs over existing `deploy/batch_analyze.py`
- CSV upload directly from the batch run form
- run history, status, summary, and log tail
- `analysis_results` table browsing and CSV export
- basic system diagnostics without exposing secrets

## Local config

The app reads optional local overrides from `../ops_console.local.env`.

Expected keys:

- `APP_USERS_JSON`
- `APP_SECRET`
- `APP_PORT`
- `XPOZ_DB_PATH`
- `XPOZ_ACCOUNTS_CSV`
- `XPOZ_API_KEY` (или `XPOZ_API_KEYS` через запятую для ротации)
- `ANTHROPIC_API_KEY`

The checked-in `ops_console.local.env` is gitignored and intended only for local/server bootstrap.

## Install

```bash
cd /root/projects/xpoz
python3 -m pip install -r ops_console/requirements.txt
```

## Run

```bash
cd /root/projects/xpoz
uvicorn ops_console.app:app --host 0.0.0.0 --port 9004
```

By default the UI reads results from local SQLite `data.db`.

Then open `http://127.0.0.1:9004`.

## Systemd

Use:

- `systemd/xpoz-ops-console.service`
- `systemd/xpoz-ops-console.env.example`

Copy the example env file to `systemd/xpoz-ops-console.env`, fill in real values, then install the unit.
