# -*- coding: utf-8 -*-
"""认证：管理员登录、用户注册（邮箱验证码）/登录/登出。"""
from __future__ import annotations

from flask import (Blueprint, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)

from .. import mailer, security
from ..db import get_db
from ..util import (client_ip, csrf_valid, ensure_csrf, is_valid_email,
                    json_err, json_ok, nickname_problem, nickname_taken,
                    normalize_email, normalize_nickname, now_ts, setting_get,
                    text)

bp = Blueprint("auth", __name__)


# ═════════════════════ 站内跳转校验 ═════════════════════
def safe_next(target: str | None, fallback: str) -> str:
    """只允许跳到本站相对路径，挡住 //evil.com、http://evil.com 这类开放重定向。"""
    if not target:
        return fallback
    value = target.strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return fallback
    if "://" in value or value.startswith("/\\"):
        return fallback
    return value


# ═════════════════════ 登录（唯一入口，管理员就是用户） ═════════════════════
@bp.route("/login", methods=["GET", "POST"])
def login():
    """统一登录。管理员和普通用户查的是同一张 users 表。

    无论是不是管理员，登录后都进用户中心 /u；管理员只是侧栏多几个管理入口，
    点「管理概览」即可进入 /admin。
    """
    viewer = security.current_viewer()
    if viewer is not None:
        return redirect(safe_next(request.args.get("next"), url_for("portal.dashboard")))

    error = None
    email = ""
    if request.method == "POST":
        if not csrf_valid():
            error = "页面已过期，请重试"
        else:
            email = normalize_email(text(request.form.get("email"), 190))
            password = request.form.get("password") or ""
            db = get_db()
            if db is None:
                error = "数据库不可用，请稍后重试"
            else:
                user = db.query_one("SELECT * FROM users WHERE email = %s", (email,))
                if user is None:
                    error = "邮箱或密码错误"
                elif int(user["status"]) != 1:
                    # 先判状态再验密码：否则被禁用账号输错密码只报"密码错误"
                    error = "该账号已被禁用，请联系管理员"
                elif not security.verify_password(user["password_hash"], password):
                    error = "邮箱或密码错误"
                else:
                    db.execute(
                        "UPDATE users SET last_login_at = %s, last_login_ip = %s WHERE id = %s",
                        (now_ts(), client_ip(), user["id"]),
                    )
                    security.login_user(user["id"])
                    return redirect(safe_next(request.args.get("next"),
                                               url_for("portal.dashboard")))

    return render_template("login.html", error=error, email=email)


# 兼容旧地址：/admin/login 仍可用，直接复用统一登录
@bp.route("/admin/login", methods=["GET"])
def admin_login():
    if security.current_admin() is not None:
        return redirect(url_for("manage.dashboard"))
    return redirect(url_for("auth.login", next=request.args.get("next")))


# 兼容旧的视图名（模板/重定向里可能还引用 auth.user_login）
user_login = login


@bp.post("/logout")
def logout():
    security.logout_all()
    return redirect(url_for("auth.login"))


@bp.post("/admin/logout")
def admin_logout():
    security.logout_all()
    return redirect(url_for("auth.login"))


@bp.route("/register", methods=["GET", "POST"])
def register():
    if security.current_user() is not None:
        return redirect(url_for("portal.dashboard"))

    db = get_db()
    gift = int(setting_get("register_points", 30) or 0)

    error = None
    email = ""
    nickname = ""
    if request.method == "POST":
        if not csrf_valid():
            error = "页面已过期，请重试"
        else:
            email = normalize_email(text(request.form.get("email"), 190))
            password = request.form.get("password") or ""
            password2 = request.form.get("password2") or ""
            code = text(request.form.get("code"), 16)
            nickname = normalize_nickname(request.form.get("nickname"))

            if not is_valid_email(email):
                error = "邮箱格式不正确"
            elif db.query_one("SELECT id FROM users WHERE email = %s", (email,)):
                error = "该邮箱已注册，请直接登录"
            elif nickname_problem(nickname):
                error = nickname_problem(nickname)
            elif nickname_taken(db, nickname):
                error = "用户名「%s」已被占用，换一个吧" % nickname
            elif security.password_problem(password):
                error = security.password_problem(password)
            elif password != password2:
                error = "两次输入的密码不一致"
            elif not code:
                error = "请填写邮箱验证码"
            elif not mailer.verify_code(email, code, "register"):
                error = "验证码错误或已过期"
            else:
                user_id = db.insert(
                    "INSERT INTO users (email, password_hash, nickname, points, api_key, "
                    "status, email_verified, created_at) VALUES (%s, %s, %s, %s, %s, 1, 1, %s)",
                    (email, security.hash_password(password),
                     nickname, gift, security.new_api_key(), now_ts()),
                )
                security.login_user(user_id)
                flash("注册成功，已赠送 %d 积分" % gift, "success")
                return redirect(url_for("portal.dashboard"))

    return render_template("register.html", error=error, email=email, gift=gift,
                           nickname=nickname)


# ═════════════════════ 发送验证码 ═════════════════════
@bp.post("/api/send-code")
def api_send_code():
    if not csrf_valid():
        return json_err("页面已过期，请刷新后重试", "csrf", 403)

    email = normalize_email(text(request.form.get("email"), 190))
    purpose = text(request.form.get("purpose"), 24) or "register"
    if purpose not in ("register", "reset"):
        purpose = "register"

    if not is_valid_email(email):
        return json_err("邮箱格式不正确", "bad_email", 400)

    ok, message = mailer.send_code(email, purpose, client_ip())
    if not ok:
        return json_err(message, "send_failed", 400)
    return json_ok(message=message)


# ═════════════════════ 密码重置（同样走邮箱验证码） ═════════════════════
@bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    db = get_db()
    error = None
    done = False
    email = ""
    if request.method == "POST":
        if not csrf_valid():
            error = "页面已过期，请重试"
        else:
            email = normalize_email(text(request.form.get("email"), 190))
            code = text(request.form.get("code"), 16)
            password = request.form.get("password") or ""
            password2 = request.form.get("password2") or ""
            user = db.query_one("SELECT id FROM users WHERE email = %s", (email,)) if db else None
            if user is None:
                error = "该邮箱未注册"
            elif not code:
                error = "请填写邮箱验证码"
            elif not mailer.verify_code(email, code, "reset"):
                error = "验证码错误或已过期"
            elif security.password_problem(password):
                error = security.password_problem(password)
            elif password != password2:
                error = "两次输入的密码不一致"
            else:
                db.execute(
                    "UPDATE users SET password_hash = %s WHERE id = %s",
                    (security.hash_password(password), user["id"]),
                )
                done = True
    return render_template("reset_password.html", error=error, email=email, done=done)


# ═════════════════════ JWT（给脚本/前端用） ═════════════════════
@bp.post("/api/auth/token")
def api_token():
    """邮箱 + 密码换 JWT。POST form 或 JSON 均可。"""
    payload = request.get_json(silent=True) or request.form
    email = normalize_email(text(payload.get("email"), 190))
    password = payload.get("password") or ""
    db = get_db()
    user = db.query_one("SELECT * FROM users WHERE email = %s", (email,)) if db else None

    if user is None or not security.verify_password(user["password_hash"], password):
        return json_err("邮箱或密码错误", "invalid_credentials", 401)
    if int(user["status"]) != 1:
        return json_err("账号已被禁用", "account_disabled", 403)

    token = security.jwt_encode({"typ": "user", "uid": user["id"], "email": user["email"]})
    return json_ok(
        token=token,
        token_type="Bearer",
        expires_in=security.JWT_TTL,
        user={"id": user["id"], "email": user["email"], "points": int(user["points"])},
    )


@bp.get("/api/auth/me")
def api_me():
    user = security.current_user()
    if user:
        return json_ok(user={
            "id": user["id"], "email": user["email"],
            "nickname": user["nickname"], "points": int(user["points"]),
        })

    token_user = security.resolve_token_user(security.bearer_token())
    if token_user:
        return json_ok(user={
            "id": token_user["id"], "email": token_user["email"],
            "nickname": token_user["nickname"], "points": int(token_user["points"]),
        })
    return json_err("未登录", "unauthorized", 401)


@bp.get("/healthz")
def healthz():
    from .. import config

    return jsonify({"ok": True, "installed": config.is_installed()})
