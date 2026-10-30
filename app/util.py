# -*- coding: utf-8 -*-
"""小工具集合：时间、校验、CSRF、settings 表读写、统一 JSON 响应。"""
from __future__ import annotations

import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from flask import jsonify, request, session

TZ = timezone(timedelta(hours=8))

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+$")
SLUG_RE = re.compile(r"[^a-z0-9_\-]+")


# ───────────────────────── 时间 ─────────────────────────
def now_ts() -> int:
    return int(time.time())


def fmt_ts(ts, pattern: str = "%Y-%m-%d %H:%M:%S") -> str:
    try:
        value = int(ts)
    except (TypeError, ValueError):
        return "-"
    if value <= 0:
        return "-"
    return datetime.fromtimestamp(value, TZ).strftime(pattern)


def fmt_ago(ts) -> str:
    try:
        value = int(ts)
    except (TypeError, ValueError):
        return "-"
    if value <= 0:
        return "-"
    diff = now_ts() - value
    if diff < 60:
        return "刚刚"
    if diff < 3600:
        return "%d 分钟前" % (diff // 60)
    if diff < 86400:
        return "%d 小时前" % (diff // 3600)
    if diff < 86400 * 30:
        return "%d 天前" % (diff // 86400)
    return fmt_ts(value, "%Y-%m-%d")


def day_buckets(days: int = 7) -> list[tuple[int, int, str]]:
    """返回最近 N 天的 (起始时间戳, 结束时间戳, 日期标签)，含今天，按时间升序。

    标签用「月-日」，图表横轴直接用。
    """
    today = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for offset in range(days - 1, -1, -1):
        start = today - timedelta(days=offset)
        end = start + timedelta(days=1)
        out.append((
            int(start.timestamp()),
            int(end.timestamp()),
            start.strftime("%m-%d"),
        ))
    return out


def build_trend(rows, days: int = 7) -> list[dict]:
    """把「按天 group by」的查询结果整理成连续的 N 天序列。

    rows: [{"d": "2026-10-20", "total": 12, "ok": 10}, ...]
    没有数据的那天补 0，保证图表横轴连续、不会跳日期。
    """
    by_day: dict[str, dict] = {}
    for row in rows or []:
        key = str(row.get("d") or "")
        by_day[key] = {
            "total": int(row.get("total") or 0),
            "ok": int(row.get("ok") or 0),
        }

    series = []
    for start, _end, label in day_buckets(days):
        day_key = datetime.fromtimestamp(start, TZ).strftime("%Y-%m-%d")
        hit = by_day.get(day_key, {})
        total = hit.get("total", 0)
        ok_count = hit.get("ok", 0)
        series.append({
            "label": label,
            "total": total,
            "ok": ok_count,
            "failed": max(0, total - ok_count),
            "rate": round(ok_count * 100.0 / total, 1) if total else None,
        })
    return series


# ───────────────────────── 随机 ─────────────────────────
def rand_hex(nbytes: int = 16) -> str:
    return secrets.token_hex(nbytes)


def new_api_key() -> str:
    return "tb_" + secrets.token_hex(24)


def new_secret_key() -> str:
    return secrets.token_hex(32)


# ───────────────────────── 校验 ─────────────────────────
def is_valid_email(email: str) -> bool:
    email = (email or "").strip()
    return bool(EMAIL_RE.match(email)) and len(email) <= 190


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def normalize_slug(raw: str) -> str:
    value = (raw or "").strip().lower()
    value = SLUG_RE.sub("-", value).strip("-")
    return value[:64]


def to_int(value, default: int = 0, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        number = default
    if minimum is not None and number < minimum:
        number = minimum
    if maximum is not None and number > maximum:
        number = maximum
    return number


def to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y", "t")


def text(value, limit: int = 500) -> str:
    return ("" if value is None else str(value)).strip()[:limit]


# ───────────────────────── 用户名（昵称） ─────────────────────────
NICKNAME_MIN = 2
NICKNAME_MAX = 24
# 允许中英文、数字、下划线、短横线；禁止空格和易混淆符号
_NICKNAME_BAD = set(' \t\r\n/@\\:*?"<>|\'')


def normalize_nickname(raw) -> str:
    return text(raw, NICKNAME_MAX * 2)


def nickname_problem(nickname: str) -> str | None:
    """校验用户名，返回错误提示；None 表示合规。

    用户名是用户对外的展示名，要求：必填、2-24 字符、唯一（唯一性由调用方查库）。
    """
    value = (nickname or "").strip()
    if not value:
        return "用户名不能为空"
    if len(value) < NICKNAME_MIN:
        return "用户名至少 %d 个字符" % NICKNAME_MIN
    if len(value) > NICKNAME_MAX:
        return "用户名最多 %d 个字符" % NICKNAME_MAX
    bad = sorted({ch for ch in value if ch in _NICKNAME_BAD})
    if bad:
        return "用户名不能包含 %s" % " ".join(bad)
    if value.isdigit():
        return "用户名不能是纯数字"
    return None


def nickname_taken(db, nickname: str, exclude_user_id: int | None = None) -> bool:
    """用户名是否已被占用（不区分大小写，避免「小明」和「小明 」这类混淆）。"""
    if db is None:
        return False
    sql = "SELECT id FROM users WHERE nickname = %s"
    args: list = [nickname]
    if exclude_user_id:
        sql += " AND id <> %s"
        args.append(int(exclude_user_id))
    return db.query_one(sql, tuple(args)) is not None


# ───────────────────────── 客户端 IP ─────────────────────────
def client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.headers.get("X-Real-IP") or request.remote_addr or "")[:64]


# ───────────────────────── CSRF ─────────────────────────
def ensure_csrf() -> str:
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    return token


def csrf_valid() -> bool:
    token = session.get("csrf")
    sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token") or ""
    if not token or not sent:
        return False
    return hmac_compare(token, sent)


def hmac_compare(a: str, b: str) -> bool:
    import hmac as _hmac

    return _hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


# ───────────────────────── JSON 响应 ─────────────────────────
def json_ok(**payload):
    body = {"ok": True}
    body.update(payload)
    return jsonify(body)


def json_err(message: str, code: str = "error", status: int = 400, **extra):
    body = {"ok": False, "error": {"code": code, "message": message}}
    body.update(extra)
    return jsonify(body), status


# ───────────────────────── settings 表 ─────────────────────────
def setting_get(key: str, default=None):
    from .db import get_db

    db = get_db()
    if db is None:
        return default
    row = db.query_one("SELECT v FROM settings WHERE k = %s", (key,))
    if not row:
        return default
    raw = row["v"]
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def setting_set(key: str, value) -> None:
    from .db import get_db

    db = get_db()
    if db is None:
        return
    db.upsert_setting(key, json.dumps(value, ensure_ascii=False), now_ts())


def settings_all() -> dict:
    from .db import get_db

    db = get_db()
    if db is None:
        return {}
    result = {}
    for row in db.query("SELECT k, v FROM settings"):
        try:
            result[row["k"]] = json.loads(row["v"])
        except (TypeError, ValueError):
            result[row["k"]] = row["v"]
    return result
