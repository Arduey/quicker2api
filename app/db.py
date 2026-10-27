# -*- coding: utf-8 -*-
"""MySQL 访问层（PyMySQL）。

连接按请求复用（flask.g），请求结束自动归还。
安装向导在执行安装前数据库还不存在，此时 get_db() 返回 None，
调用方需要自己判断（大多数页面在未安装时会先跳到 /install）。
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import pymysql
from flask import g, has_app_context
from pymysql.cursors import DictCursor

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


class Database:
    """薄封装：把常用查询开成方法，避免各处重复写游标代码。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg

    # ── 连接 ──
    def connect(self):
        cfg = self.cfg
        return pymysql.connect(
            host=cfg.get("host") or "127.0.0.1",
            port=int(cfg.get("port") or 3306),
            user=cfg.get("user") or "",
            password=cfg.get("password") or "",
            database=cfg.get("database") or "",
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=True,
            connect_timeout=8,
            read_timeout=60,
            write_timeout=60,
        )

    def _conn(self):
        if has_app_context():
            conn = g.get("_db_conn")
            if conn is None:
                conn = self.connect()
                g._db_conn = conn
            else:
                try:
                    conn.ping(reconnect=True)
                except Exception:
                    conn = self.connect()
                    g._db_conn = conn
            return conn
        return self.connect()

    def close(self):
        if has_app_context():
            conn = g.pop("_db_conn", None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    # ── 查询 ──
    def query(self, sql: str, args=None) -> list[dict]:
        conn = self._conn()
        with conn.cursor() as cur:
            # 注意：必须区分 None 与空元组。pymysql 只要收到非 None 的 args
            # 就会做 `sql % args`，SQL 里含字面 % （如 LIKE 'a%'）时会报
            # "not enough arguments for format string"。
            if args is None:
                cur.execute(sql)
            else:
                cur.execute(sql, args)
            return list(cur.fetchall() or [])

    def query_one(self, sql: str, args=None):
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def scalar(self, sql: str, args=None, default=0):
        row = self.query_one(sql, args)
        if not row:
            return default
        value = list(row.values())[0]
        return default if value is None else value

    def execute(self, sql: str, args=None) -> int:
        conn = self._conn()
        with conn.cursor() as cur:
            if args is None:
                cur.execute(sql)
            else:
                cur.execute(sql, args)
            return cur.rowcount

    def insert(self, sql: str, args=None) -> int:
        conn = self._conn()
        with conn.cursor() as cur:
            if args is None:
                cur.execute(sql)
            else:
                cur.execute(sql, args)
            return int(cur.lastrowid or 0)

    def executemany(self, sql: str, seq) -> int:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.executemany(sql, seq)
            return cur.rowcount

    def upsert_setting(self, key: str, value: str, ts: int) -> None:
        self.execute(
            "INSERT INTO settings (k, v, updated_at) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE v = VALUES(v), updated_at = VALUES(updated_at)",
            (key, value, ts),
        )

    # ── 事务 ──
    @contextmanager
    def tx(self):
        """整段操作走同一个连接 + 显式事务，用于扣积分之类必须原子化的动作。"""
        conn = self._conn()
        conn.begin()
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()

    def tx_query_one(self, conn, sql, args=None):
        with conn.cursor() as cur:
            if args is None:
                cur.execute(sql)
            else:
                cur.execute(sql, args)
            rows = list(cur.fetchall() or [])
            return rows[0] if rows else None

    def tx_execute(self, conn, sql, args=None) -> int:
        with conn.cursor() as cur:
            if args is None:
                cur.execute(sql)
            else:
                cur.execute(sql, args)
            return cur.rowcount


# ───────────── 全局单例（应用启动时构建） ─────────────
_db: Database | None = None


def init_db(cfg: dict) -> Database:
    global _db
    _db = Database(cfg)
    return _db


def get_db() -> Database | None:
    """懒加载：安装向导刚写完 config.json 时，进程还没重启也能直接用上新库。

    没有这一步的话，装完后必须手动重启 Python 项目，数据库才会生效。
    """
    global _db
    if _db is not None:
        return _db

    from . import config

    if not config.is_installed():
        return None
    cfg = config.load() or {}
    db_cfg = cfg.get("db") or {}
    if not db_cfg.get("host"):
        return None
    try:
        return init_db(db_cfg)
    except Exception:  # noqa: BLE001 库暂时连不上时交给上层按未安装处理
        import logging

        logging.warning("数据库懒加载失败", exc_info=True)
        return None


# ───────────── 建表 ─────────────
def schema_statements() -> list[str]:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fp:
        raw = fp.read()
    lines = [ln for ln in raw.splitlines() if not ln.strip().startswith("--")]
    parts = "\n".join(lines).split(";")
    return [p.strip() for p in parts if p.strip()]


def install_schema(db: Database | None = None) -> int:
    """执行建表语句（全部 IF NOT EXISTS，可重复执行）。返回执行的语句数。"""
    target = db or get_db()
    if target is None:
        raise RuntimeError("数据库未初始化")
    statements = schema_statements()
    for sql in statements:
        target.execute(sql)
    return len(statements)


_USE_CFG_DATABASE = object()


def test_connection(cfg: dict, database=_USE_CFG_DATABASE) -> tuple[bool, str]:
    """安装向导用：只测连通性，不改任何东西。

    database 的三种用法：
        不传           → 用 cfg 里自带的库名
        database=None  → 不指定库（用于"还没建库"时测服务器能否登录）
        database="x"   → 测指定的库是否存在、能否访问
    """
    probe = dict(cfg)
    if database is not _USE_CFG_DATABASE:
        probe["database"] = database or ""
    try:
        conn = Database(probe).connect()
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION() AS v")
            row = cur.fetchone()
        conn.close()
        return True, str((row or {}).get("v") or "")
    except Exception as exc:  # noqa: BLE001 安装向导需要把原始报错给用户看
        return False, str(exc)
