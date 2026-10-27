#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本地代码同步到服务器（FTP），不需要打包、不需要解压。

用法：
    python deploy.py              # 同步有变化的文件
    python deploy.py --all        # 强制全部重传
    python deploy.py --dry-run    # 只看会传哪些，不真传
    python deploy.py --delete     # 额外删除远端多余文件（默认不删，避免误删）

连接信息从环境变量读，不写死在代码里：
    FTP_HOST=38.246.253.104
    FTP_PORT=23
    FTP_USER=quicker2api
    FTP_PASS=******

也可以放在项目根目录的 .deploy.env（已在 .gitignore 里，不会被提交）：
    FTP_HOST=38.246.253.104
    FTP_PORT=23
    FTP_USER=quicker2api
    FTP_PASS=******

注意：上传完代码不会自动生效，需要重启 Python 项目（面板点一下），
或者给 gunicorn 配置加 reload = True。
"""
from __future__ import annotations

import argparse
import ftplib
import os
import posixpath
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# 只同步这些目录 / 文件（和交付包内容一致）
SYNC_DIRS = ["app", "templates", "static"]
SYNC_FILES = ["wsgi.py", "app.py", "requirements.txt", "README.md"]

# 永远不上传的东西
SKIP_DIRS = {"__pycache__", ".git", ".reasonix", ".venv", "venv", ".idea", ".vscode"}
SKIP_FILES = {
    "config.json", "config.json.tmp", "install.lock", "toolbox.zip",
    ".deploy.env", ".deploy_manifest.json", "gunicorn.pid",
    "gunicorn_conf.py", "uwsgi.ini",
}
SKIP_SUFFIX = (".pyc", ".pyo", ".log")


# ───────────────────── 配置 ─────────────────────
def load_env() -> dict:
    env = {}

    # 先读 .deploy.env（同目录）
    path = os.path.join(ROOT, ".deploy.env")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip("'\"")

    # 环境变量优先
    for key in ("FTP_HOST", "FTP_PORT", "FTP_USER", "FTP_PASS"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def require(env: dict) -> tuple[str, int, str, str]:
    missing = [k for k in ("FTP_HOST", "FTP_USER", "FTP_PASS") if not env.get(k)]
    if missing:
        print("缺少连接信息：%s" % "、".join(missing))
        print("请设置环境变量，或在项目根目录新建 .deploy.env（格式见脚本开头注释）")
        sys.exit(2)
    return (
        env["FTP_HOST"],
        int(env.get("FTP_PORT") or 21),
        env["FTP_USER"],
        env["FTP_PASS"],
    )


# ───────────────────── 收集本地文件 ─────────────────────
def keep(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return False
    name = parts[-1]
    if name in SKIP_FILES or name.startswith(".deploy"):
        return False
    if name.endswith(SKIP_SUFFIX):
        return False
    return True


def collect() -> list[str]:
    files: list[str] = []
    for name in SYNC_FILES:
        full = os.path.join(ROOT, name)
        if os.path.isfile(full) and keep(name):
            files.append(name)
    for folder in SYNC_DIRS:
        base = os.path.join(ROOT, folder)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for filename in sorted(filenames):
                full = os.path.join(dirpath, filename)
                rel = os.path.relpath(full, ROOT).replace("\\", "/")
                if keep(rel):
                    files.append(rel)
    return sorted(files)


# ───────────────────── FTP 操作 ─────────────────────
def remote_size(ftp: ftplib.FTP, path: str) -> int | None:
    try:
        return ftp.size(path)
    except ftplib.error_perm:
        return None  # 文件不存在
    except Exception:  # noqa: BLE001 某些服务器不支持 SIZE
        return None


def ensure_dir(ftp: ftplib.FTP, path: str) -> None:
    """逐级创建远端目录，已存在则跳过。"""
    parts = path.strip("/").split("/")
    current = ""
    for part in parts:
        current = posixpath.join(current, part) if current else part
        try:
            ftp.mkd(current)
        except ftplib.error_perm as exc:
            if "exist" not in str(exc).lower() and "550" not in str(exc):
                raise


def remote_files(ftp: ftplib.FTP, path: str = "") -> set[str]:
    """递归列出远端已有文件（相对路径）。"""
    found: set[str] = set()
    try:
        entries = list(ftp.mlsd(path or "."))
        for name, facts in entries:
            if name in (".", ".."):
                continue
            rel = posixpath.join(path, name) if path else name
            if facts.get("type") == "dir":
                found |= remote_files(ftp, rel)
            elif facts.get("type") == "file":
                found.add(rel)
        return found
    except (ftplib.error_perm, AttributeError):
        pass

    # 退化到 LIST 解析
    try:
        lines = []
        ftp.dir(path or ".", lines.append)
    except ftplib.error_perm:
        return found
    for line in lines:
        fields = line.split(maxsplit=8)
        if len(fields) < 9:
            continue
        name = fields[8].strip()
        if name in (".", ".."):
            continue
        rel = posixpath.join(path, name) if path else name
        if line.startswith("d"):
            found |= remote_files(ftp, rel)
        else:
            found.add(rel)
    return found


# ───────────────────── 主流程 ─────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="通过 FTP 同步代码到服务器")
    parser.add_argument("--all", action="store_true", help="强制全部重传")
    parser.add_argument("--dry-run", action="store_true", help="只显示计划，不执行")
    parser.add_argument("--delete", action="store_true", help="删除远端多余文件")
    args = parser.parse_args()

    env = load_env()
    host, port, user, password = require(env)

    local = collect()
    print("本地待同步：%d 个文件" % len(local))

    print("连接 %s:%d ..." % (host, port))
    ftp = ftplib.FTP()
    ftp.connect(host, port, timeout=30)
    ftp.login(user, password)
    ftp.set_pasv(True)
    print("已登录：%s" % (ftp.getwelcome() or "")[:80])

    uploaded: list[str] = []
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []

    # 预取远端文件大小，避免每个文件都单独问一遍
    try:
        ftp.voidcmd("TYPE I")
    except ftplib.error_perm:
        pass

    for rel in local:
        full = os.path.join(ROOT, rel)
        local_size = os.path.getsize(full)

        if not args.all:
            size = remote_size(ftp, rel)
            if size is not None and size == local_size:
                skipped.append(rel)
                continue

        if args.dry_run:
            uploaded.append(rel)
            continue

        try:
            parent = posixpath.dirname(rel)
            if parent:
                ensure_dir(ftp, parent)
            with open(full, "rb") as fp:
                ftp.storbinary("STOR " + rel, fp)
            uploaded.append(rel)
        except Exception as exc:  # noqa: BLE001
            failed.append((rel, str(exc)))

    deleted: list[str] = []
    if args.delete and not args.dry_run:
        try:
            existing = remote_files(ftp)
        except Exception as exc:  # noqa: BLE001
            existing = set()
            print("列远端文件失败，跳过删除：%s" % exc)
        keep_set = set(local) | SKIP_FILES
        for rel in sorted(existing - keep_set):
            if os.path.basename(rel) in SKIP_FILES or rel.endswith(SKIP_SUFFIX):
                continue
            if any(part in SKIP_DIRS for part in rel.split("/")):
                continue
            try:
                ftp.delete(rel)
                deleted.append(rel)
            except Exception as exc:  # noqa: BLE001
                failed.append((rel, str(exc)))

    try:
        ftp.quit()
    except Exception:  # noqa: BLE001
        ftp.close()

    print("\n" + "=" * 50)
    if args.dry_run:
        print("【演练模式】将上传 %d 个文件：" % len(uploaded))
        for rel in uploaded:
            print("  + " + rel)
    else:
        print("上传 %d 个，跳过 %d 个（未变化），失败 %d 个" % (
            len(uploaded), len(skipped), len(failed)))
        for rel in uploaded:
            print("  + " + rel)
        if deleted:
            print("删除 %d 个远端多余文件：" % len(deleted))
            for rel in deleted:
                print("  - " + rel)

    if failed:
        print("\n失败明细：")
        for rel, err in failed:
            print("  ! %s -> %s" % (rel, err))
        return 1

    print("\n完成。记得在宝塔「Python 项目」里点一次【重启】，新代码才会生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
