"""转账链接生成（含防风控多层 URL，移植自 PHP AlipayTransfer.php）。"""

from __future__ import annotations

import random
import string
from urllib.parse import quote, urlencode


class AlipayTransfer:
    def __init__(self, config: dict):
        self.config = config
        self.transfer_user_id = config.get("transfer_user_id", "")

    def generate_transfer_link(self, amount, memo, user_id=None):
        user_id = user_id or self.transfer_user_id
        if not user_id:
            raise ValueError("未配置转账收款账号（transfer_user_id）")

        anti_risk = self.config.get("anti_risk_url", {})
        if not anti_risk.get("enabled", True):
            return self._generate_simple_link(amount, memo, user_id)

        outer_app_id = anti_risk.get("outer_app_id", "20000218")
        inner_app_id = anti_risk.get("inner_app_id", "20000116")
        base_urls = anti_risk.get("base_urls", {})
        mdeduct_url = base_urls.get(
            "mdeduct_landing", "https://render.alipay.com/p/c/mdeduct-landing"
        )
        render_url = base_urls.get("render_scheme", "https://render.alipay.com/p/s/i")

        inner_params = {
            "appId": inner_app_id,
            "actionType": "toAccount",
            "goBack": "NO",
            "amount": f"{amount:.2f}",
            "userId": user_id,
            "memo": memo,
        }
        inner_url = f"alipays://platformapi/startapp?{urlencode(inner_params)}"
        fourth_layer = f"{render_url}?scheme={quote(inner_url)}"
        third_url = f"alipays://platformapi/startapp?{urlencode({'appId': outer_app_id, 'url': fourth_layer})}"
        second_layer = f"{render_url}?scheme={quote(third_url)}"
        return f"{mdeduct_url}?scheme={quote(second_layer)}"

    def _generate_simple_link(self, amount, memo, user_id):
        params = {
            "appId": "09999988",
            "actionType": "toAccount",
            "goBack": "NO",
            "amount": f"{amount:.2f}",
            "userId": user_id,
            "memo": memo,
        }
        return f"alipays://platformapi/startapp?{urlencode(params)}"

    def create_order(self, order_no, amount, product_name=""):
        if amount <= 0:
            raise ValueError("支付金额必须大于 0")
        if not order_no:
            raise ValueError("订单号不能为空")
        return self.generate_transfer_link(amount, order_no)

    def generate_order_no(self):
        chars = string.ascii_uppercase + string.digits
        return "".join(random.choices(chars, k=12))
