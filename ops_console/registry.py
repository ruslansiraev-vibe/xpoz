from __future__ import annotations

import json
import os
import signal
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunRegistry:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self.init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_type TEXT NOT NULL,
                    source_script TEXT NOT NULL,
                    status TEXT NOT NULL,
                    params_json TEXT NOT NULL DEFAULT '{}',
                    pid INTEGER,
                    started_at TEXT,
                    finished_at TEXT,
                    triggered_by TEXT,
                    result_summary_json TEXT NOT NULL DEFAULT '{}',
                    error_summary TEXT,
                    log_path TEXT,
                    stop_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_run_id INTEGER NOT NULL,
                    at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(job_run_id) REFERENCES job_runs(id)
                )
                """
            )

    def _row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["params"] = json.loads(item.pop("params_json") or "{}")
        item["result_summary"] = json.loads(item.pop("result_summary_json") or "{}")
        item["stop_requested"] = bool(item["stop_requested"])
        return item

    def create_run(
        self,
        *,
        job_type: str,
        source_script: str,
        params: dict[str, Any],
        triggered_by: str,
        log_path: str,
    ) -> dict[str, Any]:
        now = utcnow_iso()
        with self._lock, self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO job_runs (
                    job_type, source_script, status, params_json, triggered_by,
                    log_path, created_at, updated_at
                )
                VALUES (?, ?, 'queued', ?, ?, ?, ?, ?)
                """,
                (
                    job_type,
                    source_script,
                    json.dumps(params, ensure_ascii=False),
                    triggered_by,
                    log_path,
                    now,
                    now,
                ),
            )
            run_id = cur.lastrowid
        self.append_event(run_id, "info", "created", "Run created", params)
        return self.get_run(run_id)

    def append_event(
        self,
        run_id: int,
        level: str,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO job_events (job_run_id, at, level, event_type, message, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    utcnow_iso(),
                    level,
                    event_type,
                    message,
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )

    def update_run(self, run_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utcnow_iso()
        columns = []
        values: list[Any] = []
        for key, value in fields.items():
            db_key = f"{key}_json" if key in {"params", "result_summary"} else key
            if key in {"params", "result_summary"}:
                value = json.dumps(value or {}, ensure_ascii=False)
            columns.append(f"{db_key} = ?")
            values.append(value)
        values.append(run_id)
        with self._lock, self.connect() as conn:
            conn.execute(f"UPDATE job_runs SET {', '.join(columns)} WHERE id = ?", values)

    def mark_started(self, run_id: int, pid: int) -> None:
        self.update_run(run_id, status="running", pid=pid, started_at=utcnow_iso())
        self.append_event(run_id, "info", "started", "Run started", {"pid": pid})

    def mark_finished(
        self,
        run_id: int,
        *,
        status: str,
        summary: dict[str, Any] | None = None,
        error_summary: str | None = None,
    ) -> None:
        self.update_run(
            run_id,
            status=status,
            finished_at=utcnow_iso(),
            result_summary=summary or {},
            error_summary=error_summary,
        )
        self.append_event(
            run_id,
            "info" if status == "completed" else "warning",
            status,
            f"Run finished with status={status}",
            summary or {},
        )

    def list_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM job_runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_active_runs(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM job_runs WHERE status IN ('queued', 'starting', 'running') ORDER BY id DESC"
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM job_runs WHERE id = ?", (run_id,)).fetchone()
        return self._row_to_dict(row)

    def get_events(self, run_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM job_events
                WHERE job_run_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
        items = []
        for row in reversed(rows):
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            items.append(item)
        return items

    def request_stop(self, run_id: int) -> None:
        self.update_run(run_id, stop_requested=1)
        self.append_event(run_id, "warning", "stop_requested", "Stop requested")

    def has_active_batch(self) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM job_runs
                WHERE job_type = 'batch'
                  AND status IN ('queued', 'starting', 'running')
                LIMIT 1
                """
            ).fetchone()
        return row is not None

    def finalize_stale_runs(self, parse_summary) -> None:
        for run in self.list_active_runs():
            pid = run.get("pid")
            if pid and self._pid_exists(pid):
                self.update_run(run["id"], status="running")
                continue

            summary = parse_summary(Path(run["log_path"])) if run.get("log_path") else {}
            status = "cancelled" if run.get("stop_requested") else ("completed" if summary.get("completed") else "failed")
            error_summary = None if status == "completed" else "Run ended while app was offline or process exited unexpectedly."
            self.mark_finished(run["id"], status=status, summary=summary, error_summary=error_summary)

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def send_stop_signal(self, run_id: int) -> bool:
        run = self.get_run(run_id)
        if not run or not run.get("pid"):
            return False
        self.request_stop(run_id)
        try:
            os.killpg(run["pid"], signal.SIGTERM)
        except OSError:
            return False
        self.append_event(run_id, "warning", "sigterm", "SIGTERM sent to process group", {"pid": run["pid"]})
        return True
