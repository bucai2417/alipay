"""通用请求辅助。"""

from __future__ import annotations

from flask import jsonify, request


def ok(data=None, **extra):
    payload = {"success": True}
    if isinstance(data, dict):
        payload.update(data)
    payload.update(extra)
    return jsonify(payload)


def fail(message, status=400, **extra):
    payload = {"success": False, "message": str(message)}
    payload.update(extra)
    return jsonify(payload), status


def client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.headers.get("X-Real-IP", "") or (request.remote_addr or "unknown")


def int_arg(name, default, low=None, high=None) -> int:
    try:
        val = int(request.args.get(name, default))
    except (TypeError, ValueError):
        val = default
    if low is not None and val < low:
        val = low
    if high is not None and val > high:
        val = high
    return val


def json_body() -> dict:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return {}
