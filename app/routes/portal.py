# -*- coding: utf-8 -*-
"""用户中心。"""
from __future__ import annotations

import json

from flask import (Blueprint, flash, jsonify, redirect, render_template,
                   request, url_for)

from .. import quicker, security
from ..db import get_db
from ..util import (client_ip, csrf_valid, json_err, json_ok, now_ts, text,
                    to_int)

bp = Blueprint("portal", __name__)


def guard(view):
    from functools import wraps

    @wraps(view)
    @security.user_required
    def wrapper(*a, **kw):
        if request.method in ("POST", "PUT", "DELETE") and not csrf_valid():
            if request.path.startswith("/u/api/"):
                return json_err("页面已过期，请刷新后重试", "csrf", 403)
            flash("页面已过期，请刷新后重试", "error")
            return redirect(url_for("portal.dashboard"))
        return view(*a, **kw)

    return wrapper


# ═════════════════════ 概览 ═════════════════════
@bp.get("/u")
@bp.get("/u/")
@guard
def dashboard():
    user = security.current_user()
    db = get_db()
    stats = {
        "calls": db.scalar("SELECT COUNT(*) AS c FROM api_logs WHERE user_id = %s", (user["id"],)),
        "calls_today": db.scalar(
            "SELECT COUNT(*) AS c FROM api_logs WHERE user_id = %s AND created_at >= %s",
            (user["id"], now_ts() - 86400),
        ),
        "spent": db.scalar(
            "SELECT COALESCE(SUM(cost_points),0) AS c FROM api_logs WHERE user_id = %s AND refunded = 0",
            (user["id"],),
        ),
        "success_rate": 0,
    }
    total = stats["calls"]
    if total:
        ok_count = db.scalar(
            "SELECT COUNT(*) AS c FROM api_logs WHERE user_id = %s AND is_success = 1", (user["id"],)
        )
        stats["success_rate"] = round(ok_count * 100.0 / total, 1)

    recent = db.query(
        "SELECT id, api_name, api_slug, is_success, cost_points, refunded, time_cost_ms, created_at "
        "FROM api_logs WHERE user_id = %s ORDER BY id DESC LIMIT 8",
        (user["id"],),
    )
    apis = _visible_apis(db)
    return render_template("portal/dashboard.html", user=user, stats=stats,
                           recent=recent, apis=apis)


def _visible_apis(db) -> list[dict]:
    rows = db.query(
        "SELECT id, slug, name, description, cost_points, fields_json, call_count, status "
        "FROM apis WHERE status = 1 ORDER BY sort_order ASC, id ASC"
    )
    for row in rows:
        try:
            row["fields"] = json.loads(row["fields_json"] or "[]")
        except ValueError:
            row["fields"] = []
    return rows


# ═════════════════════ API 列表 & 调试 ═════════════════════
@bp.get("/u/apis")
@guard
def api_list():
    user = security.current_user()
    db = get_db()
    return render_template("portal/apis.html", user=user, apis=_visible_apis(db))


@bp.get("/u/apis/<slug>")
@guard
def api_detail(slug: str):
    user = security.current_user()
    db = get_db()
    row = db.query_one("SELECT * FROM apis WHERE slug = %s AND status = 1", (slug,))
    if row is None:
        flash("接口不存在或已下线", "error")
        return redirect(url_for("portal.api_list"))
    try:
        fields = json.loads(row["fields_json"] or "[]")
    except ValueError:
        fields = []
    base = (request.url_root or "").rstrip("/")
    endpoint = base + "/api/v1/" + row["slug"]
    return render_template("portal/api_detail.html", user=user, api=row, fields=fields,
                           base=base, endpoint=endpoint)


@bp.post("/u/api/playground")
@guard
def api_playground():
    """在线调试：真正走一次调用（会扣积分），方便用户确认参数。"""
    from .api_v1 import execute_call

    user = security.current_user()
    db = get_db()
    slug = text(request.form.get("slug"), 64)
    api = db.query_one("SELECT * FROM apis WHERE slug = %s AND status = 1", (slug,))
    if api is None:
        return json_err("接口不存在或已下线", "not_found", 404)

    try:
        fields = json.loads(api["fields_json"] or "[]")
    except ValueError:
        fields = []

    values = {}
    for field in fields:
        key = field.get("key") or ""
        if key:
            values[key] = request.form.get("f_" + key, "")

    result = execute_call(user, api, fields, values, ip=client_ip())
    return jsonify(result)


# ═════════════════════ 调用记录 ═════════════════════
@bp.get("/u/logs")
@guard
def log_list():
    user = security.current_user()
    db = get_db()
    page = max(1, to_int(request.args.get("page"), 1, 1))
    per_page = 20
    total = db.scalar("SELECT COUNT(*) AS c FROM api_logs WHERE user_id = %s", (user["id"],))
    rows = db.query(
        "SELECT id, api_name, api_slug, is_success, cost_points, refunded, http_status, "
        "time_cost_ms, error_message, created_at FROM api_logs WHERE user_id = %s "
        "ORDER BY id DESC LIMIT %s OFFSET %s",
        (user["id"], per_page, (page - 1) * per_page),
    )
    pages = max(1, (int(total) + per_page - 1) // per_page)
    return render_template("portal/logs.html", user=user, logs=rows,
                           page=page, pages=pages, total=total)


@bp.get("/u/logs/<int:log_id>")
@guard
def log_detail(log_id: int):
    user = security.current_user()
    db = get_db()
    row = db.query_one("SELECT * FROM api_logs WHERE id = %s AND user_id = %s", (log_id, user["id"]))
    if row is None:
        flash("记录不存在", "error")
        return redirect(url_for("portal.log_list"))

    def pretty(raw):
        try:
            return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return raw or ""

    return render_template("portal/log_detail.html", user=user, log=row,
                           params_pretty=pretty(row["params_json"]),
                           response_pretty=pretty(row["response_text"]))


# ═════════════════════ 卡密兑换 ═════════════════════
@bp.get("/u/redeem")
@guard
def redeem():
    user = security.current_user()
    db = get_db()
    history = db.query(
        "SELECT code, points, used_at FROM redeem_codes WHERE used_by = %s ORDER BY used_at DESC LIMIT 30",
        (user["id"],),
    )
    return render_template("portal/redeem.html", user=user, history=history)


@bp.post("/u/redeem")
@guard
def redeem_post():
    user = security.current_user()
    db = get_db()
    code = text(request.form.get("code"), 64).upper().replace(" ", "")
    if not code:
        flash("请输入卡密", "error")
        return redirect(url_for("portal.redeem"))

    row = db.query_one("SELECT * FROM redeem_codes WHERE code = %s", (code,))
    if row is None:
        flash("卡密不存在，请检查是否输入有误", "error")
    elif int(row["status"]) != 0:
        flash("该卡密已被使用", "error")
    elif int(row["expires_at"] or 0) and int(row["expires_at"]) < now_ts():
        flash("该卡密已过期", "error")
    else:
        with db.tx() as conn:
            affected = db.tx_execute(
                conn,
                "UPDATE redeem_codes SET status = 1, used_by = %s, used_by_email = %s, used_at = %s "
                "WHERE id = %s AND status = 0",
                (user["id"], user["email"], now_ts(), row["id"]),
            )
            if affected:
                db.tx_execute(conn, "UPDATE users SET points = points + %s WHERE id = %s",
                              (int(row["points"]), user["id"]))
        if affected:
            flash("兑换成功，获得 %d 积分" % int(row["points"]), "success")
        else:
            flash("该卡密刚刚被使用，请重试", "error")
    return redirect(url_for("portal.redeem"))


# ═════════════════════ 账号设置 ═════════════════════
@bp.get("/u/profile")
@guard
def profile():
    user = security.current_user()
    return render_template("portal/profile.html", user=user)


@bp.post("/u/profile/password")
@guard
def change_password():
    user = security.current_user()
    db = get_db()
    old = request.form.get("old_password") or ""
    new = request.form.get("new_password") or ""
    new2 = request.form.get("new_password2") or ""

    row = db.query_one("SELECT password_hash FROM users WHERE id = %s", (user["id"],))
    if not security.verify_password(row["password_hash"], old):
        flash("原密码错误", "error")
    elif security.password_problem(new):
        flash(security.password_problem(new), "error")
    elif new != new2:
        flash("两次输入的新密码不一致", "error")
    else:
        db.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                   (security.hash_password(new), user["id"]))
        flash("密码已更新", "success")
    return redirect(url_for("portal.profile"))


@bp.post("/u/profile/reset-key")
@guard
def reset_key():
    user = security.current_user()
    db = get_db()
    db.execute("UPDATE users SET api_key = %s WHERE id = %s",
               (security.new_api_key(), user["id"]))
    flash("API Key 已重置，旧 Key 立即失效", "success")
    return redirect(url_for("portal.profile"))
