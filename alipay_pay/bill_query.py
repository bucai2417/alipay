"""支付宝账单查询：alipay.data.bill.accountlog.query。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from core.logger import get_logger

logger = get_logger("alipay.bill")


class BillQuery:
    def __init__(self, alipay_client):
        self.client = alipay_client

    def query_bills(self, start_time, end_time, page_no=1, page_size=2000):
        if not self.client.validate_config():
            raise Exception("支付宝配置不完整")

        from alipay.aop.api.domain.AlipayDataBillAccountlogQueryModel import (
            AlipayDataBillAccountlogQueryModel,
        )
        from alipay.aop.api.request.AlipayDataBillAccountlogQueryRequest import (
            AlipayDataBillAccountlogQueryRequest,
        )

        model = AlipayDataBillAccountlogQueryModel()
        model.start_time = start_time
        model.end_time = end_time
        model.page_no = str(page_no)
        model.page_size = str(min(page_size, 2000))

        response = self.client.execute(
            AlipayDataBillAccountlogQueryRequest(biz_model=model)
        )

        if isinstance(response, str):
            try:
                resp_data = json.loads(response)
            except (json.JSONDecodeError, TypeError):
                resp_data = {}
            if resp_data.get("code") == "10000":
                return {
                    "success": True,
                    "data": resp_data,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            raise Exception(f"支付宝接口返回错误: {response}")

        if response is not None and getattr(response, "code", None) == "10000":
            return {
                "success": True,
                "data": self._parse_response(response),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

        if response is None:
            raise Exception("支付宝接口返回空响应：请检查 AppId / 密钥是否匹配、应用是否已上线")

        code = getattr(response, "code", None) or "N/A"
        sub_code = getattr(response, "sub_code", "") or ""
        msg = getattr(response, "sub_msg", None) or getattr(response, "msg", "") or ""
        raise Exception(f"支付宝接口错误: code={code}, sub_code={sub_code}, msg={msg}")

    def _parse_response(self, response):
        if hasattr(response, "__dict__"):
            result = {}
            for key, value in response.__dict__.items():
                if key.startswith("_"):
                    continue
                if hasattr(value, "__dict__"):
                    result[key] = self._parse_response(value)
                elif isinstance(value, list):
                    result[key] = [
                        self._parse_response(item) if hasattr(item, "__dict__") else item
                        for item in value
                    ]
                else:
                    result[key] = value
            return result
        return response

    def query_recent_bills(self, minutes_back=30):
        end_time = datetime.now()
        start_time = end_time - timedelta(minutes=minutes_back)
        return self.query_bills(
            start_time.strftime("%Y-%m-%d %H:%M:%S"),
            end_time.strftime("%Y-%m-%d %H:%M:%S"),
        )
