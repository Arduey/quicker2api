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
from .util import compact_number, fmt_ago, fmt_ts, now_ts

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
                db = init_db(db_cfg)
                # 补齐缺失的表（老站点升级、或安装时建表中断都能靠这一步自愈）。
                # schema.sql 全是 IF NOT EXISTS，重复执行无副作用。
                try:
                    from .db import install_schema

                    install_schema(db)
                except Exception as exc:  # noqa: BLE001 建表失败不该直接拦住启动
                    logging.warning("补齐表结构失败: %s", exc)

                # 老站点升级：把旧的 admins 表并入 users.is_admin。
                # 幂等，新装站点只会补一次字段（或什么都不做）。
                try:
                    from .db import migrate_admin_into_users

                    for note in migrate_admin_into_users(db):
                        logging.info("数据迁移：%s", note)
                except Exception as exc:  # noqa: BLE001 迁移失败不该影响启动
                    logging.warning("管理员表迁移失败（新装站点无此表，可忽略）: %s", exc)
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
        from .security import current_admin, current_user, current_viewer, viewer_is_admin
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

        viewer = _safe(current_viewer)

        def _nav_badges():
            """侧边栏右侧的小徽章。

                calls_today  本人近 24h 调用数（所有用户都看）
                maintenance  维护中的接口数（仅管理员）
                users        注册用户总数（仅管理员）
                total_calls  全站累计调用数（仅管理员）

            一律返回数字（包括 0）——之前把 0 过滤掉，结果新站点什么都不显示，
            用户会以为功能没做。0 也要显示，只是样式上弱化。
            """
            empty = {"calls_today": None, "maintenance": None,
                     "users": None, "total_calls": None}
            if viewer is None:
                return empty
            from .db import get_db
            from . import quicker as _quicker

            db = get_db()
            if db is None:
                return empty

            badges = {"calls_today": 0, "maintenance": None,
                      "users": None, "total_calls": None}
            uid = viewer.get("id")
            if uid:
                badges["calls_today"] = int(db.scalar(
                    "SELECT COUNT(*) AS c FROM api_logs WHERE user_id = %s AND created_at >= %s",
                    (uid, now_ts() - 86400)) or 0)

            if viewer.get("is_admin"):
                badges["maintenance"] = int(db.scalar(
                    "SELECT COUNT(*) AS c FROM apis WHERE status = %s",
                    (_quicker.STATUS_MAINTENANCE,)) or 0)
                badges["users"] = int(db.scalar(
                    "SELECT COUNT(*) AS c FROM users") or 0)
                badges["total_calls"] = int(db.scalar(
                    "SELECT COUNT(*) AS c FROM api_logs") or 0)
            return badges

        return {
            "csrf_token": ensure_csrf,
            "site_name": config.site_name(),
            "base_url": config.base_url(),
            "current_admin": _safe(current_admin),
            "current_user": _safe(current_user),
            "viewer": viewer,
            "is_admin": viewer_is_admin(viewer),
            "is_admin_view": viewer_is_admin(viewer),
            "nav_badges": _safe(_nav_badges, {}),
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
    # 大数字缩写：9532 → 9.5k+，125861 → 12w+
    app.add_template_filter(compact_number, "compact")


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

