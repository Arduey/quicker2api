# -*- coding: utf-8 -*-
"""安装向导。

全程浏览器操作：访问 /install → 四步表单 → 一键安装。
安装过程会自己建库、建表、写配置、建管理员，不需要手动创建或修改任何文件，
也不需要用命令行。

四步：
    1. 环境检查
    2. 数据库（MySQL）
    3. 站点信息 + SMTP
    4. 管理员账号 → 执行安装
"""
from __future__ import annotations

import re
import sys

from flask import Blueprint, jsonify, redirect, render_template, request

from .. import config, db, mailer, security
from ..util import is_valid_email, normalize_email, now_ts, text

bp = Blueprint("install", __name__, url_prefix="/install")

MIN_PYTHON = (3, 8)
DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


# ───────────────────────── 环境检查 ─────────────────────────
def env_report() -> dict:
    checks = []

    py_ok = sys.version_info[:2] >= MIN_PYTHON
    checks.append({
        "name": "Python 版本",
        "value": "%d.%d.%d" % sys.version_info[:3],
        "ok": py_ok,
        "hint": "" if py_ok else "需要 Python %d.%d 或更高，请在宝塔「Python 项目管理器」里换一个版本" % MIN_PYTHON,
        "required": True,
    })

    for module, label, required in (
        ("flask", "Flask", True),
        ("pymysql", "PyMySQL", True),
        ("requests", "requests", True),
        ("cryptography", "cryptography", False),
    ):
        try:
            mod = __import__(module)
            version = getattr(mod, "__version__", "已安装")
            checks.append({"name": label, "value": str(version), "ok": True, "hint": "", "required": required})
        except ImportError:
            checks.append({
                "name": label,
                "value": "未安装",
                "ok": not required,
                "hint": "在宝塔「Python 项目 → 依赖」里安装 requirements.txt，或手动添加 %s" % module,
                "required": required,
            })

    writable = config.writable()
    checks.append({
        "name": "目录可写",
        "value": config.BASE_DIR,
        "ok": writable,
        "hint": "" if writable else "项目目录不可写，无法生成 config.json，请在宝塔文件管理器里把目录权限设为 755 且属主为运行用户",
        "required": True,
    })

    installed = config.is_installed()
    checks.append({
        "name": "安装状态",
        "value": "已安装" if installed else "未安装",
        "ok": True,
        "hint": "",
        "required": False,
    })

    hard_fail = [c for c in checks if c["required"] and not c["ok"]]
    return {"checks": checks, "ready": not hard_fail}


@bp.get("")
@bp.get("/")
def wizard():
    report = env_report()
    if config.is_installed() and request.args.get("force") != "1":
        return render_template("install_done.html", report=report)
    return render_template("install.html", report=report)


@bp.get("/api/env")
def api_env():
    return jsonify(env_report())


# ───────────────────────── 测试数据库 ─────────────────────────
@bp.post("/api/test-db")
def api_test_db():
    form = request.form
    cfg = {
        "host": text(form.get("db_host"), 190) or "127.0.0.1",
        "port": text(form.get("db_port"), 8) or "3306",
        "user": text(form.get("db_user"), 64),
        "password": form.get("db_password") or "",
        "database": text(form.get("db_database"), 64),
    }
    if not cfg["user"]:
        return jsonify({"ok": False, "message": "请填写数据库用户名"})
    if not DB_NAME_RE.match(cfg["database"] or ""):
        return jsonify({"ok": False, "message": "数据库名只能包含字母、数字、下划线"})

    try:
        cfg["port"] = int(cfg["port"])
    except ValueError:
        return jsonify({"ok": False, "message": "数据库端口必须是数字"})

    ok, info = db.test_connection(cfg, database=None)
    if not ok:
        return jsonify({"ok": False, "message": "连接 MySQL 失败：%s" % info})

    exists, _ = db.test_connection(cfg, database=cfg["database"])
    if exists:
        return jsonify({
            "ok": True,
            "message": "连接成功（MySQL %s），数据库 %s 已存在，将直接建表" % (info, cfg["database"]),
            "need_create": False,
        })
    return jsonify({
        "ok": True,
        "message": "连接成功（MySQL %s），数据库 %s 不存在，安装时会自动创建" % (info, cfg["database"]),
        "need_create": True,
    })


# ───────────────────────── 测试 SMTP ─────────────────────────
@bp.post("/api/test-smtp")
def api_test_smtp():
    form = request.form
    cfg = {
        "host": text(form.get("smtp_host"), 190),
        "port": text(form.get("smtp_port"), 8) or "465",
        "user": text(form.get("smtp_user"), 190),
        "password": form.get("smtp_password") or "",
        "display_name": text(form.get("smtp_display_name"), 64),
        "timeout": 15,
    }
    try:
        cfg["port"] = int(cfg["port"])
    except ValueError:
        return jsonify({"ok": False, "message": "SMTP 端口必须是数字"})

    if not cfg["host"] or not cfg["user"] or not cfg["password"]:
        return jsonify({"ok": False, "message": "请填写 SMTP 服务器、账号和授权码"})

    to = text(form.get("test_to"), 190) or cfg["user"]
    ok, message = mailer.test_smtp(to, cfg)
    return jsonify({"ok": ok, "message": message})


# ───────────────────────── 执行安装 ─────────────────────────
@bp.post("/api/run")
def api_run():
    if config.is_installed():
        return jsonify({"ok": False, "message": "站点已安装，请勿重复执行。如需重装，请先删除项目目录下的 install.lock"})

    form = request.form
    report = env_report()
    if not report["ready"]:
        failed = [c["name"] for c in report["checks"] if c["required"] and not c["ok"]]
        return jsonify({"ok": False, "message": "环境检查未通过：%s" % "、".join(failed)})

    # ── 1. 数据库参数 ──
    db_cfg = {
        "host": text(form.get("db_host"), 190) or "127.0.0.1",
        "port": text(form.get("db_port"), 8) or "3306",
        "user": text(form.get("db_user"), 64),
        "password": form.get("db_password") or "",
        "database": text(form.get("db_database"), 64),
    }
    if not db_cfg["user"]:
        return jsonify({"ok": False, "message": "请填写数据库用户名"})
    if not DB_NAME_RE.match(db_cfg["database"] or ""):
        return jsonify({"ok": False, "message": "数据库名只能包含字母、数字、下划线"})
    try:
        db_cfg["port"] = int(db_cfg["port"])
    except ValueError:
        return jsonify({"ok": False, "message": "数据库端口必须是数字"})

    # ── 2. 站点 / SMTP / 管理员参数 ──
    site_name = text(form.get("site_name"), 64) or "API 分发平台"
    base_url = text(form.get("base_url"), 190).rstrip("/")
    register_points = text(form.get("register_points"), 8) or "30"
    try:
        register_points = max(0, min(1000000, int(register_points)))
    except ValueError:
        register_points = 30

    smtp_cfg = {
        "host": text(form.get("smtp_host"), 190),
        "port": text(form.get("smtp_port"), 8) or "465",
        "user": text(form.get("smtp_user"), 190),
        "password": form.get("smtp_password") or "",
        "display_name": text(form.get("smtp_display_name"), 64),
    }
    if not (smtp_cfg["host"] and smtp_cfg["user"] and smtp_cfg["password"]):
        return jsonify({"ok": False, "message": "SMTP 未填写完整，注册邮箱验证需要它"})
    try:
        smtp_cfg["port"] = int(smtp_cfg["port"])
    except ValueError:
        return jsonify({"ok": False, "message": "SMTP 端口必须是数字"})

    admin_email = normalize_email(text(form.get("admin_email"), 190))
    admin_password = form.get("admin_password") or ""
    admin_password2 = form.get("admin_password2") or ""
    admin_name = text(form.get("admin_name"), 64) or "管理员"

    if not is_valid_email(admin_email):
        return jsonify({"ok": False, "message": "管理员邮箱格式不正确"})
    problem = security.password_problem(admin_password)
    if problem:
        return jsonify({"ok": False, "message": "管理员密码不合规：%s" % problem})
    if admin_password != admin_password2:
        return jsonify({"ok": False, "message": "两次输入的管理员密码不一致"})

    # ── 3. 建库 ──
    ok, info = db.test_connection(db_cfg, database=None)
    if not ok:
        return jsonify({"ok": False, "message": "连接 MySQL 失败：%s" % info})
    mysql_version = info

    try:
        conn = db.Database({**db_cfg, "database": ""}).connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE DATABASE IF NOT EXISTS `%s` DEFAULT CHARACTER SET utf8mb4 "
                    "COLLATE utf8mb4_unicode_ci" % db_cfg["database"]
                )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 安装向导要把原始报错给用户
        return jsonify({"ok": False, "message": "创建数据库失败：%s" % str(exc)[:300]})

    # ── 4. 建表 ──
    try:
        database = db.init_db(db_cfg)
        table_count = db.install_schema(database)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "message": "建表失败：%s" % str(exc)[:300]})

    # ── 5. 写入设置 ──
    try:
        from ..util import setting_set

        setting_set("smtp_host", smtp_cfg["host"])
        setting_set("smtp_port", smtp_cfg["port"])
        setting_set("smtp_user", smtp_cfg["user"])
        setting_set("smtp_password", smtp_cfg["password"])
        setting_set("smtp_display_name", smtp_cfg["display_name"] or site_name)
        setting_set("smtp_timeout", 15)
        setting_set("site_name", site_name)
        setting_set("register_points", register_points)
        setting_set("installed_at", now_ts())
        setting_set("mysql_version", mysql_version)

        # 管理员
        existing = database.query_one("SELECT id FROM admins WHERE email = %s", (admin_email,))
        if existing:
            database.execute(
                "UPDATE admins SET password_hash = %s, name = %s, status = 1 WHERE id = %s",
                (security.hash_password(admin_password), admin_name, existing["id"]),
            )
        else:
            database.insert(
                "INSERT INTO admins (email, password_hash, name, status, created_at) "
                "VALUES (%s, %s, %s, 1, %s)",
                (admin_email, security.hash_password(admin_password), admin_name, now_ts()),
            )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "message": "写入初始数据失败：%s" % str(exc)[:300]})

    # ── 6. 写 config.json + install.lock ──
    try:
        config.save({
            "site_name": site_name,
            "base_url": base_url,
            "secret_key": security_secret(),
            "jwt_secret": security_secret(),
            "brand_from": "#FF7A1A",
            "brand_to": "#E24B06",
            "db": db_cfg,
            "installed_at": now_ts(),
        })
        config.mark_installed()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "message": "写入配置文件失败：%s" % str(exc)[:300]})

    return jsonify({
        "ok": True,
        "message": "安装完成",
        "tables": table_count,
        "admin_email": admin_email,
        "login_url": "/admin/login",
    })


def security_secret() -> str:
    import secrets

    return secrets.token_hex(32)


@bp.get("/lock-status")
def lock_status():
    return jsonify({"installed": config.is_installed(), "locked": config.is_locked()})
