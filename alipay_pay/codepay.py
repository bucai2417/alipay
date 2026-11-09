"""码支付订单业务：创建、查询、签名、通知。

支持两种真实收款方式：
1. 转账链接模式（默认）：生成带备注的支付宝转账链接二维码，账单按备注匹配；
2. 经营码模式：固定收款码 + 每单唯一金额，账单按金额匹配。
"""

from __future__ import annotations

import os
import threading
from datetime import datetime

from core.logger import get_logger
from core.settings import load_alipay_config
from config import BASE_DIR

from .orders import OrderStore, gen_trade_no
from .protocol import generate_sign, validate_sign
from .qr import generate_qr_base64
from .transfer import AlipayTransfer

logger = get_logger("alipay.codepay")

# 经营码唯一金额分配 + 落库必须原子，避免并发下单拿到相同金额
_amount_lock = threading.Lock()


def resolve_qr_path(qr_path: str) -> str:
    """把配置中的二维码相对路径解析为可读取的绝对路径。"""
    if not qr_path:
        return ""
    if os.path.isabs(qr_path):
        return qr_path
    for base in (BASE_DIR, os.getcwd()):
        candidate = os.path.abspath(os.path.join(base, qr_path))
        if os.path.exists(candidate):
            return candidate
    return os.path.abspath(os.path.join(BASE_DIR, qr_path))


class CodePay:
    def __init__(self, config: dict | None = None, store: OrderStore | None = None):
        self.config = config or load_alipay_config()
        self.store = store or OrderStore()
        self.merchant_id = str(self.config.get("merchant_id") or "")
        self.merchant_key = str(self.config.get("merchant_key") or "")
        self.transfer = AlipayTransfer(self.config)

    # ---------- 商户与订单查询 ----------

    def query_merchant(self, pid, key):
        if pid != self.merchant_id or key != self.merchant_key:
            return {"code": -1, "msg": "商户凭证无效"}
        return {
            "code": 1,
            "pid": int(self.merchant_id) if self.merchant_id.isdigit() else self.merchant_id,
            "key": self.merchant_key,
            "active": 1,
            "money": "0.00",
            "account": self.config.get("transfer_user_id", ""),
            "username": "Merchant",
            "rate": "96",
        }

    def query_order(self, pid, key, out_trade_no, validate_key=True):
        if validate_key and (pid != self.merchant_id or key != self.merchant_key):
            return {"code": -1, "msg": "商户凭证无效"}
        order = self.store.get_order(out_trade_no=out_trade_no, pid=pid or None)
        if not order:
            return {"code": -1, "msg": "订单不存在"}
        return self._order_view(order)

    def query_orders(self, pid, key, limit=20):
        if pid != self.merchant_id or key != self.merchant_key:
            return {"code": -1, "msg": "商户凭证无效"}
        return [self._order_view(o, full=False) for o in self.store.get_orders(pid, limit)]

    def _order_view(self, order: dict, full: bool = True) -> dict:
        view = {
            "code": 1,
            "msg": "SUCCESS",
            "trade_no": order["id"],
            "out_trade_no": order["out_trade_no"],
            "type": order["type"],
            "pid": order["pid"],
            "addtime": order["add_time"],
            "endtime": order["pay_time"],
            "name": order["name"],
            "money": f"{float(order['price']):.2f}",
            "status": order["status"],
        }
        if full:
            view["payment_amount"] = f"{float(order.get('payment_amount') or 0):.2f}"
        return view

    # ---------- 创建支付 ----------

    def create_merchant_payment(self, money, name, out_trade_no, notify_url="", source="shop"):
        """站内（商城）创建支付：跳过对外商户签名校验。"""
        params = {
            "pid": self.merchant_id or "internal",
            "type": "alipay",
            "out_trade_no": out_trade_no,
            "name": name,
            "money": f"{float(money):.2f}",
            "notify_url": notify_url or "",
            "return_url": "",
            "sitename": "",
            "sign": "",
            "sign_type": "MD5",
        }
        return self.create_payment(params, internal=True, source=source)

    def create_payment(self, params, internal=False, source="api"):
        try:
            required = ["pid", "type", "out_trade_no", "name", "money"]
            for key in required:
                if not params.get(key):
                    raise ValueError(f"缺少必要参数: {key}")

            if not internal:
                if not self.merchant_id or not self.merchant_key:
                    raise ValueError("商户 ID/密钥未配置，请先在后台生成并保存商户配置")
                if params["pid"] != self.merchant_id:
                    raise ValueError("商户 ID 不匹配")
                if not validate_sign(params, self.merchant_key):
                    raise ValueError("签名校验失败")
            if params.get("type") != "alipay":
                raise ValueError("仅支持 alipay 支付类型")

            original_amount = round(float(params["money"]), 2)
            if original_amount <= 0:
                raise ValueError("支付金额必须大于 0")

            qr_cfg = self.config.get("business_qr_mode", {}) or {}
            business_qr_mode = bool(qr_cfg.get("enabled"))
            if business_qr_mode:
                qr_path = qr_cfg.get("qr_code_path") or ""
                if not qr_path or not os.path.exists(resolve_qr_path(qr_path)):
                    raise ValueError("经营码模式已开启，但收款二维码未上传，请先在后台支付配置中上传")
            elif not str(self.config.get("transfer_user_id") or "").strip():
                raise ValueError("未配置转账收款账号 userId，请在后台支付配置中填写")

            def _insert_order(trade_no, amount):
                self.store.insert_order(
                    order_id=trade_no,
                    out_trade_no=params["out_trade_no"],
                    order_type=params["type"],
                    pid=params["pid"],
                    name=params["name"],
                    price=original_amount,
                    payment_amount=amount,
                    status=0,
                    notify_url=params.get("notify_url", ""),
                    return_url=params.get("return_url", ""),
                    sitename=params.get("sitename", ""),
                    source=source,
                )

            payment_amount = original_amount
            if business_qr_mode:
                offset = float(qr_cfg.get("amount_offset") or 0.01)
                timeout = int(self.config.get("order_timeout") or 300)
                # 并发下单时保证金额唯一且订单落库在同一个临界区
                with _amount_lock:
                    payment_amount = self.store.allocate_unique_amount(
                        original_amount, offset, timeout
                    )
                    if payment_amount != original_amount:
                        logger.info("金额已调整为 %s（原 %s）: %s",
                                    payment_amount, original_amount, params["out_trade_no"])
                    trade_no = gen_trade_no()
                    _insert_order(trade_no, payment_amount)
            else:
                trade_no = gen_trade_no()
                _insert_order(trade_no, payment_amount)

            response = {
                "code": 1,
                "msg": "SUCCESS",
                "pid": params["pid"],
                "trade_no": trade_no,
                "out_trade_no": params["out_trade_no"],
                "money": f"{original_amount:.2f}",
                "payment_amount": f"{payment_amount:.2f}",
                "business_qr_mode": business_qr_mode,
            }

            if business_qr_mode:
                response.update({
                    "payment_url": "经营码收款模式",
                    "qr_code_url": "/api/codepay/qrcode",
                    "payment_instruction": f"请使用支付宝扫描二维码，支付金额：{payment_amount:.2f} 元",
                })
                qr_path = qr_cfg.get("qr_code_path") or ""
                full = resolve_qr_path(qr_path)
                if qr_path and os.path.exists(full):
                    import base64
                    try:
                        with open(full, "rb") as f:
                            response["qr_code"] = base64.b64encode(f.read()).decode("utf-8")
                    except Exception as e:
                        logger.warning("读取经营码二维码失败: %s", e)
                if payment_amount != original_amount:
                    response["amount_adjusted"] = True
                    response["adjustment_note"] = (
                        f"检测到相同金额订单，实际支付金额已调整为 {payment_amount:.2f} 元"
                    )
            else:
                payment_url = self.transfer.create_order(
                    params["out_trade_no"], payment_amount, params["name"]
                )
                response["payment_url"] = payment_url
                response["qr_code"] = generate_qr_base64(payment_url)

            logger.info("支付订单已创建: %s（%s 元）", trade_no, payment_amount)
            return response

        except Exception as e:
            logger.warning("创建支付失败: %s", e)
            return {"code": -1, "msg": str(e)}

    # ---------- 支付通知 ----------

    def process_notification(self, params):
        try:
            for key in ("out_trade_no", "trade_no", "trade_status", "name", "money"):
                if key not in params:
                    raise ValueError(f"缺少必要参数: {key}")
            if not validate_sign(params, self.merchant_key):
                raise ValueError("签名校验失败")

            order = self.store.get_order(out_trade_no=params["out_trade_no"])
            if not order:
                raise Exception(f"订单不存在: {params['out_trade_no']}")
            if order["status"] == 1:
                return {"code": 1, "msg": "SUCCESS"}
            if params.get("trade_status") == "TRADE_SUCCESS":
                if self.store.mark_paid(order["id"]):
                    logger.info("订单已通过回调标记为已支付: %s", params["out_trade_no"])
            return {"code": 1, "msg": "SUCCESS"}
        except Exception as e:
            logger.warning("处理支付通知失败: %s", e)
            return {"code": -1, "msg": str(e)}

    def send_notification(self, order: dict) -> dict:
        """向商户 notify_url 发送支付成功通知。

        返回 {"ok": bool, "skipped": bool?, "error": str?, "attempts": int?}。
        失败会自动登记重试（由监控线程按退避策略重发）。
        """
        url = str(order.get("notify_url") or "").strip()
        if not url:
            self.store.mark_notify_skipped(order["id"])
            return {"ok": True, "skipped": True}

        notify_data = {
            "pid": order["pid"],
            "trade_no": order["id"],
            "out_trade_no": order["out_trade_no"],
            "type": order["type"],
            "name": order["name"],
            "money": f"{float(order['price']):.2f}",
            "trade_status": "TRADE_SUCCESS",
        }
        notify_data["sign"] = generate_sign(notify_data, self.merchant_key)
        notify_data["sign_type"] = "MD5"

        error = ""
        try:
            import requests

            resp = requests.post(
                url,
                data=notify_data,
                timeout=5,
                headers={"User-Agent": "CodePay/1.0"},
            )
            if (resp.text or "").strip().lower() == "success":
                self.store.mark_notify_done(order["id"])
                logger.info("商户通知成功: %s", order["out_trade_no"])
                return {"ok": True}
            error = f"HTTP {resp.status_code}: {(resp.text or '')[:120]}"
        except Exception as e:
            error = str(e)[:200]

        attempts = self.store.schedule_notify_retry(order["id"], error)
        logger.warning("商户通知失败 %s: %s（第 %s 次）",
                       order["out_trade_no"], error, attempts)
        return {"ok": False, "error": error, "attempts": attempts}
