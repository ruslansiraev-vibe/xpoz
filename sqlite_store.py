from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = PROJECT_DIR / "data.db"

ANALYSIS_JSON_COLUMNS = {
    "monetization_signals",
    "other_socials",
    "bio_keywords",
    "cta_keywords",
}
ANALYSIS_BOOL_COLUMNS = {"reels_performance", "low_performing_reels", "post_engagement", "monetization"}
ANALYSIS_EXTRA_COLUMNS = {
    "offer_type": "TEXT DEFAULT 'unknown'",
    "offer_type_confidence": "REAL DEFAULT 0",
    "funnel_type": "TEXT DEFAULT 'unknown'",
    "business_model": "TEXT DEFAULT 'unknown'",
    "audience_type": "TEXT DEFAULT 'unknown'",
    "monetization_strength": "TEXT DEFAULT 'none'",
    "platform_mix": "TEXT DEFAULT 'instagram_only'",
    "primary_domain": "TEXT",
    "bio_keywords": "TEXT",
    "cta_keywords": "TEXT",
    "language": "TEXT DEFAULT 'unknown'",
    "geo_hint": "TEXT DEFAULT 'unknown'",
    "icp": "TEXT DEFAULT 'unknown'",
}

RESULT_COLUMNS = [
    "id",
    "username",
    "analyzed_at",
    "follower_count",
    "posts_analyzed",
    "reels_performance",
    "reels_90d_count",
    "reels_above_150pct",
    "low_performing_reels",
    "bottom10_avg_views",
    "post_engagement",
    "engagement_rate_pct",
    "total_interactions",
    "monetization",
    "monetization_signals",
    "monetization_reason",
    "offer_type",
    "offer_type_confidence",
    "funnel_type",
    "business_model",
    "audience_type",
    "monetization_strength",
    "platform_mix",
    "primary_domain",
    "bio_keywords",
    "cta_keywords",
    "language",
    "geo_hint",
    "youtube_url",
    "twitter_url",
    "twitter_followers",
    "other_socials",
    "error",
    "llm_cost_usd",
    "xpoz_results_used",
    "icp",
]

# Те же поля, что и в RESULT_COLUMNS — полная строка для таблицы /results
LIST_COLUMNS = RESULT_COLUMNS

SORTABLE_COLUMNS = {
    "id",
    "username",
    "analyzed_at",
    "follower_count",
    "engagement_rate_pct",
    "xpoz_results_used",
    "offer_type",
    "monetization_strength",
    "icp",
}

DISCOVERY_COLUMNS = [
    "id",
    "created_at",
    "seed_username",
    "seed_offer_type",
    "seed_icp",
    "seed_query",
    "candidate_username",
    "candidate_source",
    "candidate_reason",
    "confidence",
    "status",
]


def get_db_path() -> Path:
    raw = os.environ.get("XPOZ_DB_PATH", "").strip()
    if not raw:
        return DEFAULT_DB_PATH
    return Path(raw) if os.path.isabs(raw) else (PROJECT_DIR / raw)


def safe_col(name: str) -> str:
    return str(name).replace('"', '""')


def _first_value(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _normalize_login(raw: str) -> str:
    value = (raw or "").strip().strip('"').strip("'")
    if not value:
        return ""
    lowered = value.lower()
    if "instagram.com" in lowered:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        parts = [part for part in parsed.path.split("/") if part]
        if not parts:
            return ""
        candidate = parts[0].lstrip("@").strip()
        if candidate.lower() in {"p", "reel", "reels", "stories", "explore"} and len(parts) > 1:
            candidate = parts[1].lstrip("@").strip()
        return candidate
    return value.lstrip("@").strip()


def _normalize_email(raw: str) -> str:
    value = (raw or "").strip()
    return value if value else ""


def _normalize_fol_cnt(raw: str) -> str:
    value = (raw or "").strip()
    return value if value else ""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or get_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            follower_count INTEGER,
            posts_analyzed INTEGER,
            reels_performance INTEGER,
            reels_90d_count INTEGER,
            reels_above_150pct INTEGER,
            low_performing_reels INTEGER,
            bottom10_avg_views REAL,
            post_engagement INTEGER,
            engagement_rate_pct REAL,
            total_interactions INTEGER,
            monetization INTEGER,
            monetization_signals TEXT,
            monetization_reason TEXT,
            offer_type TEXT DEFAULT 'unknown',
            offer_type_confidence REAL DEFAULT 0,
            funnel_type TEXT DEFAULT 'unknown',
            business_model TEXT DEFAULT 'unknown',
            audience_type TEXT DEFAULT 'unknown',
            monetization_strength TEXT DEFAULT 'none',
            platform_mix TEXT DEFAULT 'instagram_only',
            primary_domain TEXT,
            bio_keywords TEXT,
            cta_keywords TEXT,
            language TEXT DEFAULT 'unknown',
            geo_hint TEXT DEFAULT 'unknown',
            youtube_url TEXT,
            twitter_url TEXT,
            twitter_followers INTEGER,
            other_socials TEXT,
            error TEXT,
            llm_cost_usd REAL DEFAULT 0,
            xpoz_results_used INTEGER DEFAULT 0,
            icp TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            _rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            login TEXT,
            email TEXT,
            fol_cnt TEXT
        )
        """
    )
    existing_accounts_cols = set(_existing_columns(conn, "accounts"))
    for col in ("login", "email", "fol_cnt"):
        if col not in existing_accounts_cols:
            conn.execute(f'ALTER TABLE accounts ADD COLUMN "{col}" TEXT')
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS discovery_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            seed_username TEXT NOT NULL,
            seed_offer_type TEXT,
            seed_icp TEXT,
            seed_query TEXT NOT NULL,
            candidate_username TEXT,
            candidate_source TEXT,
            candidate_reason TEXT,
            confidence REAL DEFAULT 0,
            status TEXT DEFAULT 'new'
        )
        """
    )
    existing_analysis_cols = set(_existing_columns(conn, "analysis_results"))
    for col, ddl in ANALYSIS_EXTRA_COLUMNS.items():
        if col not in existing_analysis_cols:
            conn.execute(f'ALTER TABLE analysis_results ADD COLUMN "{safe_col(col)}" {ddl}')
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_username ON analysis_results(username)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_analyzed_at ON analysis_results(analyzed_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_reels_perf ON analysis_results(reels_performance)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_low_reels ON analysis_results(low_performing_reels)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_engagement ON analysis_results(post_engagement)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_monetization ON analysis_results(monetization)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_follower_count ON analysis_results(follower_count)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_eng_rate ON analysis_results(engagement_rate_pct)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_offer_type ON analysis_results(offer_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_icp ON analysis_results(icp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_monetization_strength ON analysis_results(monetization_strength)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_platform_mix ON analysis_results(platform_mix)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_login ON accounts(login)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_email ON accounts(email)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_fol_cnt ON accounts(fol_cnt)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dc_seed_username ON discovery_candidates(seed_username)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dc_candidate_username ON discovery_candidates(candidate_username)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dc_status ON discovery_candidates(status)")
    conn.commit()


def _existing_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def ensure_accounts_columns(conn: sqlite3.Connection, headers: list[str]) -> None:
    init_db(conn)
    existing = set(_existing_columns(conn, "accounts"))
    for header in headers:
        if header not in existing:
            conn.execute(f'ALTER TABLE accounts ADD COLUMN "{safe_col(header)}" TEXT')
    conn.commit()


def sync_accounts_csv(conn: sqlite3.Connection, csv_path: str) -> int:
    init_db(conn)
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = list(reader.fieldnames or [])
        ensure_accounts_columns(conn, headers)
        existing = set(_existing_columns(conn, "accounts"))
        for required_col in ("login", "email", "fol_cnt"):
            if required_col not in existing:
                conn.execute(f'ALTER TABLE accounts ADD COLUMN "{required_col}" TEXT')
        conn.execute("DELETE FROM accounts")
        if not headers:
            conn.commit()
            return 0
        insert_cols = ["login", "email", "fol_cnt", *headers]
        col_list = ", ".join(f'"{safe_col(col)}"' for col in insert_cols)
        placeholders = ", ".join(["?"] * len(insert_cols))
        rows = []
        for row in reader:
            login = _normalize_login(
                _first_value(
                    row,
                    [
                        "login",
                        "username",
                        "user",
                        "handle",
                        "instagram",
                        "instagram_username",
                        "ownerInstagram",
                    ],
                )
            )
            email = _normalize_email(_first_value(row, ["email", "ownerEmail", "contact_email"]))
            fol_cnt = _normalize_fol_cnt(_first_value(row, ["fol_cnt", "follower_count", "followers", "followersCount"]))
            raw_values = [("" if row.get(col) is None else str(row.get(col, "")).strip()) for col in headers]
            rows.append([login, email, fol_cnt, *raw_values])
        if rows:
            conn.executemany(
                f"INSERT INTO accounts ({col_list}) VALUES ({placeholders})",
                rows,
            )
        conn.commit()
        return len(rows)


def get_analyzed_set(conn: sqlite3.Connection, limit: int = 50000) -> set[str]:
    rows = conn.execute(
        "SELECT username FROM analysis_results ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return {str(row["username"]).strip() for row in rows if row["username"]}


def get_retryable_usernames(conn: sqlite3.Connection, limit: int = 10000) -> list[str]:
    retryable_keywords = ("usage limit", "quota", "exhausted", "unexpected error", "timed out")
    rows = conn.execute(
        "SELECT username, error FROM analysis_results WHERE error IS NOT NULL ORDER BY id DESC"
    ).fetchall()
    result: list[str] = []
    seen = set()
    for row in rows:
        username = str(row["username"] or "").strip()
        error = str(row["error"] or "").lower()
        if not username or username in seen:
            continue
        if any(keyword in error for keyword in retryable_keywords):
            seen.add(username)
            result.append(username)
        if len(result) >= limit:
            break
    return result


def _filter_sql(extra_filter: str, available_cols: set[str]) -> tuple[str, list[str]]:
    if not extra_filter:
        return "", []
    col, _, value = extra_filter.partition("=")
    col = col.strip()
    value = value.strip()
    if not col or not value or col not in available_cols:
        return "", []
    return f' AND "{safe_col(col)}" = ?', [value]


def query_filtered_usernames(
    conn: sqlite3.Connection,
    *,
    limit: int,
    skip_analyzed: bool,
    extra_filter: str = "",
) -> list[str]:
    init_db(conn)
    done_set = get_analyzed_set(conn) if skip_analyzed else set()
    available_cols = set(_existing_columns(conn, "accounts"))
    where_filter, params = _filter_sql(extra_filter, available_cols)
    rows = conn.execute(
        f"""
        SELECT login
        FROM accounts
        WHERE COALESCE(TRIM(login), '') != ''
          AND COALESCE(TRIM(email), '') NOT IN ('', '0')
          AND CAST(REPLACE(REPLACE(COALESCE(fol_cnt, '0'), ',', ''), ' ', '') AS INTEGER) > 3000
          {where_filter}
        ORDER BY CAST(REPLACE(REPLACE(COALESCE(fol_cnt, '0'), ',', ''), ' ', '') AS INTEGER) DESC, _rowid ASC
        """,
        params,
    ).fetchall()
    usernames = [str(row["login"]).strip().lstrip("@") for row in rows if row["login"]]
    if done_set:
        usernames = [username for username in usernames if username not in done_set]
    return usernames[:limit]


def query_all_usernames(
    conn: sqlite3.Connection,
    *,
    limit: int,
    extra_filter: str = "",
    skip_analyzed: bool = False,
) -> list[str]:
    init_db(conn)
    done_set = get_analyzed_set(conn) if skip_analyzed else set()
    available_cols = set(_existing_columns(conn, "accounts"))
    where_filter, params = _filter_sql(extra_filter, available_cols)
    rows = conn.execute(
        f"""
        SELECT login
        FROM accounts
        WHERE COALESCE(TRIM(login), '') != ''
          {where_filter}
        ORDER BY CAST(REPLACE(REPLACE(COALESCE(fol_cnt, '0'), ',', ''), ' ', '') AS INTEGER) DESC, _rowid ASC
        """,
        params,
    ).fetchall()
    usernames = [str(row["login"]).strip().lstrip("@") for row in rows if row["login"]]
    if done_set:
        usernames = [username for username in usernames if username not in done_set]
    return usernames[:limit]


def upsert_analysis_result(
    conn: sqlite3.Connection,
    *,
    username: str,
    record: dict[str, Any],
) -> int:
    init_db(conn)
    conn.execute(
        "DELETE FROM analysis_results WHERE username = ? AND error IS NOT NULL",
        (username,),
    )
    columns = [col for col in RESULT_COLUMNS if col != "id"]
    values = []
    for col in columns:
        value = record.get(col)
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        if col in ANALYSIS_BOOL_COLUMNS and value is not None:
            value = int(bool(value))
        values.append(value)
    placeholders = ", ".join(["?"] * len(columns))
    col_list = ", ".join(columns)
    cur = conn.execute(
        f"INSERT INTO analysis_results ({col_list}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    return int(cur.lastrowid)


def get_stats(conn: sqlite3.Connection) -> dict[str, int]:
    init_db(conn)
    total = conn.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0]
    passed = conn.execute(
        """
        SELECT COUNT(*)
        FROM analysis_results
        WHERE reels_performance = 1
          AND low_performing_reels = 1
          AND post_engagement = 1
          AND error IS NULL
        """
    ).fetchone()[0]
    monetized = conn.execute(
        "SELECT COUNT(*) FROM analysis_results WHERE monetization = 1"
    ).fetchone()[0]
    return {
        "total_analyzed": int(total),
        "passed_all": int(passed),
        "with_monetization": int(monetized),
    }


def healthcheck(conn: sqlite3.Connection) -> tuple[bool, str]:
    try:
        init_db(conn)
        total = conn.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0]
        return True, f"SQLite reachable, analysis_results count={total}"
    except Exception as exc:
        return False, f"SQLite error: {exc}"


def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ANALYSIS_JSON_COLUMNS:
        value = item.get(key)
        if isinstance(value, str) and value:
            try:
                item[key] = json.loads(value)
            except json.JSONDecodeError:
                pass
    for key in ANALYSIS_BOOL_COLUMNS:
        if key in item and item[key] is not None:
            item[key] = bool(item[key])
    return item


def _results_where(search: str, quick_filter: str) -> tuple[str, list[Any]]:
    clauses = []
    params: list[Any] = []
    search = search.strip()
    if search:
        wildcard = f"%{search}%"
        clauses.append(
            "("
            "username LIKE ? COLLATE NOCASE OR "
            "monetization_reason LIKE ? COLLATE NOCASE OR "
            "offer_type LIKE ? COLLATE NOCASE OR "
            "funnel_type LIKE ? COLLATE NOCASE OR "
            "business_model LIKE ? COLLATE NOCASE OR "
            "audience_type LIKE ? COLLATE NOCASE OR "
            "monetization_strength LIKE ? COLLATE NOCASE OR "
            "platform_mix LIKE ? COLLATE NOCASE OR "
            "primary_domain LIKE ? COLLATE NOCASE OR "
            "icp LIKE ? COLLATE NOCASE OR "
            "youtube_url LIKE ? COLLATE NOCASE OR "
            "twitter_url LIKE ? COLLATE NOCASE OR "
            "error LIKE ? COLLATE NOCASE"
            ")"
        )
        params.extend([wildcard] * 13)
    if quick_filter == "passed":
        clauses.extend(
            [
                "reels_performance = 1",
                "low_performing_reels = 1",
                "post_engagement = 1",
                "error IS NULL",
            ]
        )
    elif quick_filter == "monetization":
        clauses.append("monetization = 1")
    elif quick_filter == "has_error":
        clauses.append("error IS NOT NULL AND TRIM(error) != ''")
    elif quick_filter == "has_youtube":
        clauses.append("youtube_url IS NOT NULL AND TRIM(youtube_url) != ''")
    elif quick_filter == "has_twitter":
        clauses.append("twitter_url IS NOT NULL AND TRIM(twitter_url) != ''")
    elif quick_filter.startswith("offer_"):
        clauses.append("offer_type = ?")
        params.append(quick_filter.removeprefix("offer_"))
    elif quick_filter.startswith("icp_"):
        clauses.append("icp = ?")
        params.append(quick_filter.removeprefix("icp_").upper())
    elif quick_filter.startswith("strength_"):
        clauses.append("monetization_strength = ?")
        params.append(quick_filter.removeprefix("strength_"))
    elif quick_filter.startswith("platform_"):
        clauses.append("platform_mix = ?")
        params.append(quick_filter.removeprefix("platform_"))

    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


def list_results(
    conn: sqlite3.Connection,
    *,
    page: int,
    page_size: int,
    search: str,
    quick_filter: str,
    sort_by: str,
    sort_dir: str,
) -> tuple[list[dict[str, Any]], int]:
    init_db(conn)
    page = max(1, page)
    page_size = max(1, min(page_size, 1000))
    offset = (page - 1) * page_size
    sort_column = sort_by if sort_by in SORTABLE_COLUMNS else "id"
    sort_direction = "ASC" if sort_dir == "asc" else "DESC"
    where_sql, params = _results_where(search, quick_filter)
    total = conn.execute(
        f"SELECT COUNT(*) FROM analysis_results{where_sql}",
        params,
    ).fetchone()[0]
    rows = conn.execute(
        f"""
        SELECT {', '.join(LIST_COLUMNS)}
        FROM analysis_results
        {where_sql}
        ORDER BY {sort_column} {sort_direction}, id {sort_direction}
        LIMIT ? OFFSET ?
        """,
        params + [page_size, offset],
    ).fetchall()
    return [_decode_row(row) for row in rows], int(total)


def fetch_result(conn: sqlite3.Connection, record_id: int) -> dict[str, Any] | None:
    init_db(conn)
    row = conn.execute(
        f"SELECT {', '.join(RESULT_COLUMNS)} FROM analysis_results WHERE id = ?",
        (record_id,),
    ).fetchone()
    return _decode_row(row) if row else None


def export_results_csv(
    conn: sqlite3.Connection,
    *,
    search: str,
    quick_filter: str,
    sort_by: str,
    sort_dir: str,
) -> tuple[str, int]:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=RESULT_COLUMNS)
    writer.writeheader()
    page = 1
    written = 0
    while True:
        rows, _ = list_results(
            conn,
            page=page,
            page_size=1000,
            search=search,
            quick_filter=quick_filter,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
        if not rows:
            break
        for row in rows:
            full_row = fetch_result(conn, row["id"]) or row
            writer.writerow(
                {
                    key: json.dumps(full_row[key], ensure_ascii=False)
                    if isinstance(full_row.get(key), (list, dict))
                    else ("" if full_row.get(key) is None else full_row.get(key))
                    for key in RESULT_COLUMNS
                }
            )
            written += 1
        if len(rows) < 1000:
            break
        page += 1
    return output.getvalue(), written


def export_icp_emails(conn: sqlite3.Connection) -> dict[str, set[str]]:
    init_db(conn)
    rows = conn.execute(
        """
        SELECT ar.username, ar.icp, acc.email
        FROM analysis_results ar
        JOIN accounts acc ON acc.login = ar.username
        WHERE ar.icp IS NOT NULL
          AND TRIM(ar.icp) != ''
          AND acc.email IS NOT NULL
          AND TRIM(acc.email) NOT IN ('', '0')
        """
    ).fetchall()
    segments: dict[str, set[str]] = {
        "ICP1": set(),
        "ICP2": set(),
        "ICP3": set(),
        "ICP4": set(),
        "ICP5": set(),
    }
    for row in rows:
        icp = str(row["icp"]).strip()
        email = str(row["email"]).strip()
        if icp in segments and email:
            segments[icp].add(email)
    return segments


def select_discovery_seeds(
    conn: sqlite3.Connection,
    *,
    limit: int = 250,
    min_followers: int = 5000,
) -> list[dict[str, Any]]:
    init_db(conn)
    rows = conn.execute(
        """
        SELECT username, follower_count, offer_type, funnel_type, business_model,
               audience_type, monetization_strength, platform_mix, primary_domain,
               bio_keywords, cta_keywords, icp, youtube_url, twitter_url
        FROM analysis_results
        WHERE monetization = 1
          AND error IS NULL
          AND TRIM(COALESCE(offer_type, 'unknown')) != 'unknown'
          AND monetization_strength IN ('moderate', 'strong')
          AND COALESCE(follower_count, 0) >= ?
        ORDER BY follower_count DESC, id DESC
        LIMIT ?
        """,
        (min_followers, limit),
    ).fetchall()
    return [_decode_row(row) for row in rows]


def _seed_queries_from_row(row: dict[str, Any]) -> list[str]:
    queries: list[str] = []
    for key in ("cta_keywords", "bio_keywords"):
        for value in row.get(key) or []:
            text = str(value or "").strip().lower()
            if text and text not in queries:
                queries.append(text)
    primary_domain = str(row.get("primary_domain") or "").strip().lower()
    if primary_domain:
        stem = primary_domain.split(".")[0]
        if stem and stem not in queries:
            queries.append(stem)
    offer_type = str(row.get("offer_type") or "").strip().lower()
    if offer_type and offer_type != "unknown" and offer_type not in queries:
        queries.append(offer_type.replace("_", " "))
    return queries[:8]


def replace_discovery_candidates(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    init_db(conn)
    conn.execute("DELETE FROM discovery_candidates")
    if not rows:
        conn.commit()
        return 0
    columns = [col for col in DISCOVERY_COLUMNS if col != "id"]
    placeholders = ", ".join(["?"] * len(columns))
    conn.executemany(
        f"INSERT INTO discovery_candidates ({', '.join(columns)}) VALUES ({placeholders})",
        [[row.get(col) for col in columns] for row in rows],
    )
    conn.commit()
    return len(rows)


def build_discovery_candidate_rows(
    conn: sqlite3.Connection,
    *,
    search_provider: Callable[[str, int], list[dict[str, Any]]] | None = None,
    seed_limit: int = 250,
    per_query_limit: int = 5,
    min_followers: int = 5000,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    seeds = select_discovery_seeds(conn, limit=seed_limit, min_followers=min_followers)
    candidate_rows: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for seed in seeds:
        seed_username = str(seed.get("username") or "").strip()
        if not seed_username:
            continue
        seed_queries = _seed_queries_from_row(seed)
        if not search_provider:
            for query in seed_queries:
                pair = (seed_username, query)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                candidate_rows.append(
                    {
                        "created_at": "",
                        "seed_username": seed_username,
                        "seed_offer_type": seed.get("offer_type") or "unknown",
                        "seed_icp": seed.get("icp") or "unknown",
                        "seed_query": query,
                        "candidate_username": "",
                        "candidate_source": "seed_query",
                        "candidate_reason": "Seed query only; run with a search provider to resolve usernames.",
                        "confidence": 0.3,
                        "status": "seed_only",
                    }
                )
            continue

        for query in seed_queries:
            results = search_provider(query, per_query_limit) or []
            for result in results:
                candidate_username = str(result.get("username") or "").strip().lstrip("@")
                if not candidate_username or candidate_username.lower() == seed_username.lower():
                    continue
                pair = (seed_username.lower(), candidate_username.lower())
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                candidate_rows.append(
                    {
                        "created_at": "",
                        "seed_username": seed_username,
                        "seed_offer_type": seed.get("offer_type") or "unknown",
                        "seed_icp": seed.get("icp") or "unknown",
                        "seed_query": query,
                        "candidate_username": candidate_username,
                        "candidate_source": str(result.get("source") or "xpoz_search"),
                        "candidate_reason": f"Matched via seed query '{query}' from @{seed_username}",
                        "confidence": float(result.get("confidence") or 0.65),
                        "status": "new",
                    }
                )
    return seeds, candidate_rows


def export_seed_accounts_csv(
    conn: sqlite3.Connection,
    *,
    limit: int = 250,
    min_followers: int = 5000,
) -> tuple[str, int]:
    seeds = select_discovery_seeds(conn, limit=limit, min_followers=min_followers)
    fieldnames = [
        "username",
        "follower_count",
        "offer_type",
        "funnel_type",
        "business_model",
        "audience_type",
        "monetization_strength",
        "platform_mix",
        "primary_domain",
        "icp",
        "cta_keywords",
        "bio_keywords",
        "youtube_url",
        "twitter_url",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in seeds:
        writer.writerow(
            {
                key: json.dumps(row.get(key), ensure_ascii=False)
                if isinstance(row.get(key), (list, dict))
                else ("" if row.get(key) is None else row.get(key))
                for key in fieldnames
            }
        )
    return output.getvalue(), len(seeds)


def export_discovery_candidates_csv(conn: sqlite3.Connection) -> tuple[str, int]:
    init_db(conn)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=DISCOVERY_COLUMNS)
    writer.writeheader()
    rows = conn.execute(
        f"SELECT {', '.join(DISCOVERY_COLUMNS)} FROM discovery_candidates ORDER BY id DESC"
    ).fetchall()
    for row in rows:
        writer.writerow(dict(row))
    return output.getvalue(), len(rows)
