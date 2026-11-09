"""PushPlus 推送（同步版）。"""

from __future__ import annotations

import requests

from core.logger import get_logger

logger = get_logger("alipay.pushplus")


class PushPlusNotifier:
    def __init__(self, config: dict):
        self.config = config.get("pushplus", {}) or {}

    def enabled(self) -> bool:
        return bool(self.config.get("enabled") and self.config.get("token"))

    def send(self, title, content, template=None) -> bool:
        if not self.enabled():
            return False
        payload = {
            "token": self.config.get("token", ""),
            "title": title,
            "content": content,
            "template": template or self.config.get("template", "markdown"),
        }
        for key in ("channel", "topic"):
            val = self.config.get(key)
            if val:
                payload[key] = val
        try:
            resp = requests.post(
                self.config.get("endpoint", "http://www.pushplus.plus/send"),
                json=payload,
                timeout=10,
            )
            data = resp.json()
            if data.get("code") == 200:
                logger.info("PushPlus 通知已发送: %s", title)
                return True
            logger.warning("PushPlus 通知失败: %s", data)
            return False
        except Exception as e:
            logger.warning("PushPlus 请求异常: %s", e)
            return False

    def payment_success(self, order: dict, detected_by="payment_monitor") -> bool:
        prefix = self.config.get("title_prefix", "码支付")
        amount = order.get("payment_amount") or order.get("price") or "未知"
        content = (
            f"**订单号:** {order.get('out_trade_no', '')}\n"
            f"**金额:** {amount} 元\n"
            f"**商品:** {order.get('name', '')}\n"
            f"**检测方式:** {detected_by}\n"
            f"**时间:** {order.get('pay_time', '')}"
        )
        return self.send(f"{prefix} - 支付成功通知", content, "markdown")
