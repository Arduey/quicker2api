# -*- coding: utf-8 -*-
"""QPS 限流：按用户 + 按接口两个维度。

为什么用数据库而不是进程内存：
    gunicorn 是多 worker（本项目 4 个），内存计数各算各的，
    实际放行量会变成「限制 × worker 数」，限流形同虚设。
    所以计数落在 MySQL 的 rate_counters 表，按 (维度, 目标, 秒) 累加。

精度说明：
    「自增 + 读回」不是原子操作，并发瞬间可能多放过一两个请求。
    对 QPS 保护来说这点误差可以接受，换来的是实现简单、不依赖 Redis。
"""
from __future__ import annotations

import time

from .db import get_db
from .util import setting_get

# settings 表里的键名
KEY_PER_USER = "qps_per_user"
KEY_PER_API = "qps_per_api"

# 保留最近多少秒的计数（够判定即可，不必留长）
KEEP_SECONDS = 120


def limits() -> tuple[int, int]:
    """返回 (每用户 QPS, 每接口 QPS)。0 表示该维度不限。"""
    def _read(key: str) -> int:
        try:
            value = int(setting_get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0
        # 上限 10000 防止误填天文数字导致毫无保护
        return max(0, min(10000, value))

    return _read(KEY_PER_USER), _read(KEY_PER_API)


def _bump(db, bucket: str, target: str, second_ts: int) -> int:
    """把某一秒的计数 +1，返回累加后的值。"""
    db.execute(
        "INSERT INTO rate_counters (bucket, target, second_ts, hits) VALUES (%s, %s, %s, 1) "
        "ON DUPLICATE KEY UPDATE hits = hits + 1",
        (bucket, target, second_ts),
    )
    return int(db.scalar(
        "SELECT hits FROM rate_counters WHERE bucket = %s AND target = %s AND second_ts = %s",
        (bucket, target, second_ts)) or 0)


def check(user_id, api_id) -> str:
    """检查是否超限。返回空串表示放行，否则返回给用户看的错误文案。

    两个维度都要过：任一超标就拒绝。
    """
    limit_user, limit_api = limits()
    if not limit_user and not limit_api:
        return ""

    db = get_db()
    if db is None:
        # 数据库不可用时不该把调用全挡死，放行让后续逻辑自己报错
        return ""

    now_ts = int(time.time())

    if limit_user and user_id:
        hits = _bump(db, "user", str(int(user_id)), now_ts)
        if hits > limit_user:
            return "调用过于频繁：每个账号最多 %d 次/秒，请稍后重试" % limit_user

    if limit_api and api_id:
        hits = _bump(db, "api", str(int(api_id)), now_ts)
        if hits > limit_api:
            return "该接口调用过于频繁：最多 %d 次/秒，请稍后重试" % limit_api

    return ""


def cleanup(force: bool = False) -> None:
    """删除过期的计数行。

    每次调用都清理会白白多一条 DELETE，所以按概率触发（约 1/200）。
    force=True 用于测试或手工维护。
    """
    if not force and int(time.time() * 1000) % 200 != 0:
        return
    db = get_db()
    if db is None:
        return
    try:
        db.execute("DELETE FROM rate_counters WHERE second_ts < %s",
                   (int(time.time()) - KEEP_SECONDS,))
    except Exception:  # noqa: BLE001 清理失败不影响主流程
        pass
