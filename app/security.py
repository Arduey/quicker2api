# -*- coding: utf-8 -*-
"""密码哈希、JWT 签发/校验、登录态装饰器。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from functools import wraps

from flask import g, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import config
from .db import get_db

JWT_TTL = 7 * 24 * 3600  # 7 天


# ───────────────────────── 密码 ─────────────────────────
def hash_password(password: str) -> str:
    return generate_password_hash(password, method="pbkdf2:sha256:260000")


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return check_password_hash(password_hash, password)
    except (ValueError, TypeError):
        return False


def password_problem(password: str) -> str | None:
    """返回错误提示，None 表示合规。"""
    if password is None or len(password) < 8:
        return "密码至少 8 位"
    if len(password) > 128:
        return "密码不能超过 128 位"
    if password.isdigit():
        return "密码不能是纯数字"
    return None


# ───────────────────────── JWT（HS256，标准库实现） ─────────────────────────
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _secret() -> bytes:
    cfg = config.load() or {}
    return (cfg.get("jwt_secret") or "change-me").encode("utf-8")


def jwt_encode(payload: dict, ttl: int = JWT_TTL) -> str:
    body = dict(payload)
    issued = int(time.time())
    body.setdefault("iat", issued)
    body.setdefault("exp", issued + int(ttl))
    header = {"alg": "HS256", "typ": "JWT"}
    segments = [
        _b64e(json.dumps(header, separators=(",", ":")).encode()),
        _b64e(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()),
    ]
    signing_input = ".".join(segments).encode("ascii")
    signature = hmac.new(_secret(), signing_input, hashlib.sha256).digest()
    segments.append(_b64e(signature))
    return ".".join(segments)


def jwt_decode(token: str) -> dict | None:
    try:
        parts = (token or "").strip().split(".")
        if len(parts) != 3:
            return None
        signing_input = (parts[0] + "." + parts[1]).encode("ascii")
        expect = hmac.new(_secret(), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expect, _b64d(parts[2])):
            return None
        payload = json.loads(_b64d(parts[1]))
        if int(payload.get("exp") or 0) < int(time.time()):
            return None
        return payload
    except Exception:  # noqa: BLE001 任何解析失败都当作无效 token
        return None


# ───────────────────────── 当前登录者 ─────────────────────────
# 设计：管理员就是 users 表里 is_admin = 1 的普通用户。
# 只有一套 session（user_id）、一套账号，管理员额外拥有管理入口。
def current_user():
    if "user" in g:
        return g.user
    user_id = session.get("user_id")
    g.user = None
    if user_id:
        db = get_db()
        if db is not None:
            g.user = db.query_one(
                "SELECT id, email, nickname, points, api_key, status, is_admin, "
                "created_at, last_login_at, last_login_ip "
                "FROM users WHERE id = %s AND status = 1",
                (user_id,),
            )
            if g.user is None:
                session.pop("user_id", None)
    return g.user


def current_admin():
    """管理员视角：就是 is_admin=1 的当前用户。

    保留这个函数是为了让管理后台的装饰器读起来直观，
    本质上与 current_user() 是同一个账号。
    """
    user = current_user()
    if user is not None and int(user.get("is_admin") or 0) == 1:
        return user
    return None


def current_viewer():
    """当前登录者（用户与管理员统一）。

    管理员和普通用户共用同一套页面，只是 is_admin=1 时会多出管理菜单。
    """
    if "viewer" in g:
        return g.viewer

    user = current_user()
    if user is not None:
        g.viewer = {
            "kind": "admin" if int(user.get("is_admin") or 0) == 1 else "user",
            "id": user["id"],
            "email": user["email"],
            "nickname": user.get("nickname") or "",
            "points": int(user.get("points") or 0),
            "api_key": user.get("api_key") or "",
            "is_admin": int(user.get("is_admin") or 0) == 1,
            "created_at": user.get("created_at") or 0,
        }
    else:
        g.viewer = None
    return g.viewer


def viewer_is_admin(viewer) -> bool:
    return bool(viewer) and bool(viewer.get("is_admin"))


def login_user(user_id: int) -> None:
    session.clear()
    session["user_id"] = int(user_id)
    session.permanent = True
    g.pop("user", None)
    g.pop("viewer", None)


# 兼容旧的调用名
    g.pop("viewer", None)


def logout_all() -> None:
    session.clear()
    g.pop("user", None)
    g.pop("viewer", None)


# 兼容旧的调用名
login_admin = login_user


# ───────────────────────── 装饰器 ─────────────────────────
def _wants_json() -> bool:
    if request.method not in ("GET", "HEAD"):
        return True
    accept = request.headers.get("Accept", "")
    if "application/json" in accept:
        return True
    return request.path.startswith("/api/") or request.path.startswith("/u/api/")


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_admin() is None:
            if _wants_json():
                return jsonify({"ok": False, "error": {"code": "forbidden", "message": "需要管理员权限"}}), 403
            flash_msg = "需要管理员权限" if current_user() else "请先登录"
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapper


def user_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            if _wants_json():
                return jsonify({"ok": False, "error": {"code": "unauthorized", "message": "请先登录"}}), 401
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapper


def viewer_required(view):
    """用户中心用：普通用户或管理员都可以访问（管理员是管理视角）。"""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_viewer() is None:
            if _wants_json():
                return jsonify({"ok": False, "error": {"code": "unauthorized", "message": "请先登录"}}), 401
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapper


# ───────────────────────── API Key ─────────────────────────
def new_api_key() -> str:
    return "tb_" + secrets.token_hex(24)


def resolve_api_key(raw: str):
    """把请求头/表单里的 key 换成用户记录。"""
    key = (raw or "").strip()
    if not key.startswith("tb_") or len(key) < 20:
        return None
    db = get_db()
    if db is None:
        return None
    return db.query_one(
        "SELECT id, email, nickname, points, api_key, status FROM users WHERE api_key = %s",
        (key[:64],),
    )


def bearer_token() -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def resolve_token_user(token: str):
    payload = jwt_decode(token)
    if not payload or payload.get("typ") != "user":
        return None
    db = get_db()
    if db is None:
        return None
    return db.query_one(
        "SELECT id, email, nickname, points, api_key, status FROM users WHERE id = %s AND status = 1",
        (int(payload.get("uid") or 0),),
    )
