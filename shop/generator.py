"""卡密自动生成规则。"""

import secrets
import string

CHARSETS = {
    "hex": "0123456789ABCDEF",
    "digits": string.digits,
    "upper": string.ascii_uppercase + string.digits,
    "alnum": string.ascii_letters + string.digits,
}


def generate_codes(config: dict, count: int = 1) -> list:
    """按规则生成卡密。

    config 示例：{"prefix": "VIP", "length": 16, "charset": "hex", "quantity": 1}
    count 为本次需要生成的总条数（quantity 由调用方相乘）。
    """
    if count <= 0:
        return []
    prefix = str(config.get("prefix") or "")
    length = max(4, min(int(config.get("length") or 16), 64))
    charset = CHARSETS.get(str(config.get("charset") or "hex").lower(), CHARSETS["hex"])

    codes, seen = [], set()
    attempts, max_attempts = 0, max(count * 20, 100)
    while len(codes) < count and attempts < max_attempts:
        attempts += 1
        body = "".join(secrets.choice(charset) for _ in range(length))
        code = f"{prefix}{body}" if prefix else body
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
    if len(codes) < count:
        raise RuntimeError(f"卡密生成不足：需要 {count} 条，仅生成 {len(codes)} 条")
    return codes
