# -*- coding: utf-8 -*-
"""对外 API：平台用户调用这里。

    POST /api/v1/<slug>
    鉴权：Authorization: Bearer <API Key 或 JWT>
         或 X-API-Key: <API Key>

    请求体（二选一）：
      - JSON：{"键": "值", ...}
      - 表单：键=值

    返回：
      {"ok": true, "data": {...Quicker 原始返回...}, "cost": 2, "points": 128}
      {"ok": false, "error": {"code": "...", "message": "..."}, "cost": 0, "points": 128}

另外提供（只读）：
    GET  /api/v1/apis          当前用户可用的接口清单
    GET  /api/v1/apis/<slug>   单个接口的参数定义
"""
from __future__ import annotations

import json

from flask import Blueprint, jsonify, request

from .. import quicker, security
from ..db import get_db
from ..util import client_ip, json_err, now_ts, text

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


# ═════════════════════ 鉴权 ═════════════════════
def authenticate():
    """返回 (user, None) 或 (None, 错误响应)。"""
    token = security.bearer_token()
    key = request.headers.get("X-API-Key", "")

    user = None
    if token:
        if token.startswith("tb_"):
            user = security.resolve_api_key(token)
        else:
            user = security.resolve_token_user(token) or security.resolve_api_key(token)
    elif key:
        user = security.resolve_api_key(key)

    if user is None:
        # 也允许在 JSON body / 表单里传 api_key，方便某些不方便设 header 的场景
        payload = request.get_json(silent=True) or {}
        raw = payload.get("api_key") or request.form.get("api_key") or request.args.get("api_key")
        if raw:
            user = security.resolve_api_key(str(raw))

    if user is None:
        return None, json_err(
            "未提供有效凭证。请在请求头加 Authorization: Bearer <API Key> 或 X-API-Key: <API Key>",
            "unauthorized", 401,
        )
    if int(user.get("status") or 0) != 1:
        return None, json_err("账号已被禁用，请联系管理员", "account_disabled", 403)
    return user, None


def load_api(slug: str):
    """按 slug 取接口。

    返回 (api, problem)。problem 取值：
        None         正常可调用
        "not_found"  接口不存在
        "offline"    已下线（用户不可见）
        "maintenance" 维护中（用户可见但不能调）
    """
    db = get_db()
    api = db.query_one("SELECT * FROM apis WHERE slug = %s", (slug,))
    if api is None:
        return None, "not_found"
    status = quicker.normalize_status(api["status"])
    if status == quicker.STATUS_OFFLINE:
        return None, "offline"
    if status == quicker.STATUS_MAINTENANCE:
        return api, "maintenance"
    return api, None


def api_fields(api: dict) -> list[dict]:
    try:
        fields = json.loads(api.get("fields_json") or "[]")
    except (TypeError, ValueError):
        fields = []
    return fields if isinstance(fields, list) else []


def resolve_callable(slug: str):
    """取接口并判断能否调用，返回 (api, 错误响应或 None)。

    三种不可用的情况给出不同语义：
        下线   → 403 offline（用户看不到，属于越权访问）
        维护中 → 503 maintenance（用户可见，只是暂时不服务）
        不存在 → 404 not_found

    错误响应是构造好的 Response（状态码已设），调用方直接 return 即可。
    """
    from flask import jsonify as _jsonify

    api, problem = load_api(slug)

    if problem == "offline":
        resp = _jsonify({"ok": False, "error": {"code": "offline", "message": "该接口已下线"}})
        resp.status_code = 403
        return None, resp

    if problem == "not_found" or api is None:
        resp = _jsonify({"ok": False, "error": {"code": "not_found", "message": "接口不存在"}})
        resp.status_code = 404
        return None, resp

    if problem == "maintenance":
        note = (api.get("maintenance_note") or "").strip()
        message = "该接口正在维护中，暂时无法调用"
        if note:
            message += "：" + note
        resp = _jsonify({"ok": False, "error": {"code": "maintenance", "message": message}})
        resp.status_code = 503
        resp.headers["Retry-After"] = "300"
        return None, resp

    return api, None


def collect_values(fields: list[dict]) -> dict:
    """从 JSON body 或表单里取出这些键的值。"""
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        source = payload
    else:
        source = request.form
    values = {}
    for field in fields:
        key = field.get("key") or ""
        if not key:
            continue
        if key in source:
            value = source.get(key)
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            values[key] = "" if value is None else str(value)
    return values


# ═════════════════════ 核心执行（页面调试也复用） ═════════════════════
def execute_call(user: dict, api: dict, fields: list[dict], values: dict, ip: str = "") -> dict:
    """跑一次调用：校验 → 扣费 → 推送 → 落日志 → 失败退款。"""
    db = get_db()
    cost = max(0, int(api.get("cost_points") or 0))

    errors = quicker.validate_fields(fields, values)
    if errors:
        return {
            "ok": False,
            "error": {"code": "bad_params", "message": "；".join(errors), "fields": errors},
            "cost": 0,
            "points": int(user.get("points") or 0),
        }

    # ── 先原子扣费，避免并发超额 ──
    if cost > 0:
        with db.tx() as conn:
            affected = db.tx_execute(
                conn,
                "UPDATE users SET points = points - %s WHERE id = %s AND points >= %s",
                (cost, user["id"], cost),
            )
        if not affected:
            balance = db.scalar("SELECT points FROM users WHERE id = %s", (user["id"],))
            return {
                "ok": False,
                "error": {
                    "code": "insufficient_points",
                    "message": "积分不足，本次调用需要 %d 积分，当前 %d 积分" % (cost, int(balance)),
                },
                "cost": 0,
                "points": int(balance),
            }

    payload = quicker.build_payload(api, fields, values)
    result = quicker.push(payload)

    # ── 失败退款 ──
    refunded = 0
    if cost > 0 and not result["ok"]:
        with db.tx() as conn:
            db.tx_execute(conn, "UPDATE users SET points = points + %s WHERE id = %s", (cost, user["id"]))
        refunded = 1

    # ── 计数 & 日志 ──
    db.execute(
        "UPDATE apis SET call_count = call_count + 1, success_count = success_count + %s WHERE id = %s",
        (1 if result["ok"] else 0, api["id"]),
    )
    try:
        params_json = json.dumps(quicker.mask_payload(payload), ensure_ascii=False)
    except (TypeError, ValueError):
        params_json = "{}"
    db.insert(
        "INSERT INTO api_logs (user_id, user_email, api_id, api_slug, api_name, params_json, "
        "response_text, is_success, cost_points, refunded, http_status, time_cost_ms, "
        "error_message, ip, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (user["id"], user.get("email") or "", api["id"], api["slug"], api["name"], params_json,
         (result["text"] or "")[:60000], 1 if result["ok"] else 0, cost, refunded,
         result["status"], result["time_cost_ms"], (result["error"] or "")[:500], ip[:64], now_ts()),
    )

    points = int(db.scalar("SELECT points FROM users WHERE id = %s", (user["id"],)) or 0)

    if result["ok"]:
        return {
            "ok": True,
            "data": result["response"] if result["response"] else result["text"],
            "raw": result["text"][:20000],
            "cost": cost - (cost if refunded else 0),
            "points": points,
            "time_cost_ms": result["time_cost_ms"],
        }

    return {
        "ok": False,
        "error": {"code": "push_failed", "message": result["error"] or "推送失败"},
        "raw": result["text"][:20000],
        "cost": 0,
        "refunded": bool(refunded),
        "points": points,
        "time_cost_ms": result["time_cost_ms"],
    }


# ═════════════════════ 接口清单 ═════════════════════
@bp.get("/apis")
def list_apis():
    user, err = authenticate()
    if err:
        return err
    db = get_db()
    rows = db.query(
        "SELECT slug, name, description, cost_points, fields_json, call_count "
        "FROM apis WHERE status = 1 ORDER BY sort_order ASC, id ASC"
    )
    out = []
    for row in rows:
        out.append({
            "slug": row["slug"],
            "name": row["name"],
            "description": row["description"],
            "cost_points": int(row["cost_points"]),
            "call_count": int(row["call_count"]),
            "params": [
                {"key": f.get("key"), "label": f.get("label"),
                 "required": bool(f.get("required")), "default": f.get("default", ""),
                 "hint": f.get("hint", "")}
                for f in api_fields(row)
            ],
            "endpoint": "/api/v1/%s" % row["slug"],
        })
    return jsonify({"ok": True, "apis": out, "points": int(user.get("points") or 0)})


@bp.get("/apis/<slug>")
def api_schema(slug: str):
    user, err = authenticate()
    if err:
        return err
    api, problem = load_api(slug)
    if problem == "offline":
        return json_err("该接口已下线", "offline", 403)
    if problem == "maintenance":
        note = (api.get("maintenance_note") or "").strip()
        message = "该接口正在维护中，暂时无法调用"
        if note:
            message += "：" + note
        return json_err(message, "maintenance", 503, retry_after=300)
    if api is None:
        return json_err("接口不存在", "not_found", 404)
    return jsonify({
        "ok": True,
        "api": {
            "slug": api["slug"],
            "name": api["name"],
            "description": api["description"],
            "cost_points": int(api["cost_points"]),
            "status": quicker.normalize_status(api["status"]),
            "status_label": quicker.status_label(api["status"]),
            "maintenance_note": api.get("maintenance_note") or "",
            "callable": quicker.is_callable(api),
            "params": [
                {"key": f.get("key"), "label": f.get("label"),
                 "required": bool(f.get("required")), "default": f.get("default", ""),
                 "hint": f.get("hint", "")}
                for f in api_fields(api)
            ],
            "endpoint": "/api/v1/%s" % api["slug"],
        },
        "points": int(user.get("points") or 0),
    })


@bp.get("/me")
def me():
    user, err = authenticate()
    if err:
        return err
    return jsonify({
        "ok": True,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "nickname": user.get("nickname"),
            "points": int(user.get("points") or 0),
        },
    })


# ═════════════════════ 执行调用 ═════════════════════
@bp.route("/<slug>", methods=["POST"])
def call_api(slug: str):
    user, err = authenticate()
    if err:
        return err

    api, err = resolve_callable(slug)
    if err is not None:
        return err

    fields = api_fields(api)
    values = collect_values(fields)
    result = execute_call(user, api, fields, values, ip=client_ip())

    status = 200
    if not result["ok"]:
        code = (result.get("error") or {}).get("code")
        status = 402 if code == "insufficient_points" else 400
    return jsonify(result), status


@bp.get("/<slug>")
def call_api_get(slug: str):
    """GET 也支持，但不推荐（参数会进日志/浏览器历史）。"""
    user, err = authenticate()
    if err:
        return err

    api, err = resolve_callable(slug)
    if err is not None:
        return err

    fields = api_fields(api)
    values = {}
    for field in fields:
        key = field.get("key") or ""
        if key and key in request.args:
            values[key] = text(request.args.get(key), 2000)
    result = execute_call(user, api, fields, values, ip=client_ip())
    status = 200
    if not result["ok"]:
        code = (result.get("error") or {}).get("code")
        status = 402 if code == "insufficient_points" else 400
    return jsonify(result), status
