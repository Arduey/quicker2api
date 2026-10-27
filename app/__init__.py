# -*- coding: utf-8 -*-
"""应用工厂。"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import timedelta

from flask import Flask, flash, g, jsonify, redirect, request, session, url_for

from . import config
from .db import init_db
from .util import fmt_ago, fmt_ts, now_ts

PERMANENT_SESSION_LIFETIME = timedelta(days=7)


def _bootstrap_secret() -> str:
    """未安装阶段的站点密钥。

    必须是"同一部署下每个 worker 算出来都一样"的值，否则宝塔 gunicorn 多 worker 时
    session 在 A 进程签、B 进程验不过，安装向导会莫名报 CSRF 失效。
    这里用项目绝对路径做派生（同一目录 = 同一密钥），安装完成后自动改用
    config.json 里的随机 secret_key。
    """
    seed = "toolbox-bootstrap|" + os.path.abspath(config.BASE_DIR)
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def create_app() -> Flask:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    app = Flask(
        __name__,
        template_folder=os.path.join(config.BASE_DIR, "templates"),
        static_folder=os.path.join(config.BASE_DIR, "static"),
    )

    cfg = config.load() or {}
    # 未安装时也必须稳定：多个 gunicorn worker 各自随机生成会导致 session 互不认。
    app.secret_key = cfg.get("secret_key") or _bootstrap_secret()
    app.permanent_session_lifetime = PERMANENT_SESSION_LIFETIME
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_NAME="tb_session",
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        JSON_AS_ASCII=False,
    )
    app.json.ensure_ascii = False

    # 数据库（未安装时为空，页面会跳去安装向导）
    if config.is_installed():
        db_cfg = (cfg or {}).get("db") or {}
        if db_cfg.get("host"):
            try:
                init_db(db_cfg)
            except Exception as exc:  # noqa: BLE001 装好了但库挂了也要能提示
                logging.error("数据库初始化失败: %s", exc)

    _register_blueprints(app)
    _register_hooks(app)
    _register_filters(app)
    _register_error_handlers(app)
    return app


def _register_blueprints(app: Flask) -> None:
    from .routes.api_v1 import bp as api_v1_bp
    from .routes.auth import bp as auth_bp
    from .routes.install import bp as install_bp
    from .routes.manage import bp as manage_bp
    from .routes.portal import bp as portal_bp

    app.register_blueprint(install_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(portal_bp)
    app.register_blueprint(manage_bp)
    app.register_blueprint(api_v1_bp)


def _register_hooks(app: Flask) -> None:
    from .db import get_db

    PUBLIC_PREFIXES = ("/install", "/static", "/favicon.ico", "/healthz")

    @app.before_request
    def _ensure_installed():
        path = request.path
        if any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return None
        if not config.is_installed():
            if path.startswith("/api/"):
                return jsonify({
                    "ok": False,
                    "error": {"code": "not_installed", "message": "站点尚未安装，请访问 /install"},
                }), 503
            return redirect("/install")
        return None

    @app.before_request
    def _session_defaults():
        session.permanent = True
        return None

    @app.teardown_appcontext
    def _close_db(exc):
        db = get_db()
        if db is not None:
            db.close()

    @app.context_processor
    def _inject():
        from .security import current_admin, current_user
        from .util import ensure_csrf, settings_all

        installed = config.is_installed()

        def _safe(fn, default=None):
            """数据库可能临时不可用，模板渲染不应该因此 500。"""
            if not installed:
                return default
            try:
                return fn()
            except Exception:  # noqa: BLE001
                logging.warning("模板上下文注入失败", exc_info=True)
                return default

        return {
            "csrf_token": ensure_csrf,
            "site_name": config.site_name(),
            "base_url": config.base_url(),
            "current_admin": _safe(current_admin),
            "current_user": _safe(current_user),
            "now_ts": now_ts,
            "settings": _safe(settings_all, {}),
        }

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp


def _register_filters(app: Flask) -> None:
    app.add_template_filter(fmt_ts, "ts")
    app.add_template_filter(fmt_ago, "ago")


def _register_error_handlers(app: Flask) -> None:
    from flask import render_template

    def _json_wanted() -> bool:
        if request.path.startswith("/api/"):
            return True
        return "application/json" in request.headers.get("Accept", "")

    @app.errorhandler(404)
    def _not_found(_err):
        if _json_wanted():
            return jsonify({"ok": False, "error": {"code": "not_found", "message": "接口不存在"}}), 404
        return render_template("error.html", code=404, message="页面不存在"), 404

    @app.errorhandler(413)
    def _too_large(_err):
        if _json_wanted():
            return jsonify({"ok": False, "error": {"code": "too_large", "message": "请求体过大"}}), 413
        return render_template("error.html", code=413, message="请求体过大"), 413

    @app.errorhandler(500)
    def _server_error(err):
        logging.exception("未处理异常: %s", err)
        if _json_wanted():
            return jsonify({"ok": False, "error": {"code": "server_error", "message": "服务器内部错误"}}), 500
        return render_template("error.html", code=500, message="服务器内部错误"), 500

    @app.errorhandler(405)
    def _method_not_allowed(_err):
        if _json_wanted():
            return jsonify({"ok": False, "error": {"code": "bad_method", "message": "请求方法不允许"}}), 405
        return render_template("error.html", code=405, message="请求方法不允许"), 405


# ═════════════════════ WSGI 兼容入口 ═════════════════════
# 项目里同时有 app.py 和 app/ 包，`gunicorn app:app` 里的 import app 会优先
# 命中这个包。为了让「应用名称填 app:app」也能直接跑起来，这里把实例挂成
# 包属性。wsgi:app 依然可用，两者等价。
app = create_app()

