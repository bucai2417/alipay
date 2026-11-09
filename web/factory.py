"""Flask 应用工厂。"""

from __future__ import annotations

import html
import os
import re
import secrets
from datetime import timedelta

from flask import (Flask, Response, jsonify, render_template, request,
                   send_from_directory)

from config import DATA_DIR, SECRET_KEY


def _load_secret_key() -> str:
    if SECRET_KEY:
        return SECRET_KEY
    path = os.path.join(DATA_DIR, ".flask_secret")
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                key = f.read().strip()
            if key:
                return key
    except Exception:
        pass
    key = secrets.token_hex(32)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(key)
    except Exception:
        pass
    return key


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = _load_secret_key()
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 上传图片上限 8MB
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    )

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Permissions-Policy", "geolocation=(), camera=(), microphone=()"
        )
        return resp

    @app.route("/favicon.ico")
    def favicon():
        svg = (
            b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
            b"<rect width='32' height='32' rx='8' fill='#1677ff'/>"
            b"<text x='16' y='23' font-size='18' font-family='sans-serif'"
            b" font-weight='bold' text-anchor='middle' fill='white'>K</text></svg>"
        )
        return Response(svg, mimetype="image/svg+xml")

    @app.route("/uploads/<path:filename>")
    def uploaded_file(filename):
        if os.path.basename(filename) != filename:
            return "", 404
        return send_from_directory(os.path.join(DATA_DIR, "uploads"), filename)

    @app.template_filter("strip_html")
    def strip_html(value):
        text = re.sub(r"<[^>]+>", " ", str(value or ""))
        return re.sub(r"\s+", " ", html.unescape(text)).strip()

    from web import api_admin, api_codepay, api_shop, auth, mcp_server

    app.register_blueprint(auth.bp)
    app.register_blueprint(api_codepay.bp)
    app.register_blueprint(api_shop.bp)
    app.register_blueprint(api_admin.bp)
    app.register_blueprint(mcp_server.mcp_bp)

    # 初始化默认后台账号
    from core.settings import get_admin_auth

    get_admin_auth()

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/"):
            return jsonify({"success": False, "message": "接口不存在"}), 404
        return render_template("error.html", title="页面不存在", message="你访问的页面不存在"), 404

    @app.errorhandler(500)
    def server_error(e):
        if request.path.startswith("/api/"):
            return jsonify({"success": False, "message": "服务器内部错误"}), 500
        return render_template("error.html", title="服务器错误", message="服务器处理请求时出错，请查看日志"), 500

    return app
