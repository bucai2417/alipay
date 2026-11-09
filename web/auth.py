"""后台登录保护：会话 + 失败限速。"""

from __future__ import annotations

import threading
import time
from functools import wraps

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from core.settings import verify_admin

bp = Blueprint("auth", __name__)

# ip -> [失败次数, 首次失败时间戳, 锁定截止时间]
_fail_lock = threading.Lock()
_failures: dict[str, list] = {}

MAX_FAILURES = 5
WINDOW_SECONDS = 300
LOCK_SECONDS = 300


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.headers.get("X-Real-IP", "") or (request.remote_addr or "unknown")


def _locked_remaining(ip: str) -> int:
    with _fail_lock:
        rec = _failures.get(ip)
        if not rec:
            return 0
        until = rec[2]
        return max(0, int(until - time.time()))


def _record_failure(ip: str) -> int:
    """记录一次失败，返回剩余可尝试次数（被锁定返回 0）。"""
    now = time.time()
    with _fail_lock:
        rec = _failures.get(ip)
        if not rec or now - rec[1] > WINDOW_SECONDS:
            rec = [0, now, 0]
        rec[0] += 1
        if rec[0] >= MAX_FAILURES:
            rec[2] = now + LOCK_SECONDS
            rec[0] = 0
            rec[1] = now
            left = 0
        else:
            left = MAX_FAILURES - rec[0]
        _failures[ip] = rec
        if len(_failures) > 5000:
            for key in [k for k, v in _failures.items()
                        if v[2] < now and now - v[1] > WINDOW_SECONDS]:
                _failures.pop(key, None)
        return left


def _clear_failures(ip: str) -> None:
    with _fail_lock:
        _failures.pop(ip, None)


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            if request.path.startswith("/api/"):
                return jsonify({"success": False, "message": "未登录"}), 401
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapper


@bp.route("/admin/login", methods=["GET", "POST"])
def login():
    error = ""
    next_url = request.args.get("next") or request.form.get("next") or "/admin/"
    if request.method == "POST":
        ip = _client_ip()
        remaining = _locked_remaining(ip)
        if remaining > 0:
            error = f"尝试次数过多，请 {remaining} 秒后再试"
        elif verify_admin(request.form.get("username", ""), request.form.get("password", "")):
            _clear_failures(ip)
            session["admin"] = request.form.get("username", "")
            session.permanent = True
            return redirect(next_url if next_url.startswith("/") else "/admin/")
        else:
            left = _record_failure(ip)
            if left > 0:
                error = f"账号或密码不正确（还可尝试 {left} 次）"
            else:
                error = "尝试次数过多，账号已临时锁定 5 分钟"
    return render_template("admin_login.html", error=error, next_url=next_url)


@bp.route("/admin/logout")
def logout():
    session.pop("admin", None)
    return redirect(url_for("auth.login"))
