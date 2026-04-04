from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlite_store import (
    connect,
    export_results_csv,
    fetch_result,
    healthcheck,
    init_db,
    list_results,
)


@dataclass
class SQLiteClient:
    db_path: Path

    @property
    def enabled(self) -> bool:
        return True

    def healthcheck(self) -> tuple[bool, str]:
        conn = connect(self.db_path)
        try:
            init_db(conn)
            return healthcheck(conn)
        finally:
            conn.close()

    def list_results(
        self,
        *,
        page: int,
        page_size: int,
        search: str,
        quick_filter: str,
        sort_by: str,
        sort_dir: str,
    ) -> tuple[list[dict[str, Any]], int]:
        conn = connect(self.db_path)
        try:
            init_db(conn)
            return list_results(
                conn,
                page=page,
                page_size=page_size,
                search=search,
                quick_filter=quick_filter,
                sort_by=sort_by,
                sort_dir=sort_dir,
            )
        finally:
            conn.close()

    def fetch_result(self, record_id: int) -> dict[str, Any] | None:
        conn = connect(self.db_path)
        try:
            init_db(conn)
            return fetch_result(conn, record_id)
        finally:
            conn.close()

    def export_results_csv(
        self,
        *,
        search: str,
        quick_filter: str,
        sort_by: str,
        sort_dir: str,
    ) -> tuple[str, int]:
        conn = connect(self.db_path)
        try:
            init_db(conn)
            return export_results_csv(
                conn,
                search=search,
                quick_filter=quick_filter,
                sort_by=sort_by,
                sort_dir=sort_dir,
            )
        finally:
            conn.close()
