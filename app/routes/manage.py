# -*- coding: utf-8 -*-
"""管理员后台。"""
from __future__ import annotations

import json

from flask import (Blueprint, flash, g, jsonify, redirect, render_template,
                   request, url_for)

from .. import config, mailer, quicker, security
from ..db import get_db
from ..util import (csrf_valid, fmt_ts, json_err, json_ok, normalize_slug,
                    now_ts, setting_get, setting_set, text, to_int)

bp = Blueprint("manage", __name__, url_prefix="/admin")


def guard(view):
    from functools import wraps

    @wraps(view)
    @security.admin_required
    def wrapper(*a, **kw):
        if request.method in ("POST", "PUT", "DELETE") and not csrf_valid():
            if request.path.startswith("/admin/api/"):
                return json_err("页面已过期，请刷新后重试", "csrf", 403)
            flash("页面已过期，请刷新后重试", "error")
            return redirect(url_for("manage.dashboard"))
        return view(*a, **kw)

    return wrapper


# ═════════════════════ 概览 ═════════════════════
@bp.get("")
@bp.get("/")
@guard
def dashboard():
    db = get_db()
    stats = {
        "users": db.scalar("SELECT COUNT(*) AS c FROM users"),
        "users_today": db.scalar("SELECT COUNT(*) AS c FROM users WHERE created_at >= %s", (now_ts() - 86400,)),
        "apis": db.scalar("SELECT COUNT(*) AS c FROM apis"),
        "apis_active": db.scalar("SELECT COUNT(*) AS c FROM apis WHERE status = 1"),
        "calls": db.scalar("SELECT COUNT(*) AS c FROM api_logs"),
        "calls_today": db.scalar("SELECT COUNT(*) AS c FROM api_logs WHERE created_at >= %s", (now_ts() - 86400,)),
        "points_spent": db.scalar("SELECT COALESCE(SUM(cost_points),0) AS c FROM api_logs WHERE refunded = 0"),
        "points_held": db.scalar("SELECT COALESCE(SUM(points),0) AS c FROM users"),
    }
    recent = db.query(
        "SELECT id, user_email, api_name, is_success, cost_points, time_cost_ms, created_at "
        "FROM api_logs ORDER BY id DESC LIMIT 12"
    )
    top_apis = db.query(
        "SELECT id, name, slug, call_count, success_count, cost_points, status "
        "FROM apis ORDER BY call_count DESC LIMIT 8"
    )
    cfg = quicker.global_settings()
    quicker_ready = bool(cfg["to_user"] and cfg["code"] and cfg["operation"])
    smtp_ready = bool(setting_get("smtp_host") and setting_get("smtp_user"))
    return render_template(
        "manage/dashboard.html", stats=stats, recent=recent, top_apis=top_apis,
        quicker_ready=quicker_ready, smtp_ready=smtp_ready,
    )


# ═════════════════════ 全局设置 ═════════════════════
@bp.route("/settings", methods=["GET", "POST"])
@guard
def settings():
    db = get_db()
    errors: list[str] = []
    if request.method == "POST":
        section = text(request.form.get("section"), 32)

        if section == "quicker":
            errors = quicker.save_global_settings(request.form)
            if not errors:
                flash("Quicker 全局设置已保存", "success")
                return redirect(url_for("manage.settings"))

        elif section == "smtp":
            smtp = {
                "host": text(request.form.get("smtp_host"), 190),
                "port": to_int(request.form.get("smtp_port"), 465, 1, 65535),
                "user": text(request.form.get("smtp_user"), 190),
                "password": request.form.get("smtp_password") or "",
                "display_name": text(request.form.get("smtp_display_name"), 64),
                "timeout": 15,
            }
            if not smtp["host"] or not smtp["user"]:
                errors.append("SMTP 服务器与账号必填")
            else:
                setting_set("smtp_host", smtp["host"])
                setting_set("smtp_port", smtp["port"])
                setting_set("smtp_user", smtp["user"])
                if smtp["password"]:
                    setting_set("smtp_password", smtp["password"])
                setting_set("smtp_display_name", smtp["display_name"])
                flash("SMTP 设置已保存", "success")
                return redirect(url_for("manage.settings"))

        elif section == "site":
            site_name = text(request.form.get("site_name"), 64) or "API 分发平台"
            register_points = to_int(request.form.get("register_points"), 30, 0, 1000000)
            setting_set("site_name", site_name)
            setting_set("register_points", register_points)
            cfg = config.load() or {}
            cfg["site_name"] = site_name
            base_url = text(request.form.get("base_url"), 190).rstrip("/")
            cfg["base_url"] = base_url
            config.save(cfg)
            flash("站点设置已保存", "success")
            return redirect(url_for("manage.settings"))

        else:
            errors.append("未知的设置分组")

    groups = {
        "quicker": quicker.global_settings(),
        "smtp": {
            "host": setting_get("smtp_host", ""),
            "port": setting_get("smtp_port", 465),
            "user": setting_get("smtp_user", ""),
            "display_name": setting_get("smtp_display_name", ""),
            "has_password": bool(setting_get("smtp_password", "")),
        },
        "site": {
            "site_name": setting_get("site_name", "API 分发平台"),
            "register_points": setting_get("register_points", 30),
            "base_url": config.base_url(),
        },
    }
    return render_template(
        "manage/settings.html", groups=groups, errors=errors,
        operations=quicker.OPERATIONS, max_wait=quicker.MAX_WAIT_LIMIT,
    )


@bp.post("/api/test-smtp")
@guard
def api_test_smtp():
    to = text(request.form.get("to"), 190)
    ok, message = mailer.test_smtp(to)
    return jsonify({"ok": ok, "message": message})


@bp.post("/api/quicker-devices")
@guard
def api_quicker_devices():
    ok, data = quicker.fetch_devices()
    if not ok:
        return json_err(str(data), "fetch_failed", 400)
    return json_ok(data=data)


@bp.post("/api/quicker-test")
@guard
def api_quicker_test():
    """用当前全局设置发一条测试推送，帮助确认 toUser/code/operation 是否可用。"""
    cfg = quicker.global_settings()
    missing = [k for k in ("to_user", "code") if not cfg.get(k)]
    if missing:
        return json_err("请先保存全局设置：%s" % "、".join(missing), "missing", 400)

    payload = {
        "toUser": cfg["to_user"],
        "code": cfg["code"],
        "toDevice": cfg["to_device"],
        "operation": "copy",
        "action": "",
        "wait": False,
        "maxWaitMs": 3000,
        "txt": False,
        "data": "Quicker 推送连通性测试 " + fmt_ts(now_ts()),
    }
    result = quicker.push(payload)
    return json_ok(ok_push=result["ok"], result={
        "status": result["status"],
        "time_cost_ms": result["time_cost_ms"],
        "text": result["text"][:2000],
        "error": result["error"],
    })


# ═════════════════════ API 管理 ═════════════════════
@bp.get("/apis")
@guard
def api_list():
    db = get_db()
    rows = db.query(
        "SELECT id, slug, name, action, wait_flag, max_wait_ms, txt_flag, cost_points, "
        "fields_json, call_count, success_count, status, sort_order, updated_at "
        "FROM apis ORDER BY sort_order ASC, id DESC"
    )
    for row in rows:
        try:
            row["fields"] = json.loads(row["fields_json"] or "[]")
        except ValueError:
            row["fields"] = []
        row["param_count"] = len(row["fields"])
    return render_template("manage/apis.html", apis=rows)


def _api_form_context(api: dict | None, errors: list[str], form_fields: list[dict]):
    return dict(
        api=api, errors=errors, form_fields=form_fields,
        shown_fields=form_fields or [],
        max_wait=quicker.MAX_WAIT_LIMIT,
    )


def _read_api_form(form) -> tuple[dict, list[dict], list[str]]:
    errors: list[str] = []
    data = {
        "name": text(form.get("name"), 100),
        "slug": normalize_slug(form.get("slug") or ""),
        "description": text(form.get("description"), 4000),
        "action": text(form.get("action"), 200),
        "wait_flag": 1 if str(form.get("wait_flag", "true")).lower() in ("1", "true", "on") else 0,
        "max_wait_ms": to_int(form.get("max_wait_ms"), 30000, 0, quicker.MAX_WAIT_LIMIT),
        "txt_flag": 1 if str(form.get("txt_flag", "false")).lower() in ("1", "true", "on") else 0,
        "cost_points": to_int(form.get("cost_points"), 0, 0, 1000000),
        "status": 1 if str(form.get("status", "1")).lower() in ("1", "true", "on") else 0,
        "sort_order": to_int(form.get("sort_order"), 0, -99999, 99999),
    }
    if not data["name"]:
        errors.append("API 名称必填")
    if not data["slug"]:
        errors.append("调用标识（slug）必填，只能用小写字母、数字、下划线、短横线")
    if len(data["slug"]) < 2:
        errors.append("调用标识至少 2 个字符")

    if data["wait_flag"] and data["max_wait_ms"] <= 0:
        errors.append("开启等待结果时，maxWaitMs 必须大于 0")

    # data 键值定义：来自动态表单的并行数组
    raw_fields = []
    keys = form.getlist("field_key")
    labels = form.getlist("field_label")
    requireds = form.getlist("field_required")
    defaults = form.getlist("field_default")
    hints = form.getlist("field_hint")

    def at(seq, index: str, default=None):
        try:
            return seq[int(index)]
        except (IndexError, ValueError, TypeError):
            return default

    for index, key in enumerate(keys):
        if not text(key, 64):
            continue
        raw_fields.append({
            "key": key,
            "label": at(labels, index) or key,
            "required": at(requireds, index) in ("1", "on", "true"),
            "default": at(defaults, index) or "",
            "hint": at(hints, index) or "",
        })

    fields, field_errors = quicker.normalize_field_list(raw_fields)
    errors.extend(field_errors)
    return data, fields, errors


@bp.route("/apis/new", methods=["GET", "POST"])
@guard
def api_new():
    db = get_db()
    errors: list[str] = []
    form_fields: list[dict] = []
    api = {
        "name": "", "slug": "", "description": "", "action": "",
        "wait_flag": 1, "max_wait_ms": quicker.MAX_WAIT_LIMIT, "txt_flag": 0,
        "cost_points": 0, "status": 1, "sort_order": 0,
    }

    if request.method == "POST":
        api, fields, errors = _read_api_form(request.form)
        form_fields = fields
        if db.query_one("SELECT id FROM apis WHERE slug = %s", (api["slug"],)):
            errors.append("调用标识「%s」已被占用" % api["slug"])
        if not errors:
            api_id = db.insert(
                "INSERT INTO apis (slug, name, description, action, wait_flag, max_wait_ms, "
                "txt_flag, cost_points, fields_json, status, sort_order, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (api["slug"], api["name"], api["description"], api["action"], api["wait_flag"],
                 api["max_wait_ms"], api["txt_flag"], api["cost_points"],
                 json.dumps(fields, ensure_ascii=False), api["status"], api["sort_order"],
                 now_ts(), now_ts()),
            )
            flash("API「%s」已创建" % api["name"], "success")
            return redirect(url_for("manage.api_edit", api_id=api_id))

    return render_template("manage/api_form.html", **_api_form_context(None, errors, form_fields),
                           draft=api, is_new=True)


@bp.route("/apis/<int:api_id>", methods=["GET", "POST"])
@guard
def api_edit(api_id: int):
    db = get_db()
    row = db.query_one("SELECT * FROM apis WHERE id = %s", (api_id,))
    if row is None:
        flash("API 不存在", "error")
        return redirect(url_for("manage.api_list"))

    try:
        fields = json.loads(row["fields_json"] or "[]")
    except ValueError:
        fields = []

    errors: list[str] = []
    draft = dict(row)

    if request.method == "POST":
        draft, new_fields, errors = _read_api_form(request.form)
        fields = new_fields
        dup = db.query_one("SELECT id FROM apis WHERE slug = %s AND id <> %s", (draft["slug"], api_id))
        if dup:
            errors.append("调用标识「%s」已被占用" % draft["slug"])
        if not errors:
            db.execute(
                "UPDATE apis SET slug=%s, name=%s, description=%s, action=%s, wait_flag=%s, "
                "max_wait_ms=%s, txt_flag=%s, cost_points=%s, fields_json=%s, status=%s, "
                "sort_order=%s, updated_at=%s WHERE id=%s",
                (draft["slug"], draft["name"], draft["description"], draft["action"],
                 draft["wait_flag"], draft["max_wait_ms"], draft["txt_flag"], draft["cost_points"],
                 json.dumps(fields, ensure_ascii=False), draft["status"], draft["sort_order"],
                 now_ts(), api_id),
            )
            flash("API「%s」已保存" % draft["name"], "success")
            return redirect(url_for("manage.api_edit", api_id=api_id))

    return render_template("manage/api_form.html", **_api_form_context(row, errors, fields),
                           draft=draft, is_new=False)


@bp.post("/apis/<int:api_id>/delete")
@guard
def api_delete(api_id: int):
    db = get_db()
    row = db.query_one("SELECT id, name FROM apis WHERE id = %s", (api_id,))
    if row:
        db.execute("DELETE FROM apis WHERE id = %s", (api_id,))
        flash("API「%s」已删除（历史调用日志保留）" % row["name"], "success")
    return redirect(url_for("manage.api_list"))


@bp.post("/apis/<int:api_id>/toggle")
@guard
def api_toggle(api_id: int):
    db = get_db()
    db.execute("UPDATE apis SET status = 1 - status, updated_at = %s WHERE id = %s", (now_ts(), api_id))
    return redirect(url_for("manage.api_list"))


# ═════════════════════ 用户管理 ═════════════════════
@bp.get("/users")
@guard
def user_list():
    db = get_db()
    keyword = text(request.args.get("q"), 190)
    page = max(1, to_int(request.args.get("page"), 1, 1))
    per_page = 30

    where, args = "", []
    if keyword:
        where = "WHERE email LIKE %s OR nickname LIKE %s"
        args = ["%%%s%%" % keyword, "%%%s%%" % keyword]

    total = db.scalar("SELECT COUNT(*) AS c FROM users %s" % where, tuple(args))
    rows = db.query(
        "SELECT id, email, nickname, points, status, created_at, last_login_at "
        "FROM users %s ORDER BY id DESC LIMIT %s OFFSET %s" % (where, per_page, (page - 1) * per_page),
        tuple(args),
    )
    for row in rows:
        row["calls"] = db.scalar("SELECT COUNT(*) AS c FROM api_logs WHERE user_id = %s", (row["id"],))

    pages = max(1, (int(total) + per_page - 1) // per_page)
    return render_template("manage/users.html", users=rows, keyword=keyword,
                           page=page, pages=pages, total=total)


@bp.post("/users/<int:user_id>/points")
@guard
def user_points(user_id: int):
    db = get_db()
    delta = to_int(request.form.get("delta"), 0, -10000000, 10000000)
    reason = text(request.form.get("reason"), 200) or "管理员调整"
    user = db.query_one("SELECT id, email, points FROM users WHERE id = %s", (user_id,))
    if user is None:
        flash("用户不存在", "error")
    elif delta == 0:
        flash("调整数量不能为 0", "error")
    else:
        db.execute("UPDATE users SET points = GREATEST(0, points + %s) WHERE id = %s", (delta, user_id))
        after = db.scalar("SELECT points FROM users WHERE id = %s", (user_id,))
        flash("%s 积分 %+d（%s），当前 %s" % (user["email"], delta, reason, after), "success")
    return redirect(request.referrer or url_for("manage.user_list"))


@bp.post("/users/<int:user_id>/toggle")
@guard
def user_toggle(user_id: int):
    db = get_db()
    db.execute("UPDATE users SET status = 1 - status WHERE id = %s", (user_id,))
    return redirect(request.referrer or url_for("manage.user_list"))


@bp.post("/users/<int:user_id>/reset-key")
@guard
def user_reset_key(user_id: int):
    db = get_db()
    db.execute("UPDATE users SET api_key = %s WHERE id = %s", (security.new_api_key(), user_id))
    flash("已重置该用户的 API Key", "success")
    return redirect(request.referrer or url_for("manage.user_list"))


# ═════════════════════ 调用日志 ═════════════════════
@bp.get("/logs")
@guard
def log_list():
    db = get_db()
    page = max(1, to_int(request.args.get("page"), 1, 1))
    per_page = 30
    keyword = text(request.args.get("q"), 190)
    status = text(request.args.get("status"), 8)

    clauses, args = [], []
    if keyword:
        clauses.append("(user_email LIKE %s OR api_slug LIKE %s OR api_name LIKE %s)")
        args += ["%%%s%%" % keyword] * 3
    if status == "ok":
        clauses.append("is_success = 1")
    elif status == "fail":
        clauses.append("is_success = 0")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    total = db.scalar("SELECT COUNT(*) AS c FROM api_logs %s" % where, tuple(args))
    rows = db.query(
        "SELECT id, user_email, api_name, api_slug, is_success, cost_points, refunded, "
        "http_status, time_cost_ms, error_message, ip, created_at "
        "FROM api_logs %s ORDER BY id DESC LIMIT %s OFFSET %s" % (where, per_page, (page - 1) * per_page),
        tuple(args),
    )
    pages = max(1, (int(total) + per_page - 1) // per_page)
    return render_template("manage/logs.html", logs=rows, page=page, pages=pages,
                           total=total, keyword=keyword, status=status)


@bp.get("/logs/<int:log_id>")
@guard
def log_detail(log_id: int):
    db = get_db()
    row = db.query_one("SELECT * FROM api_logs WHERE id = %s", (log_id,))
    if row is None:
        flash("日志不存在", "error")
        return redirect(url_for("manage.log_list"))

    def pretty(raw):
        try:
            return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return raw or ""

    return render_template("manage/log_detail.html", log=row,
                           params_pretty=pretty(row["params_json"]),
                           response_pretty=pretty(row["response_text"]))


# ═════════════════════ 卡密 ═════════════════════
@bp.get("/cards")
@guard
def card_list():
    db = get_db()
    page = max(1, to_int(request.args.get("page"), 1, 1))
    per_page = 30
    batch = text(request.args.get("batch"), 64)

    where, args = "", []
    if batch:
        where, args = "WHERE batch = %s", [batch]

    total = db.scalar("SELECT COUNT(*) AS c FROM redeem_codes %s" % where, tuple(args))
    rows = db.query(
        "SELECT id, code, points, batch, status, used_by_email, used_at, expires_at, note, created_at "
        "FROM redeem_codes %s ORDER BY id DESC LIMIT %s OFFSET %s" % (where, per_page, (page - 1) * per_page),
        tuple(args),
    )
    batches = db.query(
        "SELECT batch, COUNT(*) AS total, SUM(status = 1) AS used, "
        "MIN(created_at) AS created_at, SUM(points) AS points "
        "FROM redeem_codes GROUP BY batch ORDER BY MIN(id) DESC LIMIT 30"
    )
    summary = {
        "unused": db.scalar("SELECT COUNT(*) AS c FROM redeem_codes WHERE status = 0"),
        "used": db.scalar("SELECT COUNT(*) AS c FROM redeem_codes WHERE status = 1"),
        "points_used": db.scalar("SELECT COALESCE(SUM(points),0) AS c FROM redeem_codes WHERE status = 1"),
    }
    pages = max(1, (int(total) + per_page - 1) // per_page)
    return render_template("manage/cards.html", cards=rows, batches=batches, summary=summary,
                           page=page, pages=pages, batch=batch, total=total)


@bp.post("/cards/generate")
@guard
def card_generate():
    import secrets

    db = get_db()
    count = to_int(request.form.get("count"), 10, 1, 500)
    points = to_int(request.form.get("points"), 100, 1, 1000000)
    prefix = text(request.form.get("prefix"), 16) or "TB"
    note = text(request.form.get("note"), 200)
    days = to_int(request.form.get("expires_days"), 0, 0, 3650)
    expires_at = now_ts() + days * 86400 if days > 0 else 0
    batch = "%s-%s" % (prefix.upper(), now_ts())

    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    made: list[str] = []
    attempts = 0
    while len(made) < count and attempts < count * 20:
        attempts += 1
        token = "".join(secrets.choice(alphabet) for _ in range(16))
        code = "%s-%s-%s-%s" % (prefix.upper(), token[0:4], token[4:8], token[8:12] + token[12:16])
        if db.query_one("SELECT id FROM redeem_codes WHERE code = %s", (code,)):
            continue
        db.insert(
            "INSERT INTO redeem_codes (code, points, batch, status, expires_at, note, created_at) "
            "VALUES (%s,%s,%s,0,%s,%s,%s)",
            (code, points, batch, expires_at, note, now_ts()),
        )
        made.append(code)

    flash("已生成 %d 张卡密（每张 %d 积分，批次 %s）" % (len(made), points, batch), "success")
    return redirect(url_for("manage.card_list", batch=batch))


@bp.post("/cards/<int:card_id>/delete")
@guard
def card_delete(card_id: int):
    db = get_db()
    db.execute("DELETE FROM redeem_codes WHERE id = %s AND status = 0", (card_id,))
    return redirect(request.referrer or url_for("manage.card_list"))


@bp.get("/cards/export")
@guard
def card_export():
    from flask import Response

    db = get_db()
    batch = text(request.args.get("batch"), 64)
    if batch:
        rows = db.query("SELECT code, points FROM redeem_codes WHERE batch = %s ORDER BY id", (batch,))
    else:
        rows = db.query("SELECT code, points FROM redeem_codes WHERE status = 0 ORDER BY id")
    body = "\n".join(row["code"] for row in rows)
    filename = "cards-%s.txt" % (batch or "unused")
    return Response(body, mimetype="text/plain",
                    headers={"Content-Disposition": "attachment; filename=%s" % filename})


# ═════════════════════ 接口试用（管理员免切账号） ═════════════════════
@bp.get("/try")
@guard
def api_try():
    """管理员直接在这里填参数试接口，不用再注册/登录一个普通账号。

    试用走的是与真实调用完全一致的组装与推送逻辑，只是：
      - 不扣积分（管理员没有积分账户）
      - 日志里标记为管理端试用，便于区分
    """
    db = get_db()
    slug = text(request.args.get("slug"), 64)
    rows = db.query(
        "SELECT id, slug, name, description, action, wait_flag, max_wait_ms, txt_flag, "
        "cost_points, fields_json, call_count, success_count, status "
        "FROM apis ORDER BY sort_order ASC, id ASC"
    )
    for row in rows:
        try:
            row["fields"] = json.loads(row["fields_json"] or "[]")
        except ValueError:
            row["fields"] = []
        total = int(row.get("call_count") or 0)
        row["success_rate"] = (
            round(int(row.get("success_count") or 0) * 100.0 / total, 1) if total else None
        )

    current = None
    if slug:
        current = next((r for r in rows if r["slug"] == slug), None)

    cfg = quicker.global_settings()
    quicker_ready = bool(cfg.get("to_user") and cfg.get("code") and cfg.get("operation"))
    return render_template("manage/try.html", apis=rows, current=current,
                           quicker_ready=quicker_ready)


@bp.post("/api/try")
@guard
def api_try_push():
    """真正发一次推送（不扣费、不占用用户积分）。"""
    db = get_db()
    slug = text(request.form.get("slug"), 64)
    api = db.query_one("SELECT * FROM apis WHERE slug = %s", (slug,))
    if api is None:
        return json_err("接口不存在", "not_found", 404)
    if int(api["status"]) != 1:
        return json_err("该接口已下线，无法试用", "offline", 403)

    try:
        fields = json.loads(api["fields_json"] or "[]")
    except ValueError:
        fields = []

    values = {}
    for field in fields:
        key = field.get("key") or ""
        if key:
            values[key] = request.form.get("f_" + key, "")

    errors = quicker.validate_fields(fields, values)
    if errors:
        return json_ok(ok_push=False, error="；".join(errors), cost=0)

    payload = quicker.build_payload(api, fields, values)
    result = quicker.push(payload)

    # 试用也记日志，但标记为管理端，用户侧看不到
    try:
        params_json = json.dumps(quicker.mask_payload(payload), ensure_ascii=False)
    except (TypeError, ValueError):
        params_json = "{}"
    db.insert(
        "INSERT INTO api_logs (user_id, user_email, api_id, api_slug, api_name, params_json, "
        "response_text, is_success, cost_points, refunded, http_status, time_cost_ms, "
        "error_message, ip, created_at) VALUES (0,%s,%s,%s,%s,%s,%s,%s,0,0,%s,%s,%s,%s,%s)",
        ("[管理端试用]", api["id"], api["slug"], api["name"], params_json,
         (result["text"] or "")[:60000], 1 if result["ok"] else 0,
         result["status"], result["time_cost_ms"], (result["error"] or "")[:500],
         "[admin]", now_ts()),
    )
    db.execute(
        "UPDATE apis SET call_count = call_count + 1, success_count = success_count + %s WHERE id = %s",
        (1 if result["ok"] else 0, api["id"]),
    )

    return json_ok(
        ok_push=result["ok"],
        error=result["error"],
        data=result["response"] if result["response"] else result["text"],
        raw=(result["text"] or "")[:20000],
        payload=quicker.mask_payload(payload),
        time_cost_ms=result["time_cost_ms"],
        cost=0,
    )


# ═════════════════════ 我的账号（管理员） ═════════════════════
@bp.route("/profile", methods=["GET", "POST"])
@guard
def profile():
    db = get_db()
    admin = security.current_admin()
    errors: list[str] = []
    if request.method == "POST":
        old = request.form.get("old_password") or ""
        new = request.form.get("new_password") or ""
        new2 = request.form.get("new_password2") or ""
        row = db.query_one("SELECT password_hash FROM admins WHERE id = %s", (admin["id"],))
        if not security.verify_password(row["password_hash"], old):
            errors.append("原密码错误")
        elif security.password_problem(new):
            errors.append(security.password_problem(new))
        elif new != new2:
            errors.append("两次输入的新密码不一致")
        else:
            db.execute("UPDATE admins SET password_hash = %s WHERE id = %s",
                       (security.hash_password(new), admin["id"]))
            flash("密码已更新", "success")
            return redirect(url_for("manage.profile"))

    detail = db.query_one(
        "SELECT id, email, name, status, created_at, last_login_at, last_login_ip "
        "FROM admins WHERE id = %s", (admin["id"],)
    )
    return render_template("manage/profile.html", detail=detail, errors=errors)
