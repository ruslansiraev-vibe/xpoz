#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.error
import urllib.request

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from sqlite_store import RESULT_COLUMNS, connect, init_db, upsert_analysis_result


SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
PAGE_SIZE = 1000


def _fetch_page(select_columns: list[str], offset: int) -> list[dict]:
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    select = urllib.parse.quote(",".join(select_columns), safe=",")
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/analysis_results?select={select}&limit={PAGE_SIZE}&offset={offset}",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_all_results() -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY are required for migration.")
    rows: list[dict] = []
    offset = 0
    select_columns = [column for column in RESULT_COLUMNS if column != "icp"]
    while True:
        try:
            batch = _fetch_page(select_columns, offset)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 400 and "analysis_results.icp" in detail and "icp" in select_columns:
                select_columns = [column for column in select_columns if column != "icp"]
                batch = _fetch_page(select_columns, offset)
            else:
                raise RuntimeError(f"Supabase HTTP {exc.code}: {detail}") from exc
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        print(f"  ... fetched {len(rows):,} rows", flush=True)
    return rows


def main() -> None:
    print("Fetching analysis_results from Supabase...")
    rows = fetch_all_results()
    print(f"  Found {len(rows):,} rows")

    conn = connect()
    try:
        init_db(conn)
        conn.execute("DELETE FROM analysis_results")
        conn.commit()
        for row in rows:
            username = str(row.get("username") or "").strip()
            if not username:
                continue
            upsert_analysis_result(conn, username=username, record=row)
    finally:
        conn.close()
    print("Done. analysis_results migrated to SQLite.")


if __name__ == "__main__":
    main()
