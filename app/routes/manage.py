# -*- coding: utf-8 -*-
"""管理员后台。"""
from __future__ import annotations

import json

from flask import (Blueprint, flash, g, jsonify, redirect, render_template,
                   request, url_for)

from .. import config, mailer, quicker, security
from ..db import get_db
from ..util import (api_categories, csrf_valid, fmt_ts, json_err, json_ok,
                    normalize_slug, now_ts, setting_get, setting_set,
                    slug_problem, text, to_int)

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

        elif section == "ratelimit":
            per_user = to_int(request.form.get("qps_per_user"), 0, 0, 10000)
            per_api = to_int(request.form.get("qps_per_api"), 0, 0, 10000)
            setting_set("qps_per_user", per_user)
            setting_set("qps_per_api", per_api)
            flash("限流设置已保存", "success")
            return redirect(url_for("manage.settings"))

        elif section == "categories":
            # 分类每行一个，去空去重，最多 40 个
            raw = (request.form.get("categories") or "").replace("\r\n", "\n")
            names, seen = [], set()
            for line in raw.split("\n"):
                name = text(line, 32)
                if name and name not in seen:
                    seen.add(name)
                    names.append(name)
            if len(names) > 40:
                errors.append("分类最多 40 个")
            else:
                setting_set("api_categories", names)
                flash("接口分类已保存", "success")
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
        "categories": api_categories(),
        "ratelimit": {
            "per_user": setting_get("qps_per_user", 0) or 0,
            "per_api": setting_get("qps_per_api", 0) or 0,
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
    keyword = text(request.args.get("q"), 100)
    status_filter = text(request.args.get("status"), 16)

    clauses, args = [], []
    if keyword:
        clauses.append("(name LIKE %s OR slug LIKE %s)")
        args += ["%%%s%%" % keyword] * 2
    if status_filter == "online":
        clauses.append("status = %s")
        args.append(quicker.STATUS_ONLINE)
    elif status_filter == "maintenance":
        clauses.append("status = %s")
        args.append(quicker.STATUS_MAINTENANCE)
    elif status_filter == "offline":
        clauses.append("status = %s")
        args.append(quicker.STATUS_OFFLINE)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = db.query(
        "SELECT id, slug, name, action, wait_flag, max_wait_ms, txt_flag, cost_points, "
        "fields_json, call_count, success_count, status, maintenance_note, category, updated_at "
        "FROM apis" + where + " ORDER BY id DESC",
        tuple(args),
    )
    for row in rows:
        try:
            row["fields"] = json.loads(row["fields_json"] or "[]")
        except ValueError:
            row["fields"] = []
        row["param_count"] = len(row["fields"])

    counts = {
        "all": db.scalar("SELECT COUNT(*) AS c FROM apis"),
        "online": db.scalar("SELECT COUNT(*) AS c FROM apis WHERE status = %s",
                            (quicker.STATUS_ONLINE,)),
        "maintenance": db.scalar("SELECT COUNT(*) AS c FROM apis WHERE status = %s",
                                 (quicker.STATUS_MAINTENANCE,)),
        "offline": db.scalar("SELECT COUNT(*) AS c FROM apis WHERE status = %s",
                             (quicker.STATUS_OFFLINE,)),
    }
    return render_template("manage/apis.html", apis=rows, keyword=keyword,
                           status_filter=status_filter, counts=counts)


def _api_form_context(api: dict | None, errors: list[str], form_fields: list[dict]):
    return dict(
        api=api, errors=errors, form_fields=form_fields,
        shown_fields=form_fields or [],
        max_wait=quicker.MAX_WAIT_LIMIT,
        status_choices=quicker.STATUS_CHOICES,
        categories=api_categories(),
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
        "status": quicker.normalize_status(form.get("status", quicker.STATUS_ONLINE)),
        "maintenance_note": text(form.get("maintenance_note"), 200),
        "category": text(form.get("category"), 32),
    }
    if not data["name"]:
        errors.append("API 名称必填")
    # 调用标识必须非空且唯一；格式问题统一由 slug_problem 判定，
    # 避免校验规则散落在多处、改一处漏一处。
    problem = slug_problem(data["slug"])
    if problem:
        errors.append(problem)

    if data["wait_flag"] and data["max_wait_ms"] <= 0:
        errors.append("开启等待结果时，maxWaitMs 必须大于 0")

    # data 键值定义：来自动态表单的并行数组
    raw_fields = []
    keys = form.getlist("field_key")
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
        "cost_points": 0, "status": 1, "category": "",
    }

    if request.method == "POST":
        api, fields, errors = _read_api_form(request.form)
        form_fields = fields
        if db.query_one("SELECT id FROM apis WHERE slug = %s", (api["slug"],)):
            errors.append("调用标识「%s」已被占用" % api["slug"])
        if not errors:
            api_id = db.insert(
                "INSERT INTO apis (slug, name, description, action, wait_flag, max_wait_ms, "
                "txt_flag, cost_points, fields_json, status, maintenance_note, category, "
                "created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (api["slug"], api["name"], api["description"], api["action"], api["wait_flag"],
                 api["max_wait_ms"], api["txt_flag"], api["cost_points"],
                 json.dumps(fields, ensure_ascii=False), api["status"],
                 api["maintenance_note"], api["category"],
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
                "maintenance_note=%s, category=%s, updated_at=%s WHERE id=%s",
                (draft["slug"], draft["name"], draft["description"], draft["action"],
                 draft["wait_flag"], draft["max_wait_ms"], draft["txt_flag"], draft["cost_points"],
                 json.dumps(fields, ensure_ascii=False), draft["status"],
                 draft["maintenance_note"], draft["category"],
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


# ═════════════════════ API 批量操作 ═════════════════════
def _batch_ids() -> list[int]:
    ids = [to_int(v, 0, 1) for v in request.form.getlist("ids")]
    return [i for i in ids if i > 0]


@bp.post("/apis/batch")
@guard
def api_batch():
    """批量操作：设为在线 / 设为维护 / 下线 / 删除。

    用 GET 拿不到，必须是 POST + CSRF；删除会让调用地址立即失效，需二次确认。
    """
    db = get_db()
    ids = _batch_ids()
    action = text(request.form.get("action"), 24)
    back = request.referrer or url_for("manage.api_list")

    if not ids:
        flash("没有选中任何接口", "error")
        return redirect(back)

    placeholders = ",".join(["%s"] * len(ids))
    note = text(request.form.get("maintenance_note"), 200)

    if action == "online":
        affected = db.execute(
            "UPDATE apis SET status = %s, maintenance_note = '', updated_at = %s "
            "WHERE id IN (%s)" % ("%s", "%s", placeholders),
            tuple([quicker.STATUS_ONLINE, now_ts()] + ids))
        flash("已把 %d 个接口设为「在线」" % int(affected or 0), "success")

    elif action == "maintenance":
        affected = db.execute(
            "UPDATE apis SET status = %s, maintenance_note = %s, updated_at = %s "
            "WHERE id IN (%s)" % ("%s", "%s", "%s", placeholders),
            tuple([quicker.STATUS_MAINTENANCE, note, now_ts()] + ids))
        flash("已把 %d 个接口设为「维护中」" % int(affected or 0), "success")

    elif action == "offline":
        affected = db.execute(
            "UPDATE apis SET status = %s, maintenance_note = '', updated_at = %s "
            "WHERE id IN (%s)" % ("%s", "%s", placeholders),
            tuple([quicker.STATUS_OFFLINE, now_ts()] + ids))
        flash("已把 %d 个接口设为「下线」" % int(affected or 0), "success")

    elif action == "delete":
        affected = db.execute(
            "DELETE FROM apis WHERE id IN (%s)" % placeholders, tuple(ids))
        flash("已删除 %d 个接口（历史调用日志保留）" % int(affected or 0), "success")

    else:
        flash("未知的批量操作", "error")

    return redirect(back)


@bp.post("/apis/batch-by-filter")
@guard
def api_batch_by_filter():
    """按当前筛选条件批量操作（只影响筛选结果）。"""
    db = get_db()
    action = text(request.form.get("action"), 24)
    keyword = text(request.form.get("q"), 100)
    status_filter = text(request.form.get("status"), 16)
    back = url_for("manage.api_list", q=keyword, status=status_filter)

    clauses, args = [], []
    if keyword:
        clauses.append("(name LIKE %s OR slug LIKE %s)")
        args += ["%%%s%%" % keyword] * 2
    if status_filter == "online":
        clauses.append("status = %s")
        args.append(quicker.STATUS_ONLINE)
    elif status_filter == "maintenance":
        clauses.append("status = %s")
        args.append(quicker.STATUS_MAINTENANCE)
    elif status_filter == "offline":
        clauses.append("status = %s")
        args.append(quicker.STATUS_OFFLINE)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    if not clauses:
        flash("请先设置筛选条件，避免误操作全部接口", "error")
        return redirect(url_for("manage.api_list"))

    # 注意：这里不能用 "..." + where % (...) 的写法——% 优先级高于 +，
    # 会先对 where 做格式化而报错。统一改成先拼好 SQL 再传参。
    if action == "online":
        sql = "UPDATE apis SET status = %s, maintenance_note = '', updated_at = %s" + where
        affected = db.execute(sql, tuple([quicker.STATUS_ONLINE, now_ts()] + args))
        flash("已把符合条件的 %d 个接口设为「在线」" % int(affected or 0), "success")

    elif action == "maintenance":
        sql = ("UPDATE apis SET status = %s, maintenance_note = %s, updated_at = %s"
               + where)
        affected = db.execute(
            sql,
            tuple([quicker.STATUS_MAINTENANCE,
                   text(request.form.get("maintenance_note"), 200), now_ts()] + args))
        flash("已把符合条件的 %d 个接口设为「维护中」" % int(affected or 0), "success")

    elif action == "offline":
        sql = "UPDATE apis SET status = %s, maintenance_note = '', updated_at = %s" + where
        affected = db.execute(sql, tuple([quicker.STATUS_OFFLINE, now_ts()] + args))
        flash("已把符合条件的 %d 个接口设为「下线」" % int(affected or 0), "success")

    else:
        flash("未知的批量操作", "error")

    return redirect(back)


@bp.post("/apis/<int:api_id>/toggle")
@guard
def api_toggle(api_id: int):
    """快速切换：在线 ↔ 下线。维护态请用下面的 set-status 精确指定。"""
    db = get_db()
    row = db.query_one("SELECT status FROM apis WHERE id = %s", (api_id,))
    if row is None:
        flash("接口不存在", "error")
        return redirect(url_for("manage.api_list"))

    current = quicker.normalize_status(row["status"])
    target = quicker.STATUS_OFFLINE if current == quicker.STATUS_ONLINE else quicker.STATUS_ONLINE
    db.execute("UPDATE apis SET status = %s, updated_at = %s WHERE id = %s",
               (target, now_ts(), api_id))
    flash("接口状态已改为「%s」" % quicker.status_label(target), "success")
    return redirect(request.referrer or url_for("manage.api_list"))


@bp.post("/apis/<int:api_id>/status")
@guard
def api_set_status(api_id: int):
    """精确设置状态（在线 / 维护中 / 下线）。"""
    db = get_db()
    target = quicker.normalize_status(request.form.get("status"))
    note = text(request.form.get("maintenance_note"), 200)

    row = db.query_one("SELECT name FROM apis WHERE id = %s", (api_id,))
    if row is None:
        flash("接口不存在", "error")
        return redirect(url_for("manage.api_list"))

    db.execute(
        "UPDATE apis SET status = %s, maintenance_note = %s, updated_at = %s WHERE id = %s",
        (target, note if target == quicker.STATUS_MAINTENANCE else "", now_ts(), api_id),
    )
    flash("「%s」已切换为「%s」" % (row["name"], quicker.status_label(target)), "success")
    return redirect(request.referrer or url_for("manage.api_list"))


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


@bp.post("/logs/<int:log_id>/set-example")
@guard
def log_set_example(log_id: int):
    """把这次调用的返回存成该接口的「返回示例」，展示给平台用户。

    返回示例由管理员从真实调用里挑，比手写更贴近实际，也不容易过期。
    """
    if not csrf_valid():
        flash("会话已过期，请重试", "error")
        return redirect(url_for("manage.log_detail", log_id=log_id))

    db = get_db()
    row = db.query_one(
        "SELECT id, api_id, api_name, response_text, is_success FROM api_logs WHERE id = %s",
        (log_id,))
    if row is None:
        flash("日志不存在", "error")
        return redirect(url_for("manage.log_list"))
    if not int(row.get("is_success") or 0):
        flash("只有成功的调用才能设为返回示例", "error")
        return redirect(url_for("manage.log_detail", log_id=log_id))

    text_body = (row.get("response_text") or "").strip()
    if not text_body:
        flash("这次调用没有返回内容，无法作为示例", "error")
        return redirect(url_for("manage.log_detail", log_id=log_id))

    api_exists = db.query_one("SELECT id FROM apis WHERE id = %s", (row["api_id"],))
    if api_exists is None:
        flash("该接口已被删除，无法设置示例", "error")
        return redirect(url_for("manage.log_detail", log_id=log_id))

    db.execute("UPDATE apis SET response_example = %s, updated_at = %s WHERE id = %s",
               (text_body[:60000], now_ts(), row["api_id"]))
    flash("已把这次返回设为「%s」的返回示例" % row["api_name"], "success")
    return redirect(url_for("manage.log_detail", log_id=log_id))


@bp.post("/apis/<int:api_id>/clear-example")
@guard
def api_clear_example(api_id: int):
    """清空某个接口的返回示例。"""
    if not csrf_valid():
        flash("会话已过期，请重试", "error")
        return redirect(url_for("manage.api_edit", api_id=api_id))
    db = get_db()
    row = db.query_one("SELECT name FROM apis WHERE id = %s", (api_id,))
    if row is None:
        flash("接口不存在", "error")
        return redirect(url_for("manage.api_list"))
    db.execute("UPDATE apis SET response_example = NULL, updated_at = %s WHERE id = %s",
               (now_ts(), api_id))
    flash("已清空「%s」的返回示例" % row["name"], "success")
    return redirect(url_for("manage.api_edit", api_id=api_id))


# ═════════════════════ 卡密 ═════════════════════
@bp.get("/cards")
@guard
def card_list():
    db = get_db()
    page = max(1, to_int(request.args.get("page"), 1, 1))
    per_page = 30
    batch = text(request.args.get("batch"), 64)
    keyword = text(request.args.get("q"), 64)
    status_filter = text(request.args.get("status"), 16)

    clauses, args = [], []
    if batch:
        clauses.append("batch = %s")
        args.append(batch)
    if keyword:
        clauses.append("code LIKE %s")
        args.append("%%%s%%" % keyword.upper())
    if status_filter == "unused":
        clauses.append("status = 0")
    elif status_filter == "used":
        clauses.append("status = 1")
    elif status_filter == "exported":
        clauses.append("exported = 1")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    total = db.scalar("SELECT COUNT(*) AS c FROM redeem_codes %s" % where, tuple(args))
    rows = db.query(
        "SELECT id, code, points, batch, status, exported, used_by_email, used_at, note, created_at "
        "FROM redeem_codes %s ORDER BY id DESC LIMIT %s OFFSET %s" % (where, per_page, (page - 1) * per_page),
        tuple(args),
    )
    batches = db.query(
        "SELECT batch, COUNT(*) AS total, SUM(status = 1) AS used, "
        "MIN(created_at) AS created_at, SUM(points) AS points "
        "FROM redeem_codes GROUP BY batch ORDER BY MIN(id) DESC LIMIT 40"
    )
    summary = {
        "all": db.scalar("SELECT COUNT(*) AS c FROM redeem_codes"),
        "unused": db.scalar("SELECT COUNT(*) AS c FROM redeem_codes WHERE status = 0"),
        "used": db.scalar("SELECT COUNT(*) AS c FROM redeem_codes WHERE status = 1"),
        "exported": db.scalar("SELECT COUNT(*) AS c FROM redeem_codes WHERE exported = 1"),
        "points_used": db.scalar("SELECT COALESCE(SUM(points),0) AS c FROM redeem_codes WHERE status = 1"),
    }
    pages = max(1, (int(total) + per_page - 1) // per_page)
    return render_template("manage/cards.html", cards=rows, batches=batches, summary=summary,
                           page=page, pages=pages, batch=batch, total=total,
                           keyword=keyword, status_filter=status_filter)


@bp.post("/cards/generate")
@guard
def card_generate():
    import secrets

    db = get_db()
    count = to_int(request.form.get("count"), 10, 1, 500)
    points = to_int(request.form.get("points"), 100, 1, 1000000)
    prefix = text(request.form.get("prefix"), 16) or "TB"
    note = text(request.form.get("note"), 200)
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
            "INSERT INTO redeem_codes (code, points, batch, status, note, created_at) "
            "VALUES (%s,%s,%s,0,%s,%s)",
            (code, points, batch, note, now_ts()),
        )
        made.append(code)

    flash("已生成 %d 张卡密（每张 %d 积分，批次 %s）" % (len(made), points, batch), "success")
    return redirect(url_for("manage.card_list", batch=batch))


@bp.post("/cards/batch-delete")
@guard
def card_batch_delete():
    """批量删除卡密。

    已使用的卡密默认不删（它对应真实的积分发放记录），
    除非显式勾选 include_used。
    """
    db = get_db()
    ids = [to_int(v, 0, 1) for v in request.form.getlist("ids")]
    ids = [i for i in ids if i > 0]
    if not ids:
        flash("没有选中任何卡密", "error")
        return redirect(request.referrer or url_for("manage.card_list"))

    include_used = str(request.form.get("include_used", "")).lower() in ("1", "on", "true")
    placeholders = ",".join(["%s"] * len(ids))
    if include_used:
        deleted = db.execute("DELETE FROM redeem_codes WHERE id IN (%s)" % placeholders, tuple(ids))
    else:
        deleted = db.execute(
            "DELETE FROM redeem_codes WHERE id IN (%s) AND status = 0" % placeholders, tuple(ids))
        skipped = len(ids) - int(deleted or 0)
        if skipped:
            flash("已删除 %d 张未使用卡密，%d 张已使用的被保留" % (int(deleted or 0), skipped), "success")
            return redirect(request.referrer or url_for("manage.card_list"))

    flash("已删除 %d 张卡密" % int(deleted or 0), "success")
    return redirect(request.referrer or url_for("manage.card_list"))


@bp.post("/cards/batch-delete-filtered")
@guard
def card_batch_delete_filtered():
    """按当前筛选条件批量删除（只删未使用，避免误删已发放记录）。"""
    db = get_db()
    batch = text(request.form.get("batch"), 64)
    keyword = text(request.form.get("q"), 64)
    status_filter = text(request.form.get("status"), 16)

    clauses, args = [], []
    if batch:
        clauses.append("batch = %s")
        args.append(batch)
    if keyword:
        clauses.append("code LIKE %s")
        args.append("%%%s%%" % keyword.upper())
    if status_filter == "used":
        flash("当前筛选是「已使用」，出于安全考虑不允许批量删除已使用卡密", "error")
        return redirect(request.referrer or url_for("manage.card_list"))
    clauses.append("status = 0")

    deleted = db.execute(
        "DELETE FROM redeem_codes WHERE " + " AND ".join(clauses), tuple(args))
    flash("按当前筛选条件删除了 %d 张未使用卡密" % int(deleted or 0), "success")
    return redirect(url_for("manage.card_list", batch=batch, q=keyword, status=status_filter))


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
    status_filter = text(request.args.get("status"), 16)
    # 默认只导出未使用的；status=all 可导出全部
    if batch:
        rows = db.query(
            "SELECT id, code, points, status FROM redeem_codes WHERE batch = %s ORDER BY id", (batch,))
    elif status_filter == "all":
        rows = db.query("SELECT id, code, points, status FROM redeem_codes ORDER BY id")
    else:
        rows = db.query(
            "SELECT id, code, points, status FROM redeem_codes WHERE status = 0 ORDER BY id")

    # 导出过的打上标记，方便下次筛「未导出」
    ids = [r["id"] for r in rows]
    if ids:
        placeholders = ",".join(["%s"] * len(ids))
        db.execute("UPDATE redeem_codes SET exported = 1 WHERE id IN (%s)" % placeholders, tuple(ids))

    body = "\n".join(row["code"] for row in rows)
    filename = "cards-%s.txt" % (batch or "unused")
    return Response(body, mimetype="text/plain",
                    headers={"Content-Disposition": "attachment; filename=%s" % filename})

