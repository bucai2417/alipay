"""敏感配置字段的本地加密存储。

密文带 ``ENC2:`` 前缀，密钥持久化在 ``data/.secret_key``，也可通过
环境变量 ``ALIPAY_ENCRYPT_KEY`` 指定（Docker / 多机迁移时保持同一密钥）。
未加密的旧明文值读取时原样返回，便于平滑升级。
"""

from __future__ import annotations

import base64
import hashlib
import os

from config import DATA_DIR

ENC_PREFIX = "ENC2:"
_key_cache: bytes | None = None


def _key_path() -> str:
    return os.path.join(DATA_DIR, ".secret_key")


def _load_key() -> bytes:
    global _key_cache
    if _key_cache is not None:
        return _key_cache

    raw = os.getenv("ALIPAY_ENCRYPT_KEY", "").strip()
    if raw:
        _key_cache = base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())
        return _key_cache

    path = _key_path()
    try:
        if os.path.isfile(path):
            with open(path, "rb") as f:
                data = f.read().strip()
            if data:
                _key_cache = data
                return data
    except Exception:
        pass

    from cryptography.fernet import Fernet

    data = Fernet.generate_key()
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception:
        pass
    _key_cache = data
    return data


def _fernet():
    from cryptography.fernet import Fernet

    return Fernet(_load_key())


def encrypt_value(value: str) -> str:
    """加密字符串；空值与非字符串原样返回。"""
    if not isinstance(value, str) or not value:
        return value
    if is_encrypted(value):
        return value
    try:
        token = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
        return ENC_PREFIX + token
    except Exception:
        # 加密不可用时退回明文，避免配置无法保存
        return value


def decrypt_value(value: str) -> str:
    """解密字符串；失败时返回空串，避免把乱码当密钥使用。"""
    if not isinstance(value, str) or not value:
        return value if isinstance(value, str) else ""
    if not is_encrypted(value):
        return value
    try:
        plain = _fernet().decrypt(value[len(ENC_PREFIX):].encode("ascii"))
        return plain.decode("utf-8")
    except Exception:
        return ""


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(ENC_PREFIX)
