# -*- coding: utf-8 -*-
"""Quicker 推送服务（长连接）对接。

接口（V2）：POST https://push.getquicker.cn/to/quicker
请求体：JSON
    toUser     账号 email          —— 全局设置
    code       推送验证码           —— 全局设置
    toDevice   目标设备             —— 全局设置
    operation  操作类型             —— 全局设置
    action     动作名/ID            —— 每个 API 自己配，默认空
    wait       是否等待结果         —— 每个 API 自己配，默认 true
    maxWaitMs  最长等待毫秒         —— 每个 API 自己配，默认 30000
    txt        是否返回纯文本       —— 每个 API 自己配，默认 false
    data       动作参数             —— 平台用户填写的键值 → 压缩 JSON 文本

返回 JSON：
    {"isSuccess": true, "successCount": 1, "errorMessage": null,
     "timeCost": 219, "devices": {"DESKTOP-X": "Ok."}}
"""
from __future__ import annotations

import json
import time

import requests

from .util import now_ts, setting_get, setting_set, text

PUSH_URL = "https://push.getquicker.cn/to/quicker"
DEVICES_URL = "https://push.getquicker.cn/get/devices"
MAX_WAIT_LIMIT = 30000
MAX_DATA_BYTES = 30 * 1024  # 接口上限 30KB

OPERATIONS = [
    ("action", "action — 运行动作（通过 action 参数指定动作名或 ID）"),
    ("copy", "copy — 将内容复制到剪贴板"),
    ("paste", "paste — 将内容粘贴到当前窗口"),
    ("open", "open — 打开网址或文件路径"),
    ("input", "input — 模拟按键（sendkeys 语法，1.27.3+ 请用 sendkeys）"),
    ("sendkeys", "sendkeys — 模拟按键（sendkeys 语法）"),
    ("inputtext", "inputtext — 模拟输入文本（原样输入）"),
    ("inputscript", "inputscript — 多步骤输入（1.28.16+）"),
    ("downloadfile", "downloadfile — 下载文件（1.28.16+）"),
]

OPERATION_KEYS = [k for k, _ in OPERATIONS]


# ───────────────────────── 全局设置 ─────────────────────────
GLOBAL_DEFAULTS = {
    "to_user": "",
    "code": "",
    "to_device": "",          # 空 = 活动主机；* = 所有主机；!电脑名 = 排除；电脑名 = 指定
    "operation": "action",    # 本平台主要用途是"运行动作"
    "timeout": 35,            # 给 requests 的读超时，比 maxWaitMs 略大
}


def global_settings() -> dict:
    cfg = dict(GLOBAL_DEFAULTS)
    for key in GLOBAL_DEFAULTS:
        stored = setting_get("quicker_" + key, None)
        if stored is not None:
            cfg[key] = stored
    cfg["operation"] = cfg.get("operation") or "action"
    try:
        cfg["timeout"] = max(5, min(120, int(cfg.get("timeout") or 35)))
    except (TypeError, ValueError):
        cfg["timeout"] = 35
    return cfg


def save_global_settings(form: dict) -> list[str]:
    """保存全局设置，返回错误列表（空 = 成功）。"""
    errors: list[str] = []
    to_user = text(form.get("to_user"), 190)
    code = text(form.get("code"), 64)
    to_device = text(form.get("to_device"), 64)
    operation = text(form.get("operation"), 32) or "action"
    timeout = text(form.get("timeout"), 8)

    if to_user and "@" not in to_user:
        errors.append("toUser 必须是 Quicker 账号的邮箱地址")
    if operation not in OPERATION_KEYS:
        errors.append("operation 不在可选值范围内")
    if errors:
        return errors

    try:
        timeout_value = max(5, min(120, int(timeout or 35)))
    except ValueError:
        timeout_value = 35

    setting_set("quicker_to_user", to_user)
    setting_set("quicker_code", code)
    setting_set("quicker_to_device", to_device)
    setting_set("quicker_operation", operation)
    setting_set("quicker_timeout", timeout_value)
    return []


def fetch_devices() -> tuple[bool, dict | list]:
    """查询已连接设备列表，供后台选择 toDevice。"""
    cfg = global_settings()
    if not cfg["to_user"] or not cfg["code"]:
        return False, "请先填写 toUser 与 code"
    try:
        resp = requests.get(
            DEVICES_URL,
            params={"email": cfg["to_user"], "code": cfg["code"]},
            timeout=cfg["timeout"],
        )
        return True, resp.json()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:300]


# ───────────────────────── data 组装 ─────────────────────────
def build_data(fields: list[dict], values: dict) -> str:
    """把"用户填写的 key -> value"做成压缩 JSON 文本，作为 data 的值。

    fields: [{"key": "name", "label": "姓名", "required": true, "default": ""}, ...]
    values: {"name": "张三"}
    返回：'{"name":"张三"}'  —— 单层键值对，不换行
    """
    ordered: dict[str, str] = {}
    for field in fields:
        key = (field.get("key") or "").strip()
        if not key:
            continue
        raw = values.get(key, None)
        if raw is None or raw == "":
            raw = field.get("default", "")
        ordered[key] = "" if raw is None else str(raw)
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))


def validate_fields(fields: list[dict], values: dict) -> list[str]:
    """校验用户提交的键值，返回错误列表。"""
    errors: list[str] = []
    for field in fields:
        key = (field.get("key") or "").strip()
        if not key:
            continue
        raw = values.get(key, None)
        if (raw is None or str(raw).strip() == "") and field.get("required"):
            if not str(field.get("default") or ""):
                errors.append("缺少必填参数：%s" % (field.get("label") or key))
    return errors


def normalize_field_list(raw) -> tuple[list[dict], list[str]]:
    """清洗后台提交的字段定义，返回 (fields, errors)。"""
    fields: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return fields, errors

    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        key = text(item.get("key"), 64)
        if not key:
            continue
        if not key.replace("_", "").replace("-", "").isalnum() or key[0].isdigit():
            errors.append("第 %d 个参数的键名「%s」不合法：只能用字母、数字、下划线、短横线，且不能以数字开头" % (index, key))
            continue
        if len(key) > 64:
            errors.append("第 %d 个参数的键名过长（最多 64 字符）" % index)
            continue
        if key in seen:
            errors.append("参数键名「%s」重复" % key)
            continue
        seen.add(key)
        fields.append({
            "key": key,
            "label": text(item.get("label"), 64) or key,
            "required": bool(item.get("required")),
            "default": text(item.get("default"), 500),
            "hint": text(item.get("hint"), 200),
        })
    if len(fields) > 50:
        errors.append("参数最多 50 个")
        fields = fields[:50]
    return fields, errors


# ───────────────────────── 推送 ─────────────────────────
def build_payload(api: dict, fields: list[dict], values: dict) -> dict:
    """按 API 配置 + 全局设置组装最终推送给 Quicker 的 JSON。"""
    cfg = global_settings()
    payload = {
        "toUser": cfg["to_user"],
        "code": cfg["code"],
        "toDevice": cfg["to_device"],
        "operation": cfg["operation"],
        "action": (api.get("action") or "").strip(),
        "wait": bool(int(api.get("wait_flag") if api.get("wait_flag") is not None else 1)),
        "maxWaitMs": int(api.get("max_wait_ms") or MAX_WAIT_LIMIT),
        "txt": bool(int(api.get("txt_flag") if api.get("txt_flag") is not None else 0)),
        "data": build_data(fields, values),
    }
    payload["maxWaitMs"] = max(0, min(MAX_WAIT_LIMIT, payload["maxWaitMs"]))
    return payload


def mask_payload(payload: dict) -> dict:
    """日志里给 code 打码。"""
    masked = dict(payload)
    code = str(masked.get("code") or "")
    if code:
        masked["code"] = code[:2] + "*" * max(0, len(code) - 4) + code[-2:] if len(code) > 4 else "****"
    return masked


def push(payload: dict, timeout: int | None = None) -> dict:
    """发起推送。返回统一的执行结果，方便落日志。

    {"ok": bool, "status": int, "response": dict|str, "text": str,
     "error": str, "time_cost_ms": int}
    """
    cfg = global_settings()
    started = time.time()
    result = {
        "ok": False,
        "status": 0,
        "response": {},
        "text": "",
        "error": "",
        "time_cost_ms": 0,
    }

    if not cfg["to_user"] or not cfg["code"]:
        result["error"] = "平台尚未配置 Quicker 的 toUser / code，请联系管理员"
        return result

    data_size = len(str(payload.get("data") or "").encode("utf-8"))
    if data_size > MAX_DATA_BYTES:
        result["error"] = "data 超过 30KB 上限（当前 %d 字节）" % data_size
        return result

    try:
        resp = requests.post(
            PUSH_URL,
            json=payload,
            timeout=(10, timeout or cfg["timeout"]),
        )
        result["status"] = resp.status_code
        result["text"] = (resp.text or "")[:20000]
        ctype = resp.headers.get("Content-Type", "")
        if "json" in ctype.lower():
            try:
                result["response"] = resp.json()
            except ValueError:
                result["response"] = {}
        if resp.status_code >= 400:
            result["error"] = "Quicker 接口返回 HTTP %d" % resp.status_code
        else:
            body = result["response"]
            if isinstance(body, dict) and "isSuccess" in body:
                result["ok"] = bool(body.get("isSuccess"))
                if not result["ok"]:
                    result["error"] = str(body.get("errorMessage") or "推送失败")
            elif isinstance(body, dict):
                result["ok"] = True
            else:
                result["ok"] = result["text"].startswith("SUCCESS")
                if not result["ok"] and result["text"].startswith("ERROR:"):
                    result["error"] = result["text"][6:].strip()[:400]
    except requests.Timeout:
        result["error"] = "等待 Quicker 返回结果超时（maxWaitMs=%s）" % payload.get("maxWaitMs")
    except Exception as exc:  # noqa: BLE001
        result["error"] = "请求 Quicker 失败：%s" % str(exc)[:300]

    result["time_cost_ms"] = int((time.time() - started) * 1000)
    return result


def now() -> int:
    return now_ts()
