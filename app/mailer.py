# -*- coding: utf-8 -*-
"""SMTP 发信 + 邮箱验证码。

发信部分沿用参考示例的写法（465 走 SMTP_SSL，其余端口走 STARTTLS），
SMTP 参数来自安装向导 / 后台设置，验证码存 MySQL 而不是内存，
这样多 worker（gunicorn）下也不会出现"A 发的码 B 校验不到"。
"""
from __future__ import annotations

import logging
import secrets
import smtplib
import time
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from .db import get_db
from .util import is_valid_email, normalize_email, now_ts

log = logging.getLogger("email_code")

CODE_LENGTH = 6
CODE_CHARS = "0123456789"
CODE_TTL_SECONDS = 10 * 60
RESEND_INTERVAL_SECONDS = 60
HOURLY_CAP = 10  # 同一邮箱每小时最多发 10 封


# ───────────────────────── SMTP 配置 ─────────────────────────
def smtp_config() -> dict:
    from .util import setting_get

    return {
        "host": setting_get("smtp_host", "") or "",
        "port": int(setting_get("smtp_port", 465) or 465),
        "user": setting_get("smtp_user", "") or "",
        "password": setting_get("smtp_password", "") or "",
        "display_name": setting_get("smtp_display_name", "") or "",
        "timeout": int(setting_get("smtp_timeout", 15) or 15),
    }


def send_mail(to: str, subject: str, html: str, cfg: dict | None = None) -> tuple[bool, str]:
    """返回 (ok, 错误信息)。错误信息只在失败时有值。"""
    cfg = cfg or smtp_config()
    host, user, password = cfg.get("host"), cfg.get("user"), cfg.get("password")
    if not (host and user and password):
        return False, "SMTP 未配置"

    port = int(cfg.get("port") or 465)
    timeout = int(cfg.get("timeout") or 15)
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr((cfg.get("display_name") or user, user))
        msg["To"] = to
        msg.attach(MIMEText(html, "html", "utf-8"))

        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
        with server:
            if port != 465:
                try:
                    server.starttls()
                except smtplib.SMTPException:
                    pass  # 有些内网服务器不支持 STARTTLS，直接发
            server.login(user, password)
            server.sendmail(user, [to], msg.as_string())
        return True, ""
    except Exception as exc:  # noqa: BLE001 发信失败要把原因带给调用方
        log.warning("邮件发送失败 -> %s : %s", to, exc)
        return False, str(exc)[:300]


# ───────────────────────── 邮件模板 ─────────────────────────
def brand() -> dict:
    from . import config

    cfg = config.load() or {}
    return {
        "name": cfg.get("site_name") or "API 分发平台",
        "from": cfg.get("brand_from") or "#FF7A1A",
        "to": cfg.get("brand_to") or "#E24B06",
    }


def render_html(code: str, subject_intro: str = "请在校验页面填写") -> str:
    b = brand()
    minutes = max(1, CODE_TTL_SECONDS // 60)
    return f"""<!doctype html><html><body style="margin:0;background:#F4F0E8;padding:28px 12px;font-family:'PingFang SC','Microsoft YaHei',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table width="380" cellpadding="0" cellspacing="0" style="width:380px;background:#fff;border-radius:16px;overflow:hidden;border:1px solid #ECE6D8;box-shadow:0 14px 34px rgba(84,64,24,.10)">
  <tr><td style="background:linear-gradient(135deg,{b['from']} 0%,#F95D1F 55%,{b['to']} 100%);padding:26px 30px">
      <div style="color:#fff;font-size:15px;font-weight:800">{b['name']}</div>
      <div style="color:rgba(255,255,255,.82);font-size:10px;letter-spacing:2px;margin-top:3px">EMAIL VERIFY</div>
      <div style="margin-top:18px;color:#fff;font-size:21px;font-weight:800">邮箱验证</div>
      <div style="color:rgba(255,255,255,.86);font-size:13px;line-height:1.7;margin-top:6px">{subject_intro}。</div>
  </td></tr>
  <tr><td style="padding:26px 30px 8px;text-align:center">
      <div style="color:#B7AFA0;font-size:11px;font-weight:700;letter-spacing:1px">验证码（{minutes} 分钟内有效）</div>
      <div style="margin:12px 0 0;font-size:34px;font-weight:800;letter-spacing:8px;color:#201D17">{code}</div>
      <div style="margin-top:12px;font-size:12px;color:#7E6F3C">请勿把验证码告知他人，谨防钓鱼与诈骗。</div>
  </td></tr>
  <tr><td style="padding:16px 30px;border-top:1px dashed #ECE6D8;color:#B4AC99;font-size:11px">此邮件由系统自动发送·请勿直接回复</td></tr>
</table></td></tr></table></body></html>"""


# ───────────────────────── 验证码 ─────────────────────────
def gen_code() -> str:
    return "".join(secrets.choice(CODE_CHARS) for _ in range(CODE_LENGTH))


def send_code(email: str, purpose: str = "register", ip: str = "") -> tuple[bool, str]:
    """发验证码，返回 (ok, 给前端看的提示)。"""
    db = get_db()
    email = normalize_email(email)
    if not is_valid_email(email):
        return False, "邮箱格式不正确"

    now = now_ts()

    if purpose == "register":
        exists = db.query_one("SELECT id FROM users WHERE email = %s", (email,))
        if exists:
            return False, "该邮箱已注册，请直接登录或更换邮箱"

    row = db.query_one(
        "SELECT id, last_sent_at FROM email_codes WHERE email = %s AND purpose = %s "
        "ORDER BY id DESC LIMIT 1",
        (email, purpose),
    )
    if row:
        elapsed = now - int(row["last_sent_at"] or 0)
        if elapsed < RESEND_INTERVAL_SECONDS:
            return False, "发送过于频繁，请 %d 秒后重试" % (RESEND_INTERVAL_SECONDS - elapsed + 1)

    hourly = db.scalar(
        "SELECT COUNT(*) AS c FROM email_codes WHERE email = %s AND created_at > %s",
        (email, now - 3600),
    )
    if int(hourly or 0) >= HOURLY_CAP:
        return False, "该邮箱发送次数过多，请稍后再试"

    code = gen_code()
    db.execute("DELETE FROM email_codes WHERE email = %s AND purpose = %s", (email, purpose))
    db.insert(
        "INSERT INTO email_codes (email, code, purpose, expires_at, last_sent_at, ip, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (email, code, purpose, now + CODE_TTL_SECONDS, now, ip[:64], now),
    )

    ok, err = send_mail(
        email,
        "%s 验证码（%d 分钟内有效）" % (brand()["name"], CODE_TTL_SECONDS // 60),
        render_html(code),
    )
    if not ok:
        db.execute("DELETE FROM email_codes WHERE email = %s AND purpose = %s", (email, purpose))
        return False, "邮件发送失败：%s" % err
    return True, "验证码已发送至邮箱"


def normalize_code(code: str) -> str:
    return "".join((code or "").split()).upper()


def verify_code(email: str, code: str, purpose: str = "register") -> bool:
    """校验并消费，同一个码不能用两次。"""
    import hmac

    db = get_db()
    email, code = normalize_email(email), normalize_code(code)
    if not email or not code:
        return False

    row = db.query_one(
        "SELECT id, code, expires_at FROM email_codes WHERE email = %s AND purpose = %s "
        "ORDER BY id DESC LIMIT 1",
        (email, purpose),
    )
    if row is None:
        return False
    if not hmac.compare_digest(normalize_code(row["code"]).encode(), code.encode()):
        return False
    if int(row["expires_at"] or 0) < now_ts():
        db.execute("DELETE FROM email_codes WHERE id = %s", (row["id"],))
        return False

    db.execute("DELETE FROM email_codes WHERE id = %s", (row["id"],))
    return True


def test_smtp(to: str, cfg: dict | None = None) -> tuple[bool, str]:
    """安装向导里点"测试发信"用。"""
    ok, err = send_mail(
        to or (cfg or {}).get("user") or "",
        "%s 邮件连通性测试" % brand()["name"],
        render_html("888888", "收到这封邮件说明 SMTP 配置可用"),
        cfg,
    )
    if ok:
        return True, "测试邮件已发送，请查收（含垃圾箱）"
    return False, "发送失败：%s" % err


def last_code_at() -> int:
    return int(time.time())
