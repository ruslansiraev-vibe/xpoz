#!/usr/bin/env python3
"""
batch_analyze.py — массовый анализ аккаунтов из локального SQLite.

Синхронизирует accounts из CSV в SQLite, запускает analyze_account.py для каждого
параллельно (ThreadPoolExecutor), сохраняет результаты в analysis_results.

Usage:
    python batch_analyze.py --preset email-qualified --workers 20
    python batch_analyze.py --limit 50
    python batch_analyze.py --username whop
    python batch_analyze.py --stats
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from analyze_account import analyze, CriteriaResult
from sqlite_store import (
    _normalize_login,
    connect,
    get_analyzed_set as sqlite_get_analyzed_set,
    get_db_path,
    get_retryable_usernames as sqlite_get_retryable_usernames,
    get_stats as sqlite_get_stats,
    init_db,
    query_all_usernames,
    query_filtered_usernames,
    sync_accounts_csv,
    upsert_analysis_result,
)

CSV_ENV_VAR = "XPOZ_ACCOUNTS_CSV"
DEFAULT_CSV_PATH = os.path.join(os.path.dirname(__file__), "accounts.csv")


# ── Выборка логинов ───────────────────────────────────────────────────────────

def resolve_csv_path(cli_path: str | None = None) -> str:
    path = (cli_path or os.environ.get(CSV_ENV_VAR) or DEFAULT_CSV_PATH).strip()
    if not path:
        return ""
    return path if os.path.isabs(path) else os.path.abspath(path)


def load_usernames_from_txt(txt_path: str) -> list[str]:
    """Один username на строку; пустые и строки, начинающиеся с #, пропускаются."""
    out: list[str] = []
    seen: set[str] = set()
    with open(txt_path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        for line in fh:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            login = _normalize_login(raw)
            if not login or login in seen:
                continue
            seen.add(login)
            out.append(login)
    return out


def load_csv_rows(csv_path: str) -> list[dict]:
    if not csv_path:
        raise FileNotFoundError(
            f"CSV path not provided. Use --csv-file or set {CSV_ENV_VAR}."
        )
    if not os.path.exists(csv_path):
        raise FileNotFoundError(
            f"CSV file not found: {csv_path}. Use --csv-file or set {CSV_ENV_VAR}."
        )

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return [dict(row) for row in reader]


def get_analyzed_set(limit: int = 50000) -> set[str]:
    conn = connect()
    try:
        return sqlite_get_analyzed_set(conn, limit=limit)
    finally:
        conn.close()


def get_filtered_usernames(limit: int = 10000,
                            skip_analyzed: bool = True,
                            extra_filter: str = "") -> list[str]:
    """
    Аккаунты из локальной SQLite accounts: email != '0' И fol_cnt > 3000.
    Сортировка: fol_cnt DESC (крупные блогеры первыми).
    """
    conn = connect()
    try:
        return query_filtered_usernames(
            conn,
            limit=limit,
            skip_analyzed=skip_analyzed,
            extra_filter=extra_filter,
        )
    finally:
        conn.close()


def get_unanalyzed_usernames(limit: int = 100,
                              extra_filter: str = "") -> list[str]:
    """Логины из SQLite accounts, которых ещё нет в analysis_results."""
    conn = connect()
    try:
        return query_all_usernames(
            conn,
            limit=limit,
            extra_filter=extra_filter,
            skip_analyzed=True,
        )
    finally:
        conn.close()


def get_all_usernames(limit: int = 100, extra_filter: str = "") -> list[str]:
    conn = connect()
    try:
        return query_all_usernames(
            conn,
            limit=limit,
            extra_filter=extra_filter,
            skip_analyzed=False,
        )
    finally:
        conn.close()


def get_errored_usernames(limit: int = 10000) -> list[str]:
    conn = connect()
    try:
        return sqlite_get_retryable_usernames(conn, limit=limit)
    finally:
        conn.close()


# ── Сохранение результата ─────────────────────────────────────────────────────

_save_lock = threading.Lock()


def save_result(username: str, res: CriteriaResult,
                llm_cost: float = 0.0, xpoz_results: int = 0) -> bool:
    """Сохранить результат анализа в локальный SQLite analysis_results."""
    socials = res.other_socials or {}
    twitter = socials.get("twitter", {})

    record = {
        "username":              username,
        "analyzed_at":           res.analyzed_at or datetime.utcnow().isoformat(),
        "follower_count":        res.follower_count,
        "posts_analyzed":        res.posts_analyzed,

        "reels_performance":     res.reels_performance,
        "reels_90d_count":       res.reels_90d_count,
        "reels_above_150pct":    res.reels_above_150pct,

        "low_performing_reels":  res.low_performing_reels,
        "bottom10_avg_views":    res.bottom10_avg_views,

        "post_engagement":       res.post_engagement,
        "engagement_rate_pct":   res.engagement_rate_pct,
        "total_interactions":    res.total_interactions,

        "monetization":          res.monetization,
        "monetization_signals":  res.monetization_signals or [],
        "monetization_reason":   res.monetization_reason or "",
        "offer_type":            res.offer_type or "unknown",
        "offer_type_confidence": res.offer_type_confidence or 0.0,
        "funnel_type":           res.funnel_type or "unknown",
        "business_model":        res.business_model or "unknown",
        "audience_type":         res.audience_type or "unknown",
        "monetization_strength": res.monetization_strength or "none",
        "platform_mix":          res.platform_mix or "instagram_only",
        "primary_domain":        res.primary_domain or "",
        "bio_keywords":          res.bio_keywords or [],
        "cta_keywords":          res.cta_keywords or [],
        "language":              res.language or "unknown",
        "geo_hint":              res.geo_hint or "unknown",

        "youtube_url":           socials.get("youtube"),
        "twitter_url":           twitter.get("url") if isinstance(twitter, dict) else None,
        "twitter_followers":     twitter.get("followers") if isinstance(twitter, dict) else None,
        "other_socials":         json.dumps(socials, ensure_ascii=False),

        "error":                 res.error or None,
        "llm_cost_usd":          llm_cost,
        "xpoz_results_used":     xpoz_results,
        "icp":                   res.icp or "unknown",
    }
    conn = connect()
    try:
        upsert_analysis_result(conn, username=username, record=record)
        return True
    except Exception as exc:
        print(f"  ⚠ Ошибка сохранения @{username} в SQLite: {exc}", file=sys.stderr)
        return False
    finally:
        conn.close()


# ── Статистика ────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    conn = connect()
    try:
        return sqlite_get_stats(conn)
    finally:
        conn.close()


# ── Параллельный batch runner ─────────────────────────────────────────────────

class ProgressCounter:
    def __init__(self, total: int):
        self._lock     = threading.Lock()
        self.total     = total
        self.done      = 0
        self.success   = 0
        self.errors    = 0
        self.xpoz      = 0
        self.llm_cost  = 0.0
        self._start    = time.time()

    def update(self, success: bool, xpoz_count: int = 0, llm_cost: float = 0.0):
        with self._lock:
            self.done += 1
            self.xpoz += xpoz_count
            self.llm_cost += llm_cost
            if success:
                self.success += 1
            else:
                self.errors += 1

    def eta_str(self) -> str:
        elapsed = time.time() - self._start
        if self.done == 0:
            return "?"
        rate = self.done / elapsed
        remaining = (self.total - self.done) / rate
        h, r = divmod(int(remaining), 3600)
        m, s = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def line(self) -> str:
        pct = self.done / self.total * 100 if self.total else 0
        return (f"[{self.done:>5}/{self.total}] {pct:5.1f}%  "
                f"ok={self.success} err={self.errors}  "
                f"ETA={self.eta_str()}  "
                f"xpoz={self.xpoz}  cost=${self.llm_cost:.3f}")


def _analyze_one(username: str, verbose: bool, counter: ProgressCounter) -> dict:
    """Worker function: analyse + save. Returns summary dict."""
    try:
        result = analyze(username, verbose=verbose)
        xpoz_count = 21 + (1 if result.other_socials.get("twitter") else 0)
        saved = save_result(username, result, xpoz_results=xpoz_count)
        counter.update(success=not result.error, xpoz_count=xpoz_count)

        flags = (f"C1={result.reels_performance} "
                 f"C2={result.low_performing_reels} "
                 f"C3={result.post_engagement} "
                 f"C4={result.monetization}")
        icon = "✅" if not result.error else "⚠"
        print(f"  {icon} @{username:<30} {flags}  fol={result.follower_count:,}",
              flush=True)
        print(f"     {counter.line()}", flush=True)

        return {"username": username, "ok": True}
    except Exception as e:
        counter.update(success=False)
        print(f"  ❌ @{username}: {e}", flush=True)
        return {"username": username, "ok": False, "error": str(e)}


def run_batch(usernames: list[str], workers: int = 20,
              verbose: bool = False) -> dict:
    total   = len(usernames)
    counter = ProgressCounter(total)

    print(f"\n{'═'*64}")
    print(f"  Batch анализ: {total} аккаунтов  |  workers={workers}")
    print(f"  Ожидаемое время: ~{total * 18 // workers // 60} мин")
    print(f"{'═'*64}\n")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_analyze_one, uname, verbose, counter): uname
            for uname in usernames
        }
        try:
            for fut in as_completed(futures):
                fut.result()  # re-raise exceptions if any
        except KeyboardInterrupt:
            print("\n  ⏹ Прерван. Ожидаю завершения активных задач...")
            pool.shutdown(wait=False, cancel_futures=True)

    elapsed = int(time.time() - counter._start)
    h, r = divmod(elapsed, 3600)
    m, s = divmod(r, 60)

    print(f"\n{'─'*64}")
    print(f"  Готово за {h:02d}:{m:02d}:{s:02d}")
    print(f"  Успешно: {counter.success}  |  Ошибок: {counter.errors}")
    print(f"  Xpoz results использовано: ~{counter.xpoz}")
    print(f"  LLM стоимость: ${counter.llm_cost:.4f}")
    print(f"  Результаты: SQLite → {get_db_path()}")
    print(f"{'─'*64}\n")

    return {
        "success": counter.success,
        "errors":  counter.errors,
        "elapsed_sec": elapsed,
        "xpoz_results": counter.xpoz,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Batch анализ Instagram аккаунтов из CSV с локальным SQLite-хранилищем"
    )
    parser.add_argument("--username",  help="Проанализировать один аккаунт")
    parser.add_argument(
        "--csv-file",
        help=f"CSV с аккаунтами для batch-режима (default: ${CSV_ENV_VAR} or {DEFAULT_CSV_PATH})",
    )
    parser.add_argument(
        "--usernames-file",
        help="TXT: по одному Instagram username на строку (batch без CSV; несовместимо с --csv-file)",
    )
    parser.add_argument("--limit",     type=int, default=100,
                        help="Макс. кол-во аккаунтов (default: 100)")
    parser.add_argument("--workers",   type=int, default=20,
                        help="Параллельных воркеров (default: 20)")
    parser.add_argument("--preset",    choices=["csv-all", "email-qualified"],
                        help="csv-all: все логины из CSV; email-qualified: email!=0 AND fol_cnt>3000")
    parser.add_argument("--filter",    help="Фильтр по accounts: country=США")
    parser.add_argument("--reanalyze", action="store_true",
                        help="Перезапустить все (включая уже проанализированные)")
    parser.add_argument("--retry-errors", action="store_true",
                        help="Повторить только аккаунты с ошибками quota/timeout")
    parser.add_argument("--stats",     action="store_true",
                        help="Показать статистику по analysis_results")
    parser.add_argument("--verbose",   action="store_true",
                        help="Подробный вывод по каждому аккаунту")

    args = parser.parse_args()

    # ── Stats ──────────────────────────────────────────────────────
    if args.stats:
        stats = get_stats()
        print(f"\nСтатистика analysis_results:")
        print(f"  Проанализировано всего   : {stats['total_analyzed']:,}")
        print(f"  Прошли критерии 1-3      : {stats['passed_all']:,}")
        print(f"  С монетизацией (C4=true) : {stats['with_monetization']:,}")
        print(f"\n  SQLite DB:")
        print(f"  {get_db_path()}")
        return

    # ── Single account ─────────────────────────────────────────────
    if args.username:
        result = analyze(args.username, verbose=True)
        save_result(args.username, result)
        print(f"\n  ✓ Сохранено в SQLite → analysis_results")
        return

    # ── Batch из TXT (список username) ────────────────────────────
    if args.usernames_file:
        path = args.usernames_file.strip()
        path = path if os.path.isabs(path) else os.path.abspath(path)
        if not os.path.exists(path):
            print(f"  ⚠ Файл не найден: {path}", file=sys.stderr)
            return
        usernames = load_usernames_from_txt(path)
        print(f"  Источник аккаунтов: TXT → {path}")
        print(f"  Уникальных username в файле: {len(usernames):,}")
        if args.limit and args.limit > 0:
            usernames = usernames[: args.limit]
            print(f"  После limit={args.limit}: {len(usernames):,}")
        if not args.reanalyze:
            done = get_analyzed_set()
            before = len(usernames)
            usernames = [u for u in usernames if u not in done]
            skipped = before - len(usernames)
            if skipped:
                print(f"  Пропущено уже проанализированных: {skipped:,}")
        if not usernames:
            print("  Нет аккаунтов для анализа (список пуст или все уже в analysis_results)")
            print("  Используй --reanalyze для повторного запуска")
            return
        print(f"  Аккаунтов для анализа: {len(usernames):,}")
        run_batch(usernames, workers=args.workers, verbose=args.verbose)
        return

    try:
        csv_path = resolve_csv_path(args.csv_file)
        csv_rows = load_csv_rows(csv_path)
        print(f"  Источник аккаунтов: CSV → {csv_path}")
        print(f"  Загружено строк из CSV: {len(csv_rows):,}")
        conn = connect()
        try:
            init_db(conn)
            imported = sync_accounts_csv(conn, csv_path)
        finally:
            conn.close()
        print(f"  Синхронизировано в SQLite accounts: {imported:,}")
    except FileNotFoundError as exc:
        print(f"  ⚠ {exc}", file=sys.stderr)
        return

    # ── Build username list ────────────────────────────────────────
    if args.preset == "email-qualified":
        print(f"  Загружаю список: email-qualified (fol_cnt>3000, email!=0)...")
        limit = args.limit if args.limit != 100 else 10000  # all by default for preset
        usernames = get_filtered_usernames(limit=limit, skip_analyzed=not args.reanalyze, extra_filter=args.filter or "")
    elif args.preset == "csv-all":
        print("  Загружаю список: все логины из CSV...")
        if args.reanalyze:
            usernames = get_all_usernames(args.limit, args.filter or "")
        else:
            usernames = get_unanalyzed_usernames(args.limit, args.filter or "")
    elif getattr(args, "retry_errors", False):
        print(f"  Загружаю аккаунты с ошибками quota/timeout...")
        limit = args.limit if args.limit != 100 else 10000
        usernames = get_errored_usernames(limit=limit)
        print(f"  Найдено ретраиваемых: {len(usernames):,}")
    elif args.reanalyze:
        usernames = get_all_usernames(args.limit, args.filter or "")
    else:
        usernames = get_unanalyzed_usernames(args.limit, args.filter or "")

    if not usernames:
        print("  Нет аккаунтов для анализа (все уже обработаны или список пуст)")
        print("  Используй --reanalyze для повторного запуска")
        return

    print(f"  Аккаунтов для анализа: {len(usernames):,}")
    run_batch(usernames, workers=args.workers, verbose=args.verbose)


if __name__ == "__main__":
    main()
