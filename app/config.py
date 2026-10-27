# -*- coding: utf-8 -*-
"""配置文件读写。

config.json / install.lock 全部由安装向导在运行时自动生成，
部署过程中不需要手动创建或编辑任何文件。
"""
from __future__ import annotations

import json
import os
import threading
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOCK_PATH = os.path.join(BASE_DIR, "install.lock")

_lock = threading.Lock()
_cache: dict = {"mtime": None, "data": None}


def is_locked() -> bool:
    """安装锁是否存在（存在 = 已经装好了）。"""
    return os.path.exists(LOCK_PATH)


def is_installed() -> bool:
    return is_locked() and os.path.exists(CONFIG_PATH)


def load() -> dict | None:
    """读取配置，带 mtime 缓存。文件不存在返回 None。"""
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
    except OSError:
        return None

    with _lock:
        if _cache["mtime"] == mtime and _cache["data"] is not None:
            return _cache["data"]

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError):
        return None

    with _lock:
        _cache["mtime"] = mtime
        _cache["data"] = data
    return data


def save(data: dict) -> None:
    """原子写入配置。"""
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)
    with _lock:
        _cache["mtime"] = None
        _cache["data"] = None


def mark_installed() -> None:
    with open(LOCK_PATH, "w", encoding="utf-8") as fp:
        fp.write("installed at %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))


def writable(path: str | None = None) -> bool:
    """检查项目目录是否可写（安装向导会往这里写 config.json）。"""
    target = path or BASE_DIR
    try:
        probe = os.path.join(target, ".write_probe")
        with open(probe, "w", encoding="utf-8") as fp:
            fp.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def site_name() -> str:
    cfg = load() or {}
    return cfg.get("site_name") or "API 分发平台"


def base_url() -> str:
    cfg = load() or {}
    return (cfg.get("base_url") or "").rstrip("/")
