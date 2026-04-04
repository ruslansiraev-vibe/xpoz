#!/usr/bin/env python3
"""
export_discovery.py — build seed exports and discovery candidates from local SQLite.

By default it writes:
- seed_accounts.csv
- discovery_candidates.csv
- discovery_candidates.txt
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from analyze_account import xpoz_call
from sqlite_store import (
    build_discovery_candidate_rows,
    connect,
    export_discovery_candidates_csv,
    export_seed_accounts_csv,
    get_db_path,
    init_db,
    replace_discovery_candidates,
)


def search_instagram_users(query: str, limit: int) -> list[dict]:
    data = xpoz_call("searchInstagramUsers", {"name": query, "limit": limit})
    rows = data.get("results") or data.get("data") or []
    if not isinstance(rows, list):
        return []
    candidates: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        username = str(row.get("username") or row.get("handle") or "").strip().lstrip("@")
        if not username:
            continue
        candidates.append(
            {
                "username": username,
                "source": "xpoz_search",
                "confidence": 0.65,
            }
        )
        if len(candidates) >= limit:
            break
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Xpoz discovery seeds and candidates")
    parser.add_argument("--output-dir", default=".", help="Directory for CSV/TXT exports")
    parser.add_argument("--seed-limit", type=int, default=250, help="Max number of seed accounts")
    parser.add_argument("--per-query-limit", type=int, default=5, help="Max Xpoz search results per query")
    parser.add_argument("--min-followers", type=int, default=5000, help="Seed minimum followers")
    parser.add_argument("--seed-only", action="store_true", help="Only export seed queries, skip Xpoz username search")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = connect()
    try:
        init_db(conn)
        print(f"Reading SQLite: {get_db_path()}")
        seed_csv, seed_count = export_seed_accounts_csv(
            conn,
            limit=max(1, args.seed_limit),
            min_followers=max(0, args.min_followers),
        )
        seed_provider = None if args.seed_only else search_instagram_users
        _, candidate_rows = build_discovery_candidate_rows(
            conn,
            search_provider=seed_provider,
            seed_limit=max(1, args.seed_limit),
            per_query_limit=max(1, args.per_query_limit),
            min_followers=max(0, args.min_followers),
        )
        written = replace_discovery_candidates(conn, candidate_rows)
        candidates_csv, _ = export_discovery_candidates_csv(conn)
    finally:
        conn.close()

    seed_path = output_dir / "seed_accounts.csv"
    candidates_path = output_dir / "discovery_candidates.csv"
    txt_path = output_dir / "discovery_candidates.txt"

    seed_path.write_text(seed_csv, encoding="utf-8")
    candidates_path.write_text(candidates_csv, encoding="utf-8")

    usernames = []
    seen = set()
    for row in candidate_rows:
        username = str(row.get("candidate_username") or "").strip()
        if not username or username in seen:
            continue
        seen.add(username)
        usernames.append(username)
    txt_path.write_text("".join(f"{username}\n" for username in usernames), encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"Seeds exported     : {seed_count:>6} -> {seed_path}")
    print(f"Candidates exported: {written:>6} -> {candidates_path}")
    print(f"Distinct usernames : {len(usernames):>6} -> {txt_path}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
