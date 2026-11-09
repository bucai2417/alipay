"""配置读写：支付宝支付配置、商城设置、后台账号。

支付配置整体以 JSON 存于 settings 表；敏感字段（私钥、商户密钥、推送 token）
在落库前用 :mod:`core.secrets` 加密，读取时透明解密。
"""

from __future__ import annotations

import copy
import os
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

from config import DATA_DIR
from core.logger import get_logger
from core.secrets import decrypt_value, encrypt_value, is_encrypted
from core.storage import db

logger = get_logger("settings")

CONFIG_KEY = "alipay_config"
SHOP_KEY = "shop_settings"
ADMIN_KEY = "admin_auth"
EMAIL_KEY = "email_config"

SENSITIVE_FIELDS = ("private_key", "alipay_public_key", "merchant_key", "pushplus_token")

EMAIL_PRESETS = {
    "qq": {"host": "smtp.qq.com", "port": 465, "security": "ssl",
           "note": "QQ 邮箱需开启 SMTP 并使用授权码"},
    "163": {"host": "smtp.163.com", "port": 465, "security": "ssl",
            "note": "163 邮箱使用客户端授权码"},
    "126": {"host": "smtp.126.com", "port": 465, "security": "ssl",
            "note": "126 邮箱使用客户端授权码"},
    "aliyun": {"host": "smtp.aliyun.com", "port": 465, "security": "ssl",
               "note": "阿里云邮箱使用授权码"},
    "exmail": {"host": "smtp.exmail.qq.com", "port": 465, "security": "ssl",
               "note": "腾讯企业邮，使用登录密码或客户端专用密码"},
    "gmail": {"host": "smtp.gmail.com", "port": 465, "security": "ssl",
              "note": "Gmail 需开启两步验证并使用应用专用密码"},
    "outlook": {"host": "smtp-mail.outlook.com", "port": 587, "security": "starttls",
                "note": "Outlook / Microsoft 365"},
    "custom": {"host": "", "port": 465, "security": "ssl", "note": "自定义 SMTP 服务器"},
}

DEFAULT_EMAIL_CONFIG = {
    "enabled": False,
    "preset": "qq",
    "host": "smtp.qq.com",
    "port": 465,
    "security": "ssl",
    "username": "",
    "password": "",
    "from_name": "卡密商城",
    "from_email": "",
}

DEFAULT_ALIPAY_CONFIG = {
    "app_id": "",
    "private_key": "",
    "alipay_public_key": "",
    "transfer_user_id": "",
    "merchant_id": "",
    "merchant_key": "",
    "server_url": "https://openapi.alipay.com/gateway.do",
    "sign_type": "RSA2",
    "charset": "UTF-8",
    "business_qr_mode": {
        "enabled": False,
        "qr_code_path": "data/qrcode/business_qr.png",
        "amount_offset": 0.01,
        "payment_timeout": 300,
        "match_tolerance": 300,
    },
    "anti_risk_url": {
        "enabled": True,
        "outer_app_id": "20000218",
        "inner_app_id": "20000116",
        "base_urls": {
            "mdeduct_landing": "https://render.alipay.com/p/c/mdeduct-landing",
            "render_scheme": "https://render.alipay.com/p/s/i",
        },
    },
    "order_timeout": 300,
    "monitor_interval": 10,
    "query_minutes_back": 30,
    "pushplus": {
        "enabled": False,
        "token": "",
        "template": "markdown",
        "channel": "",
        "topic": "",
        "title_prefix": "码支付",
        "endpoint": "http://www.pushplus.plus/send",
    },
}

DEFAULT_SHOP_SETTINGS = {
    "enabled": True,
    "public_shop_enabled": True,
    "shop_title": "卡密商城",
    "shop_notice": "",
    "max_qty_per_order": 50,
    "notify_admin_pushplus": True,
}


def _merge(base: dict, patch: dict) -> dict:
    """递归合并字典（仅合并 patch 中出现的键）。"""
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _bool(v, default=False) -> bool:
    if v is None:
        return bool(default)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _int(v, default, low=None, high=None) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return int(default)
    if low is not None and n < low:
        n = low
    if high is not None and n > high:
        n = high
    return n


def _get_raw(key: str, default: dict) -> dict:
    raw = db.settings_get(key, None)
    if not isinstance(raw, dict):
        return copy.deepcopy(default)
    return _merge(default, raw)


def load_alipay_config() -> dict:
    cfg = _get_raw(CONFIG_KEY, DEFAULT_ALIPAY_CONFIG)
    for field in SENSITIVE_FIELDS:
        val = cfg.get(field) or ""
        if is_encrypted(val):
            plain = decrypt_value(val)
            if not plain:
                logger.error("敏感字段 %s 解密失败：请检查 data/.secret_key 是否随数据库一起迁移", field)
            cfg[field] = plain
    # 规范化
    cfg.pop("sandbox_mode", None)
    cfg["order_timeout"] = _int(cfg.get("order_timeout"), 300, 60, 3600)
    cfg["monitor_interval"] = _int(cfg.get("monitor_interval"), 10, 3, 600)
    cfg["query_minutes_back"] = _int(cfg.get("query_minutes_back"), 30, 1, 1440)
    qr = cfg.get("business_qr_mode") or {}
    qr["enabled"] = _bool(qr.get("enabled"), False)
    qr["amount_offset"] = float(qr.get("amount_offset") or 0.01)
    qr["match_tolerance"] = _int(qr.get("match_tolerance"), 300, 10, 3600)
    cfg["business_qr_mode"] = qr
    pp = cfg.get("pushplus") or {}
    pp["enabled"] = _bool(pp.get("enabled"), False)
    cfg["pushplus"] = pp
    return cfg


def save_alipay_config(patch: dict) -> dict:
    """保存配置；敏感字段为空串表示“保持原值不变”。"""
    current = _get_raw(CONFIG_KEY, DEFAULT_ALIPAY_CONFIG)
    allowed_scalars = {
        "app_id", "private_key", "alipay_public_key", "transfer_user_id",
        "merchant_id", "merchant_key", "server_url", "sign_type", "charset",
        "order_timeout", "monitor_interval", "query_minutes_back",
    }
    data = {}
    for k in allowed_scalars:
        if k in (patch or {}):
            data[k] = patch[k]

    for field in SENSITIVE_FIELDS:
        if field in data:
            val = data[field]
            if isinstance(val, str) and val.strip() == "":
                data.pop(field, None)
            else:
                data[field] = encrypt_value(str(val).strip())

    if isinstance(patch.get("business_qr_mode"), dict):
        data["business_qr_mode"] = patch["business_qr_mode"]
    if isinstance(patch.get("pushplus"), dict):
        pp = dict(patch["pushplus"])
        if "token" in pp:
            tok = pp["token"]
            if isinstance(tok, str) and tok.strip() == "":
                pp.pop("token")
            else:
                pp["token"] = encrypt_value(str(tok).strip())
        data["pushplus"] = pp
    if isinstance(patch.get("anti_risk_url"), dict):
        data["anti_risk_url"] = patch["anti_risk_url"]

    merged = _merge(current, data)
    merged.pop("sandbox_mode", None)
    # 规范化后落库
    merged["order_timeout"] = _int(merged.get("order_timeout"), 300, 60, 3600)
    merged["monitor_interval"] = _int(merged.get("monitor_interval"), 10, 3, 600)
    merged["query_minutes_back"] = _int(merged.get("query_minutes_back"), 30, 1, 1440)
    qr = merged.get("business_qr_mode") or {}
    qr["enabled"] = _bool(qr.get("enabled"), False)
    try:
        qr["amount_offset"] = round(float(qr.get("amount_offset") or 0.01), 2)
    except (TypeError, ValueError):
        qr["amount_offset"] = 0.01
    qr["match_tolerance"] = _int(qr.get("match_tolerance"), 300, 10, 3600)
    merged["business_qr_mode"] = qr
    merged["pushplus"] = merged.get("pushplus") or {}
    merged["pushplus"]["enabled"] = _bool(merged["pushplus"].get("enabled"), False)

    db.settings_set(CONFIG_KEY, merged)
    logger.info("支付配置已更新（经营码=%s）", qr.get("enabled"))
    return load_alipay_config()


def is_configured(cfg: dict) -> bool:
    return bool(
        cfg.get("app_id")
        and cfg.get("private_key")
        and cfg.get("alipay_public_key")
    )


def payment_readiness(cfg: dict) -> tuple[bool, str]:
    """检查是否具备真实收款条件，返回 (是否就绪, 原因)。"""
    if not is_configured(cfg):
        return False, "请先完成支付宝应用配置（AppId / 应用私钥 / 支付宝公钥）"
    qr = cfg.get("business_qr_mode") or {}
    if qr.get("enabled"):
        path = qr.get("qr_code_path") or ""
        if not path:
            return False, "经营码模式已开启，请先上传收款二维码"
        from alipay_pay.codepay import resolve_qr_path

        if not os.path.exists(resolve_qr_path(path)):
            return False, "经营码二维码文件不存在，请重新上传"
    elif not cfg.get("transfer_user_id"):
        return False, "未配置转账收款账号 userId（转账链接模式必填）"
    return True, ""


def is_payment_ready(cfg: dict) -> bool:
    return payment_readiness(cfg)[0]


# ---------- 商城设置 ----------

def load_shop_settings() -> dict:
    s = _get_raw(SHOP_KEY, DEFAULT_SHOP_SETTINGS)
    for k in ("enabled", "public_shop_enabled", "notify_admin_pushplus"):
        s[k] = _bool(s.get(k), DEFAULT_SHOP_SETTINGS[k])
    s["max_qty_per_order"] = _int(s.get("max_qty_per_order"), 50, 1, 100)
    s["shop_title"] = str(s.get("shop_title") or "卡密商城")[:64]
    s["shop_notice"] = str(s.get("shop_notice") or "")[:500]
    return s


def save_shop_settings(patch: dict) -> dict:
    current = load_shop_settings()
    data = dict(current)
    for k in DEFAULT_SHOP_SETTINGS:
        if k in (patch or {}):
            data[k] = patch[k]
    for k in ("enabled", "public_shop_enabled", "notify_admin_pushplus"):
        data[k] = _bool(data.get(k), current.get(k, True))
    data["max_qty_per_order"] = _int(data.get("max_qty_per_order"), 50, 1, 100)
    data["shop_title"] = str(data.get("shop_title") or "卡密商城")[:64]
    data["shop_notice"] = str(data.get("shop_notice") or "")[:500]
    db.settings_set(SHOP_KEY, data)
    return load_shop_settings()


# ---------- 邮件通知 ----------

def load_email_config() -> dict:
    cfg = _get_raw(EMAIL_KEY, DEFAULT_EMAIL_CONFIG)
    pw = cfg.get("password") or ""
    if is_encrypted(pw):
        cfg["password"] = decrypt_value(pw)
    cfg["enabled"] = _bool(cfg.get("enabled"), False)
    cfg["port"] = _int(cfg.get("port"), 465, 1, 65535)
    sec = str(cfg.get("security") or "ssl").lower()
    cfg["security"] = sec if sec in ("ssl", "starttls", "none") else "ssl"
    cfg["preset"] = str(cfg.get("preset") or "custom")
    for k in ("host", "username", "from_name", "from_email"):
        cfg[k] = str(cfg.get(k) or "").strip()
    cfg["from_email"] = cfg["from_email"] or cfg["username"]
    return cfg


def save_email_config(patch: dict) -> dict:
    current = _get_raw(EMAIL_KEY, DEFAULT_EMAIL_CONFIG)
    data = {}
    for k in ("enabled", "preset", "host", "port", "security",
              "username", "from_name", "from_email"):
        if k in (patch or {}):
            data[k] = patch[k]
    if "password" in (patch or {}):
        pw = str(patch.get("password") or "")
        if pw.strip():  # 留空保持原密码
            data["password"] = encrypt_value(pw)

    merged = _merge(current, data)
    merged["enabled"] = _bool(merged.get("enabled"), False)
    merged["port"] = _int(merged.get("port"), 465, 1, 65535)
    sec = str(merged.get("security") or "ssl").lower()
    merged["security"] = sec if sec in ("ssl", "starttls", "none") else "ssl"
    merged["preset"] = str(merged.get("preset") or "custom")
    for k in ("host", "username", "from_name", "from_email"):
        merged[k] = str(merged.get(k) or "").strip()
    db.settings_set(EMAIL_KEY, merged)
    logger.info("邮件配置已更新（enabled=%s, host=%s）", merged.get("enabled"), merged.get("host"))
    return load_email_config()


# ---------- 后台账号 ----------
def _default_admin() -> dict:
    return {"username": "admin", "password_hash": generate_password_hash("admin123")}


def get_admin_auth() -> dict:
    raw = db.settings_get(ADMIN_KEY, None)
    if isinstance(raw, dict) and raw.get("username") and raw.get("password_hash"):
        return raw
    auth = _default_admin()
    db.settings_set(ADMIN_KEY, auth)
    return auth


def verify_admin(username: str, password: str) -> bool:
    auth = get_admin_auth()
    if str(username or "").strip() != auth.get("username"):
        return False
    try:
        return check_password_hash(auth.get("password_hash", ""), str(password or ""))
    except Exception:
        return False


def change_admin_password(old_password: str, new_password: str) -> tuple[bool, str]:
    auth = get_admin_auth()
    try:
        ok = check_password_hash(auth.get("password_hash", ""), str(old_password or ""))
    except Exception:
        ok = False
    if not ok:
        return False, "原密码不正确"
    if len(str(new_password or "")) < 6:
        return False, "新密码至少 6 位"
    auth["password_hash"] = generate_password_hash(str(new_password))
    db.settings_set(ADMIN_KEY, auth)
    return True, "密码已更新"


def ensure_business_qr_dir() -> str:
    path = os.path.join(DATA_DIR, "qrcode")
    os.makedirs(path, exist_ok=True)
    return path


def gen_merchant_credentials(app_id: str) -> dict:
    """按原系统算法生成商户 ID / 密钥。"""
    import hashlib
    import time

    pid = "".join(
        str(int(c, 16) % 10)
        for c in hashlib.md5(
            f"{app_id}{time.time()}{secrets.token_hex(8)}".encode()
        ).hexdigest()
    )[:16]
    return {"merchant_id": pid, "merchant_key": secrets.token_hex(16)}
