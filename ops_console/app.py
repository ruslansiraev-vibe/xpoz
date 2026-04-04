from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response

from sqlite_store import ANALYSIS_BOOL_COLUMNS, RESULT_COLUMNS

from .config import DATA_DIR, DEPLOY_DIR, LOG_DIR, PROJECT_DIR, STATIC_DIR, TEMPLATE_DIR, get_settings
from .registry import RunRegistry
from .sqlite_api import SQLiteClient


settings = get_settings()
registry = RunRegistry(DATA_DIR / "ops_console.db")
sqlite_client = SQLiteClient(settings.db_path)

app = FastAPI(title=settings.app_title)
# Статика до SessionMiddleware — сессия не нужна для /static/*.
# За reverse proxy: запускайте uvicorn с --proxy-headers (или новее Starlette с ProxyHeadersMiddleware).
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.add_middleware(SessionMiddleware, secret_key=settings.app_secret, same_site="lax")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def _as_json_filter(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


templates.env.filters["as_json"] = _as_json_filter


def _head_html() -> HTMLResponse:
    return HTMLResponse(content="", status_code=200, media_type="text/html; charset=utf-8")


@app.exception_handler(HTTPException)
async def http_exception_unauthorized_html(request: Request, exc: HTTPException):
    if exc.status_code != 401:
        return await http_exception_handler(request, exc)
    if request.url.path.startswith("/partials/"):
        return HTMLResponse(
            '<p class="muted">Сессия недействительна. <a href="/login">Войти снова</a></p>',
            status_code=200,
        )
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return RedirectResponse(url="/login", status_code=303)
    return await http_exception_handler(request, exc)


_monitor_threads: dict[int, threading.Thread] = {}


@app.on_event("startup")
def startup() -> None:
    registry.finalize_stale_runs(parse_log_summary)


def now_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def resolve_batch_csv_path() -> Path:
    configured = os.environ.get("XPOZ_ACCOUNTS_CSV", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = (DEPLOY_DIR / path).resolve()
        return path
    return DEPLOY_DIR / "accounts.csv"


def save_uploaded_csv(upload: Any, *, triggered_by: str) -> tuple[Path | None, str | None]:
    original_name = Path(upload.filename or "").name
    if not original_name:
        return None, "CSV файл не выбран."
    suffix = Path(original_name).suffix.lower()
    if suffix and suffix != ".csv":
        return None, "Загружай CSV файл."

    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(original_name).stem).strip("._") or "accounts"
    stored_name = f"{now_slug()}__{triggered_by}__{safe_stem}.csv"
    stored_path = settings.upload_dir / stored_name

    upload.file.seek(0)
    with stored_path.open("wb") as target:
        shutil.copyfileobj(upload.file, target)

    if stored_path.stat().st_size == 0:
        stored_path.unlink(missing_ok=True)
        return None, "Загруженный CSV пустой."
    return stored_path, None


def save_uploaded_txt(upload: Any, *, triggered_by: str) -> tuple[Path | None, str | None]:
    original_name = Path(upload.filename or "").name
    if not original_name:
        return None, "TXT файл не выбран."
    suffix = Path(original_name).suffix.lower()
    if suffix and suffix not in (".txt", ".text"):
        return None, "Загружай .txt файл."

    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(original_name).stem).strip("._") or "usernames"
    stored_name = f"{now_slug()}__{triggered_by}__{safe_stem}.txt"
    stored_path = settings.upload_dir / stored_name

    upload.file.seek(0)
    with stored_path.open("wb") as target:
        shutil.copyfileobj(upload.file, target)

    if stored_path.stat().st_size == 0:
        stored_path.unlink(missing_ok=True)
        return None, "Загруженный TXT пустой."
    return stored_path, None


def require_user(request: Request) -> str:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401)
    return str(user)


def current_user(request: Request) -> str | None:
    user = request.session.get("user")
    return str(user) if user else None


def flash(request: Request, message: str, level: str = "info") -> None:
    request.session["flash"] = {"message": message, "level": level}


def pop_flash(request: Request) -> dict[str, str] | None:
    return request.session.pop("flash", None)


def render(request: Request, template_name: str, context: dict[str, Any], status_code: int = 200) -> HTMLResponse:
    payload = {
        "request": request,
        "app_title": settings.app_title,
        "current_user": current_user(request),
        "flash": pop_flash(request),
    }
    payload.update(context)
    return templates.TemplateResponse(request, template_name, payload, status_code=status_code)


def check_password(username: str, password: str) -> bool:
    stored = settings.app_users.get(username)
    return bool(stored and stored == password)


def is_authenticated(request: Request) -> bool:
    return current_user(request) is not None


def parse_log_summary(log_path: Path) -> dict[str, Any]:
    if not log_path.exists():
        return {}

    text = log_path.read_text(encoding="utf-8", errors="replace")
    summary: dict[str, Any] = {}

    progress_matches = re.findall(
        r"\[\s*(\d+)\s*/\s*(\d+)\]\s+([\d.]+)%\s+ok=(\d+)\s+err=(\d+)\s+ETA=([0-9:?]+)\s+xpoz=(\d+)\s+cost=\$([0-9.]+)",
        text,
    )
    if progress_matches:
        done, total, pct, ok_count, err_count, eta, xpoz_count, cost = progress_matches[-1]
        summary.update(
            {
                "done": int(done),
                "total": int(total),
                "pct": float(pct),
                "success": int(ok_count),
                "errors": int(err_count),
                "eta": eta,
                "xpoz_results": int(xpoz_count),
                "llm_cost_usd": float(cost),
            }
        )

    done_match = re.search(r"Готово за (\d{2}:\d{2}:\d{2})", text)
    footer_match = re.search(r"Успешно:\s*(\d+)\s*\|\s*Ошибок:\s*(\d+)", text)
    if done_match:
        summary["elapsed"] = done_match.group(1)
        summary["completed"] = True
    if footer_match:
        summary["success"] = int(footer_match.group(1))
        summary["errors"] = int(footer_match.group(2))

    error_lines = [
        line.strip()
        for line in text.splitlines()
        if "❌" in line or "ERROR" in line.upper() or "Ошибка" in line
    ]
    if error_lines:
        summary["last_error"] = error_lines[-1]

    return summary


def tail_log(log_path: Path, max_lines: int = 120) -> str:
    if not log_path.exists():
        return "Log file not found yet."
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])


def script_diagnostics() -> list[dict[str, str]]:
    script_names = ["batch_analyze.py", "analyze_account.py", "export_emails.py", "export_discovery.py", "run.sh"]
    checks = []
    for name in script_names:
        exists = (DEPLOY_DIR / name).exists()
        checks.append(
            {
                "label": name,
                "status": "ok" if exists else "error",
                "detail": "available" if exists else "missing",
            }
        )

    sqlite_ok, sqlite_detail = sqlite_client.healthcheck()
    checks.append(
        {
            "label": "SQLite DB",
            "status": "ok" if sqlite_ok else "error",
            "detail": sqlite_detail,
        }
    )
    checks.append(
        {
            "label": "XPOZ_DB_PATH",
            "status": "ok" if settings.db_path else "error",
            "detail": str(settings.db_path),
        }
    )
    checks.append(
        {
            "label": "CSV upload dir",
            "status": "ok",
            "detail": str(settings.upload_dir),
        }
    )
    fallback_csv = resolve_batch_csv_path()
    checks.append(
        {
            "label": "Batch CSV source",
            "status": "ok" if fallback_csv.exists() else "warning",
            "detail": str(fallback_csv),
        }
    )
    checks.append(
        {
            "label": "APP_USERS_JSON",
            "status": "ok" if settings.app_users else "warning",
            "detail": "configured" if settings.app_users else "missing or invalid",
        }
    )
    checks.append(
        {
            "label": "APP_SECRET",
            "status": "ok" if os.environ.get("APP_SECRET") else "warning",
            "detail": "configured" if os.environ.get("APP_SECRET") else "using ephemeral in-memory secret",
        }
    )
    checks.append(
        {
            "label": "ANTHROPIC_API_KEY",
            "status": "ok" if settings.anthropic_key_present else "warning",
            "detail": "configured" if settings.anthropic_key_present else "missing",
        }
    )
    return checks


def sync_runs() -> None:
    registry.finalize_stale_runs(parse_log_summary)


def start_job(*, job_type: str, params: dict[str, Any], triggered_by: str) -> dict[str, Any]:
    log_path = LOG_DIR / f"{job_type}_{now_slug()}.log"
    source_script = "batch_analyze.py"
    run = registry.create_run(
        job_type=job_type,
        source_script=source_script,
        params=params,
        triggered_by=triggered_by,
        log_path=str(log_path),
    )

    command = [sys.executable, source_script]
    if job_type == "single":
        command.extend(["--username", params["username"]])
    elif params.get("usernames_file"):
        command.extend(["--usernames-file", str(params["usernames_file"])])
        if params.get("limit"):
            command.extend(["--limit", str(params["limit"])])
        if params.get("workers"):
            command.extend(["--workers", str(params["workers"])])
        if params.get("reanalyze"):
            command.append("--reanalyze")
    else:
        if params.get("csv_file"):
            command.extend(["--csv-file", str(params["csv_file"])])
        if params.get("preset"):
            command.extend(["--preset", str(params["preset"])])
        if params.get("limit"):
            command.extend(["--limit", str(params["limit"])])
        if params.get("workers"):
            command.extend(["--workers", str(params["workers"])])
        if params.get("filter"):
            command.extend(["--filter", str(params["filter"])])
        if params.get("reanalyze"):
            command.append("--reanalyze")
        if params.get("retry_errors"):
            command.append("--retry-errors")

    with open(log_path, "a", encoding="utf-8") as log_file:
        log_file.write(f"# Run {run['id']}\n")
        log_file.write(f"# Started at {datetime.now(timezone.utc).isoformat()}\n")
        log_file.write(f"# Command: {' '.join(command)}\n\n")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    if job_type == "batch" and params.get("csv_file") and not params.get("usernames_file"):
        env["XPOZ_ACCOUNTS_CSV"] = str(params["csv_file"])
    log_handle = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        command,
        cwd=str(DEPLOY_DIR),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        start_new_session=True,
    )
    log_handle.close()

    registry.mark_started(run["id"], proc.pid)

    monitor = threading.Thread(
        target=_monitor_run,
        args=(run["id"], proc, log_path),
        name=f"run-monitor-{run['id']}",
        daemon=True,
    )
    monitor.start()
    _monitor_threads[run["id"]] = monitor

    return registry.get_run(run["id"]) or run


def _monitor_run(run_id: int, proc: subprocess.Popen, log_path: Path) -> None:
    exit_code = proc.wait()
    run = registry.get_run(run_id)
    if not run:
        return

    summary = parse_log_summary(log_path)
    if run.get("stop_requested"):
        status = "cancelled"
        error_summary = "Run stopped by operator."
    elif exit_code == 0:
        status = "completed"
        error_summary = None
    else:
        status = "failed"
        error_summary = summary.get("last_error") or f"Process exited with code {exit_code}"

    summary["exit_code"] = exit_code
    registry.mark_finished(run_id, status=status, summary=summary, error_summary=error_summary)


def parse_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@app.get("/", response_class=HTMLResponse)
@app.head("/", response_class=HTMLResponse)
def root(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/runs", status_code=303)


@app.get("/index.html")
@app.head("/index.html")
@app.get("/overview")
@app.head("/overview")
def public_overview(request: Request):
    path = PROJECT_DIR / "index.html"
    if request.method == "HEAD":
        if not path.is_file():
            return Response(status_code=404)
        length = path.stat().st_size
        return Response(
            status_code=200,
            media_type="text/html; charset=utf-8",
            headers={"Content-Length": str(length)},
        )
    return FileResponse(path)


@app.api_route("/login", methods=["GET", "HEAD"], response_class=HTMLResponse)
def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/runs", status_code=303)
    if request.method == "HEAD":
        return _head_html()
    return render(request, "login.html", {})


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    if not check_password(username, password):
        flash(request, "Неверный логин или пароль.", "error")
        return RedirectResponse("/login", status_code=303)
    request.session["user"] = username
    flash(request, "Вход выполнен.", "success")
    return RedirectResponse("/runs", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.api_route("/runs", methods=["GET", "HEAD"], response_class=HTMLResponse)
def runs_page(request: Request, run_id: int | None = None):
    user = require_user(request)
    if request.method == "HEAD":
        return _head_html()
    sync_runs()
    runs = registry.list_runs()
    selected_run = registry.get_run(run_id) if run_id else (runs[0] if runs else None)
    if selected_run:
        selected_run["log_tail"] = tail_log(Path(selected_run["log_path"]))
        selected_run["events"] = registry.get_events(selected_run["id"])
    return render(
        request,
        "runs.html",
        {
            "runs": runs,
            "selected_run": selected_run,
            "diagnostics": script_diagnostics(),
            "active_batch_exists": registry.has_active_batch(),
            "user": user,
        },
    )


@app.post("/runs/start/batch")
async def start_batch(request: Request):
    user = require_user(request)
    form = await request.form()
    if registry.has_active_batch():
        flash(request, "Уже есть активный batch run. Дождись завершения или останови его.", "error")
        return RedirectResponse("/runs", status_code=303)

    uploaded_file = form.get("accounts_file")
    csv_path: Path | None = None
    if getattr(uploaded_file, "filename", "").strip():
        csv_path, error = save_uploaded_csv(uploaded_file, triggered_by=user)
        if error:
            flash(request, error, "error")
            return RedirectResponse("/runs", status_code=303)
    else:
        fallback_csv = resolve_batch_csv_path()
        if fallback_csv.exists():
            csv_path = fallback_csv
        else:
            flash(request, "Загрузи CSV файл для batch run.", "error")
            return RedirectResponse("/runs", status_code=303)

    params = {
        "csv_file": str(csv_path) if csv_path else None,
        "csv_filename": Path(csv_path).name if csv_path else None,
        "preset": str(form.get("preset", "csv-all")).strip() or None,
        "limit": parse_int(str(form.get("limit", "500")), 500),
        "workers": parse_int(str(form.get("workers", "20")), 20),
        "filter": str(form.get("filter", "")).strip(),
        "reanalyze": form.get("reanalyze") == "on",
        "retry_errors": form.get("retry_errors") == "on",
    }
    run = start_job(job_type="batch", params=params, triggered_by=user)
    flash(request, f"Batch run #{run['id']} запущен.", "success")
    return RedirectResponse(f"/runs?run_id={run['id']}", status_code=303)


@app.post("/runs/start/single")
async def start_single(request: Request):
    user = require_user(request)
    form = await request.form()
    username = str(form.get("username", "")).strip()
    if not username:
        flash(request, "Укажи username для single-account анализа.", "error")
        return RedirectResponse("/runs", status_code=303)
    run = start_job(job_type="single", params={"username": username}, triggered_by=user)
    flash(request, f"Single run #{run['id']} для @{username} запущен.", "success")
    return RedirectResponse(f"/runs?run_id={run['id']}", status_code=303)


@app.get("/runs/start/txt-list")
def start_txt_list_get(request: Request):
    """Прямой заход по ссылке в браузере (GET) — без POST тела; ведём на форму."""
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=303)
    flash(
        request,
        "Список из TXT запускается кнопкой «Запустить анализ списка» на странице «Запуски», а не открытием этого URL.",
        "info",
    )
    return RedirectResponse("/runs", status_code=303)


@app.post("/runs/start/txt-list")
async def start_txt_list(request: Request):
    user = require_user(request)
    form = await request.form()
    if registry.has_active_batch():
        flash(request, "Уже есть активный batch run. Дождись завершения или останови его.", "error")
        return RedirectResponse("/runs", status_code=303)

    uploaded = form.get("usernames_file")
    if not getattr(uploaded, "filename", "").strip():
        flash(request, "Выбери TXT файл со списком username.", "error")
        return RedirectResponse("/runs", status_code=303)

    txt_path, error = save_uploaded_txt(uploaded, triggered_by=user)
    if error:
        flash(request, error, "error")
        return RedirectResponse("/runs", status_code=303)

    params = {
        "usernames_file": str(txt_path),
        "txt_filename": txt_path.name,
        "limit": parse_int(str(form.get("limit", "500")), 500),
        "workers": parse_int(str(form.get("workers", "20")), 20),
        "reanalyze": form.get("reanalyze") == "on",
    }
    run = start_job(job_type="batch", params=params, triggered_by=user)
    flash(request, f"Запуск по списку TXT #{run['id']} ({params['txt_filename']}).", "success")
    return RedirectResponse(f"/runs?run_id={run['id']}", status_code=303)


@app.post("/runs/{run_id}/stop")
def stop_run(request: Request, run_id: int):
    require_user(request)
    ok = registry.send_stop_signal(run_id)
    flash(
        request,
        "Сигнал остановки отправлен." if ok else "Не удалось отправить сигнал остановки.",
        "warning" if ok else "error",
    )
    return RedirectResponse(f"/runs?run_id={run_id}", status_code=303)


@app.api_route("/partials/run-list", methods=["GET", "HEAD"], response_class=HTMLResponse)
def partial_run_list(request: Request):
    require_user(request)
    if request.method == "HEAD":
        return _head_html()
    sync_runs()
    return render(request, "partials/run_list.html", {"runs": registry.list_runs()})


@app.api_route("/partials/run-detail/{run_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
def partial_run_detail(request: Request, run_id: int):
    require_user(request)
    if request.method == "HEAD":
        return _head_html()
    sync_runs()
    run = registry.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    run["log_tail"] = tail_log(Path(run["log_path"]))
    run["events"] = registry.get_events(run_id)
    return render(request, "partials/run_detail.html", {"selected_run": run})


@app.api_route("/results", methods=["GET", "HEAD"], response_class=HTMLResponse)
def results_page(
    request: Request,
    page: int = 1,
    search: str = "",
    quick_filter: str = "all",
    sort_by: str = "id",
    sort_dir: str = "desc",
):
    require_user(request)
    if request.method == "HEAD":
        return _head_html()
    rows: list[dict[str, Any]] = []
    total = 0
    error_message = None
    try:
        rows, total = sqlite_client.list_results(
            page=page,
            page_size=100,
            search=search,
            quick_filter=quick_filter,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
    except RuntimeError as exc:
        error_message = str(exc)
    return render(
        request,
        "results.html",
        {
            "rows": rows,
            "total": total,
            "page": page,
            "page_size": 100,
            "search": search,
            "quick_filter": quick_filter,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "error_message": error_message,
            "results_table_columns": RESULT_COLUMNS,
            "results_bool_columns": ANALYSIS_BOOL_COLUMNS,
        },
    )


@app.api_route("/partials/results-table", methods=["GET", "HEAD"], response_class=HTMLResponse)
def partial_results_table(
    request: Request,
    page: int = 1,
    search: str = "",
    quick_filter: str = "all",
    sort_by: str = "id",
    sort_dir: str = "desc",
):
    require_user(request)
    if request.method == "HEAD":
        return _head_html()
    rows: list[dict[str, Any]] = []
    total = 0
    error_message = None
    try:
        rows, total = sqlite_client.list_results(
            page=page,
            page_size=100,
            search=search,
            quick_filter=quick_filter,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
    except RuntimeError as exc:
        error_message = str(exc)
    return render(
        request,
        "partials/results_table.html",
        {
            "rows": rows,
            "total": total,
            "page": page,
            "page_size": 100,
            "search": search,
            "quick_filter": quick_filter,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "error_message": error_message,
            "results_table_columns": RESULT_COLUMNS,
            "results_bool_columns": ANALYSIS_BOOL_COLUMNS,
        },
    )


@app.api_route("/results/{record_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
def result_detail(request: Request, record_id: int):
    require_user(request)
    if request.method == "HEAD":
        return _head_html()
    try:
        row = sqlite_client.fetch_result(record_id)
    except RuntimeError as exc:
        return HTMLResponse(f"<div class='error-box'>{exc}</div>", status_code=502)
    if not row:
        raise HTTPException(status_code=404, detail="Result not found")
    return render(request, "partials/result_detail.html", {"row": row})


@app.get("/export/results")
def export_results(
    request: Request,
    search: str = "",
    quick_filter: str = "all",
    sort_by: str = "id",
    sort_dir: str = "desc",
):
    require_user(request)
    try:
        csv_text, total = sqlite_client.export_results_csv(
            search=search,
            quick_filter=quick_filter,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    filename = f"analysis_results_{now_slug()}_{total}.csv"
    return StreamingResponse(
        iter([csv_text.encode("utf-8")]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/healthz")
@app.head("/healthz")
def healthz(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200)
    sync_runs()
    sqlite_ok, detail = sqlite_client.healthcheck()
    return {
        "ok": sqlite_ok,
        "detail": detail,
        "active_runs": len(registry.list_active_runs()),
    }


@app.get("/favicon.ico", include_in_schema=False)
@app.head("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)
