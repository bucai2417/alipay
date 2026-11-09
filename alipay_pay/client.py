"""支付宝 SDK 客户端封装（alipay-sdk-python）。

保留原实现中的关键兼容处理：
- 应用私钥支持 PKCS#1 / PKCS#8 / 纯 base64 自动归一；
- 猴补丁 SDK 签名函数，使 PKCS#8 私钥也可签名；
- 给出可操作的中文错误提示。
"""

from __future__ import annotations

from core.logger import get_logger

logger = get_logger("alipay.client")


class AlipayClient:
    _patched = False
    _normalize_logged = False

    def __init__(self, config: dict):
        self.config = config
        self._client = None
        if not AlipayClient._patched:
            self._patch_sdk_signing()
            AlipayClient._patched = True

    # ---------- 私钥/公钥归一 ----------

    @staticmethod
    def _clean_key_text(key: str) -> str:
        if key is None:
            return ""
        key = str(key).strip()
        if not key:
            return ""
        if key.startswith("ENC:"):
            raise ValueError(
                "应用私钥仍是加密密文（解密失败）。请检查 data/.secret_key 是否随数据库迁移，"
                "并在「支付配置」中重新保存应用私钥。"
            )
        key = key.replace("\r\n", "\n").replace("\r", "\n").replace("\\n", "\n")
        if (key.startswith('"') and key.endswith('"')) or (
            key.startswith("'") and key.endswith("'")
        ):
            key = key[1:-1].strip()
        lines = [ln.strip() for ln in key.split("\n") if ln.strip()]
        return "\n".join(lines)

    @staticmethod
    def _strip_pem_headers(key: str) -> str:
        parts = []
        for ln in key.split("\n"):
            s = ln.strip()
            if not s or s.startswith("-----"):
                continue
            parts.append(s)
        return "".join(parts)

    @staticmethod
    def _to_pem(body_b64: str, begin: str, end: str) -> str:
        body = "".join((body_b64 or "").split())
        lines = [body[i:i + 64] for i in range(0, len(body), 64)]
        return begin + "\n" + "\n".join(lines) + "\n" + end

    @staticmethod
    def _pkcs8_to_pkcs1(pem_text: str) -> str:
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.serialization import load_pem_private_key

            pkey = load_pem_private_key(pem_text.encode("utf-8"), password=None)
            return pkey.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode("utf-8")
        except ImportError:
            logger.warning("cryptography 未安装，无法转换 PKCS#8。请执行: pip install cryptography")
            return ""
        except Exception as e:
            logger.debug("PKCS#8 转换失败: %s", e)
            return ""

    @classmethod
    def _log_normalize_once(cls, msg: str) -> None:
        if not cls._normalize_logged:
            logger.info(msg)
            cls._normalize_logged = True
        else:
            logger.debug(msg)

    @classmethod
    def normalize_private_key(cls, key: str) -> str:
        """确保私钥为 PKCS#1（SDK RSA 签名要求）。"""
        key = cls._clean_key_text(key)
        if not key:
            return key

        if "BEGIN PUBLIC KEY" in key or "BEGIN RSA PUBLIC KEY" in key:
            raise ValueError("填写的是公钥，不是应用私钥。请粘贴支付宝开放平台的「应用私钥」。")

        if "BEGIN RSA PRIVATE KEY" in key:
            return key if key.endswith("\n") else key + "\n"

        if "BEGIN PRIVATE KEY" in key:
            converted = cls._pkcs8_to_pkcs1(key)
            if converted:
                cls._log_normalize_once("私钥已从 PKCS#8 自动转换为 PKCS#1 格式")
                return converted
            logger.warning("PKCS#8 私钥转换失败")
            return key

        body = cls._strip_pem_headers(key)
        if len(body) < 80:
            return key
        pkcs8_pem = cls._to_pem(
            body, "-----BEGIN PRIVATE KEY-----", "-----END PRIVATE KEY-----"
        )
        converted = cls._pkcs8_to_pkcs1(pkcs8_pem)
        if converted:
            cls._log_normalize_once("纯 base64 私钥已按 PKCS#8 解析并转为 PKCS#1")
            return converted
        return cls._to_pem(
            body, "-----BEGIN RSA PRIVATE KEY-----", "-----END RSA PRIVATE KEY-----"
        )

    @classmethod
    def normalize_public_key(cls, key: str) -> str:
        key = (key or "").strip()
        if not key:
            return key
        key = key.replace("\\n", "\n")
        if key.startswith("-----"):
            return key
        lines = [key[i:i + 64] for i in range(0, len(key), 64)]
        body = "\n".join(lines)
        return f"-----BEGIN PUBLIC KEY-----\n{body}\n-----END PUBLIC KEY-----"

    @staticmethod
    def _patch_sdk_signing():
        """让 SDK 支持 PKCS#8 私钥签名。"""
        try:
            import base64

            import rsa
            import alipay.aop.api.util.SignatureUtils as su

            def _load_pk(private_key_text: str):
                normalized = AlipayClient.normalize_private_key(private_key_text)
                try:
                    return rsa.PrivateKey.load_pkcs1(normalized, format="PEM")
                except Exception:
                    pass
                try:
                    return rsa.PrivateKey.load_pkcs1(normalized.encode("utf-8"), format="PEM")
                except Exception:
                    pass
                pkcs1 = AlipayClient._pkcs8_to_pkcs1(normalized)
                if not pkcs1:
                    body = AlipayClient._strip_pem_headers(normalized)
                    if body:
                        pkcs1 = AlipayClient._pkcs8_to_pkcs1(
                            AlipayClient._to_pem(
                                body, "-----BEGIN PRIVATE KEY-----", "-----END PRIVATE KEY-----"
                            )
                        )
                if pkcs1:
                    return rsa.PrivateKey.load_pkcs1(pkcs1, format="PEM")
                raise Exception("无法解析应用私钥，请在支付配置中重新粘贴并保存私钥。")

            def _sign_with_rsa2_compat(private_key, sign_content, charset):
                from alipay.aop.api.constant.CommonConstants import PYTHON_VERSION_3

                if PYTHON_VERSION_3 and isinstance(sign_content, str):
                    sign_content = sign_content.encode(charset)
                try:
                    pk = _load_pk(private_key)
                except ValueError as ve:
                    raise Exception(str(ve))
                except Exception as e2:
                    raise Exception(f"密钥格式错误: {e2}")
                signature = rsa.sign(sign_content, pk, "SHA-256")
                sign = base64.b64encode(signature)
                if PYTHON_VERSION_3:
                    sign = str(sign, encoding=charset)
                return sign

            su.sign_with_rsa2 = _sign_with_rsa2_compat
            su.sign_with_rsa = _sign_with_rsa2_compat
            logger.info("SDK 签名函数已补丁，支持 PKCS#8 私钥")
        except Exception as e:
            logger.warning("SDK 签名补丁失败: %s", e)

    # ---------- 客户端 ----------

    def _get_client(self):
        if self._client is not None:
            return self._client

        from alipay.aop.api.AlipayClientConfig import AlipayClientConfig
        from alipay.aop.api.DefaultAlipayClient import DefaultAlipayClient

        private_key = str(self.config.get("private_key", "")).strip()
        public_key = str(self.config.get("alipay_public_key", "")).strip()

        if not private_key or "****" in private_key or len(private_key) < 20:
            raise ValueError("应用私钥未配置或无效，请在支付配置中填写正确的私钥")
        if not public_key or "****" in public_key or len(public_key) < 20:
            raise ValueError("支付宝公钥未配置或无效，请在支付配置中填写正确的公钥")

        client_config = AlipayClientConfig()
        client_config.server_url = self.config.get(
            "server_url", "https://openapi.alipay.com/gateway.do"
        )
        client_config.app_id = str(self.config.get("app_id", "")).strip()
        client_config.app_private_key = self.normalize_private_key(private_key)
        client_config.alipay_public_key = self.normalize_public_key(public_key)
        client_config.sign_type = self.config.get("sign_type", "RSA2")
        client_config.charset = self.config.get("charset", "UTF-8")

        self._client = DefaultAlipayClient(alipay_client_config=client_config)
        logger.debug("支付宝客户端已初始化 (app_id=%s)", client_config.app_id)
        return self._client

    def validate_config(self) -> bool:
        for key in ("app_id", "private_key", "alipay_public_key"):
            if not self.config.get(key):
                logger.error("缺少必要配置: %s", key)
                return False
        return True

    def execute(self, api_model):
        return self._get_client().execute(api_model)
