"""支付监控：定时查询支付宝账单并匹配待支付订单。

支付成功后的统一出口 :func:`mark_paid` 同时负责：
- 商城订单（``SHOP_`` 前缀）自动发货；
- 向商户 ``notify_url`` 发送回调；
- PushPlus 支付通知。

监控在后台线程中按配置间隔运行，配置修改后无需重启即刻生效。
"""

from __future__ import annotations

import threading
from datetime import datetime

from core.logger import get_logger
from core.settings import is_configured, load_alipay_config

from .bill_query import BillQuery
from .client import AlipayClient
from .codepay import CodePay
from .notify_pushplus import PushPlusNotifier
from .orders import OrderStore

logger = get_logger("alipay.monitor")

_status_lock = threading.Lock()
_status = {
    "last_run": None,
    "last_result": None,
    "running": False,
}


def monitoring_status() -> dict:
    with _status_lock:
        return dict(_status)


def _extract_bills(data):
    bills = []
    if not data:
        return bills
    detail_list = data.get("detail_list") or data.get("accountLogList") or []
    if isinstance(data, list):
        detail_list = data
    for bill in detail_list:
        direction = bill.get("direction", "")
        if direction and direction != "收入":
            continue
        bills.append({
            "tradeNo": bill.get("alipay_order_no") or bill.get("alipayOrderNo") or bill.get("tradeNo", ""),
            "amount": bill.get("trans_amount") or bill.get("amount", 0),
            "remark": bill.get("trans_memo") or bill.get("memo") or bill.get("remark", ""),
            "transDate": bill.get("trans_dt") or bill.get("transDate", ""),
        })
    return bills


def _shop_service():
    from shop.service import ShopService
    return ShopService()


def _after_paid(order: dict, detected_by: str) -> None:
    codepay = CodePay()
    out_trade_no = str(order.get("out_trade_no") or "")

    if out_trade_no.startswith("SHOP_"):
        try:
            result = _shop_service().try_fulfill_by_payment(out_trade_no)
            logger.info("商城自动发货 %s: %s %s", out_trade_no, result.get("code"), result.get("msg"))
        except Exception as e:
            logger.warning("商城自动发货失败 %s: %s", out_trade_no, e)

    try:
        codepay.send_notification(order)
    except Exception as e:
        logger.warning("商户回调发送失败 %s: %s", out_trade_no, e)

    try:
        PushPlusNotifier(codepay.config).payment_success(order, detected_by)
    except Exception as e:
        logger.warning("PushPlus 通知失败 %s: %s", out_trade_no, e)


def mark_paid(order_id: str, detected_by: str = "manual") -> dict:
    """幂等标记订单已支付并触发后续动作。"""
    store = OrderStore()
    changed = store.mark_paid(order_id)
    order = store.get_order(order_id=order_id)
    if changed and order:
        _after_paid(order, detected_by)
    return {"changed": changed, "order": order}


def _process_bills_memo_mode(bills) -> int:
    store = OrderStore()
    matched = 0
    for bill in bills:
        remark = str(bill.get("remark") or "").strip()
        if not remark:
            continue
        order = store.get_order(out_trade_no=remark)
        if not order or order["status"] != 0:
            continue
        if abs(float(order["price"]) - float(bill["amount"])) < 0.01:
            if store.mark_paid(order["id"]):
                matched += 1
                logger.info("订单 %s 已支付（备注匹配）", order["out_trade_no"])
                _after_paid(store.get_order(order_id=order["id"]) or order, "备注匹配")
    return matched


def _process_bills_amount_mode(bills, cfg) -> int:
    store = OrderStore()
    tolerance = int((cfg.get("business_qr_mode") or {}).get("match_tolerance") or 300)
    matched = 0
    for bill in bills:
        try:
            amount = float(bill["amount"])
        except (TypeError, ValueError):
            continue
        order = store.find_order_by_amount(amount)
        if not order:
            continue
        try:
            order_time = datetime.strptime(order["add_time"], "%Y-%m-%d %H:%M:%S")
            bill_time = (
                datetime.strptime(bill["transDate"], "%Y-%m-%d %H:%M:%S")
                if bill.get("transDate") else datetime.now()
            )
        except ValueError:
            continue
        if bill_time < order_time or (bill_time - order_time).total_seconds() > tolerance:
            continue
        if store.mark_paid(order["id"]):
            matched += 1
            logger.info("订单 %s 已支付（金额匹配）", order["out_trade_no"])
            _after_paid(store.get_order(order_id=order["id"]) or order, "金额匹配")
        break  # 一笔支付只匹配一笔订单
    return matched


def run_monitoring_cycle(cleanup: bool = True) -> dict:
    cfg = load_alipay_config()
    start = datetime.now()

    if cleanup:
        expired = OrderStore().expire_stale(int(cfg.get("order_timeout") or 300))
        if expired:
            logger.info("已过期订单 %s 笔", len(expired))
            try:
                _shop_service().expire_pending_by_payment([o["out_trade_no"] for o in expired])
            except Exception as e:
                logger.warning("同步商城过期状态失败: %s", e)

    if not is_configured(cfg):
        return {"ok": False, "skipped": "not_configured", "matched": 0, "bills": 0}

    try:
        client = AlipayClient(cfg)
        result = BillQuery(client).query_recent_bills(int(cfg.get("query_minutes_back") or 30))
    except Exception as e:
        logger.warning("账单查询失败: %s", e)
        return {"ok": False, "error": str(e), "matched": 0, "bills": 0}

    if not result.get("success"):
        return {"ok": False, "error": "账单查询未成功", "matched": 0, "bills": 0}

    data = result.get("data") or {}
    if isinstance(data, dict) and str(data.get("total_size", "-1")) == "0":
        return {"ok": True, "matched": 0, "bills": 0}

    bills = _extract_bills(data)
    if not bills:
        return {"ok": True, "matched": 0, "bills": 0}

    business_qr_mode = bool((cfg.get("business_qr_mode") or {}).get("enabled"))
    if business_qr_mode:
        matched = _process_bills_amount_mode(bills, cfg)
    else:
        matched = _process_bills_memo_mode(bills)

    elapsed = (datetime.now() - start).total_seconds()
    return {"ok": True, "bills": len(bills), "matched": matched, "elapsed": elapsed}


class MonitorThread(threading.Thread):
    """按配置间隔轮询的后台线程。"""

    def __init__(self):
        super().__init__(name="payment-monitor", daemon=True)
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        logger.info("支付监控线程已启动")
        while not self._stop.is_set():
            try:
                cfg = load_alipay_config()
                interval = int(cfg.get("monitor_interval") or 10)
                with _status_lock:
                    _status["running"] = True
                result = run_monitoring_cycle(cleanup=True)
                with _status_lock:
                    _status["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    _status["last_result"] = result
                    _status["running"] = False
            except Exception as e:
                logger.error("支付监控周期异常: %s", e)
                with _status_lock:
                    _status["last_result"] = {"ok": False, "error": str(e)}
                    _status["running"] = False
                interval = 10
            self._stop.wait(max(3, interval))
        logger.info("支付监控线程已退出")
