"""CodePay 协议签名（MD5）。

规则：过滤空值，按 key 升序拼接 ``k=v&k=v``，末尾追加商户密钥后取 MD5。

``sign``、``sign_type``、``act``、``action``、``format`` 不参与验签，
与主流码支付网关的习惯保持一致，便于外部系统直接对接。
"""

import hashlib

IGNORED_KEYS = {"sign", "sign_type", "act", "action", "format"}


def generate_sign_string(params: dict, ignored=IGNORED_KEYS) -> str:
    filtered = {
        k: v for k, v in params.items()
        if k not in ignored and v is not None and str(v) != ""
    }
    sorted_keys = sorted(filtered.keys())
    return "&".join(f"{k}={filtered[k]}" for k in sorted_keys)


def generate_sign(params: dict, merchant_key: str, ignored=IGNORED_KEYS) -> str:
    sign_str = generate_sign_string(params, ignored)
    return hashlib.md5((sign_str + merchant_key).encode("utf-8")).hexdigest()


def validate_sign(params: dict, merchant_key: str) -> bool:
    if "sign" not in params:
        return False
    check = {k: v for k, v in params.items() if k != "sign"}
    return params["sign"] == generate_sign(check, merchant_key)
