"""卡密商城业务：下单 → 拉起支付 → 支付成功后自动发货。

商城订单的支付单号以 ``SHOP_`` 开头，支付监控命中后会回调
:meth:`ShopService.try_fulfill_by_payment` 完成发货。
"""

from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime

from core import mailer
from core.logger import get_logger
from core.settings import (
    load_alipay_config,
    load_email_config,
    load_shop_settings,
    payment_readiness,
)

from .db import ShopDB
from .generator import generate_codes

logger = get_logger("shop.service")

# 发货串行锁：避免监控、回调、补单并发时重复扣库存/重复发货
_fulfill_lock = threading.Lock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class ShopService:
    def __init__(self, db: ShopDB | None = None):
        self.db = db or ShopDB()

    # ---------- 基础设置 ----------

    def settings(self) -> dict:
        return load_shop_settings()

    # ---------- 商品 / 卡密 ----------

    def list_products(self, **kwargs):
        return self.db.list_products(**kwargs)

    def get_product(self, product_id):
        return self.db.get_product(product_id)

    def create_product(self, **kwargs):
        return self.db.create_product(**kwargs)

    def update_product(self, product_id, **kwargs):
        return self.db.update_product(product_id, **kwargs)

    def delete_product(self, product_id):
        return self.db.delete_product(product_id)

    def import_cards(self, product_id, codes):
        if not self.db.get_product(product_id):
            return {"ok": False, "msg": "商品不存在"}
        result = self.db.import_cards(product_id, codes)
        result["ok"] = True
        return result

    def list_cards(self, product_id, **kwargs):
        return self.db.list_cards(product_id, **kwargs)

    def disable_card(self, card_id):
        return self.db.disable_card(card_id)

    def enable_card(self, card_id):
        return self.db.enable_card(card_id)

    def delete_card(self, card_id):
        return self.db.delete_card(card_id)

    def batch_cards(self, product_id, action, card_ids):
        if not self.db.get_product(product_id):
            return {"ok": False, "msg": "商品不存在"}
        return self.db.batch_cards(product_id, action, card_ids)

    def list_orders(self, **kwargs):
        return self.db.list_orders(**kwargs)

    def get_stats(self):
        return self.db.get_stats()

    # ---------- 下单 ----------

    def _gen_order_no(self) -> str:
        return f"CS{int(time.time())}{secrets.token_hex(4).upper()}"

    def _gen_payment_no(self) -> str:
        return f"SHOP_{int(time.time())}_{secrets.token_hex(4)}"

    def create_order(self, product_id, qty=1, buyer_platform="", buyer_user_id="",
                     contact="", notify_url="", source="web"):
        settings = self.settings()
        if not settings.get("enabled", True):
            return {"code": -1, "msg": "商城已关闭"}
        if source == "web" and not settings.get("public_shop_enabled", True):
            return {"code": -1, "msg": "公开商城已关闭"}

        qty = int(qty or 1)
        max_qty = int(settings.get("max_qty_per_order") or 50)
        if qty < 1 or qty > max_qty:
            return {"code": -1, "msg": f"购买数量需在 1-{max_qty} 之间"}

        contact = str(contact or "").strip()
        if source == "web" and not mailer.is_email(contact):
            return {"code": -1, "msg": "请填写有效的邮箱地址（用于接收订单与卡密）"}
        if len(contact) > 200:
            return {"code": -1, "msg": "备注过长，最多 200 字"}

        product = self.db.get_product(product_id)
        if not product:
            return {"code": -1, "msg": "商品不存在"}
        if int(product.get("status") or 0) != 1:
            return {"code": -1, "msg": "商品已下架"}

        price = float(product.get("price") or 0)
        if price < 0.01:
            return {"code": -1, "msg": "商品价格无效"}

        mode = product.get("fulfill_mode") or "stock"
        if mode == "stock" and int(product.get("stock_count") or 0) < qty:
            return {"code": -1, "msg": f"库存不足，当前库存 {product.get('stock_count', 0)}"}

        pay_cfg = load_alipay_config()
        ready, reason = payment_readiness(pay_cfg)
        if not ready:
            return {"code": -1, "msg": f"支付未就绪：{reason}"}

        order_no = self._gen_order_no()
        payment_no = self._gen_payment_no()
        total_money = f"{price * qty:.2f}"
        name = str(product.get("name") or "卡密商品")
        if qty > 1:
            name = f"{name} x{qty}"

        order = self.db.create_order(
            order_no=order_no,
            product_id=product_id,
            product_name=product.get("name") or "卡密商品",
            price=price,
            qty=qty,
            payment_out_trade_no=payment_no,
            buyer_platform=buyer_platform,
            buyer_user_id=buyer_user_id,
            contact=contact,
        )

        from alipay_pay.codepay import CodePay

        pay_result = CodePay(pay_cfg).create_merchant_payment(
            total_money, name, payment_no, notify_url=notify_url, source="shop"
        )
        if pay_result.get("code") != 1:
            self.db.update_order(
                order["id"], status="failed",
                fail_reason=pay_result.get("msg") or "创建支付失败",
            )
            return {
                "code": -1,
                "msg": pay_result.get("msg") or "创建支付失败",
                "order_no": order_no,
            }

        return {
            "code": 1,
            "msg": "SUCCESS",
            "order_no": order_no,
            "payment_out_trade_no": payment_no,
            "product_id": product_id,
            "product_name": product.get("name"),
            "price": price,
            "qty": qty,
            "money": total_money,
            "payment_amount": pay_result.get("payment_amount", total_money),
            "qr_code": pay_result.get("qr_code", ""),
            "qr_code_url": pay_result.get("qr_code_url", ""),
            "payment_url": pay_result.get("payment_url", ""),
            "payment_instruction": pay_result.get("payment_instruction", ""),
            "business_qr_mode": pay_result.get("business_qr_mode", False),
            "trade_no": pay_result.get("trade_no", ""),
            "order": self.public_order_view(self.db.get_order_by_no(order_no)),
        }

    def public_order_view(self, order, include_cards=True):
        if not order:
            return None
        view = {
            "order_no": order.get("order_no"),
            "product_id": order.get("product_id"),
            "product_name": order.get("product_name_snapshot"),
            "price": order.get("price"),
            "qty": order.get("qty"),
            "money": f"{float(order.get('price') or 0) * int(order.get('qty') or 1):.2f}",
            "status": order.get("status"),
            "created_at": order.get("created_at"),
            "paid_at": order.get("paid_at"),
            "fulfilled_at": order.get("fulfilled_at"),
            "fail_reason": order.get("fail_reason") or "",
            "payment_out_trade_no": order.get("payment_out_trade_no"),
            "contact": order.get("contact") or "",
        }
        view["cards"] = (
            order.get("card_payload") or ""
            if include_cards and order.get("status") == "fulfilled" else ""
        )
        return view

    # ---------- 发货 ----------

    def fulfill_order(self, order, notify=True):
        """幂等发货（全局串行，防止并发重复发货）。"""
        with _fulfill_lock:
            return self._fulfill_order_locked(order, notify)

    def _fulfill_order_locked(self, order, notify=True):
        if not order:
            return {"code": -1, "msg": "订单不存在"}
        order = self.db.get_order_by_id(order["id"])
        if not order:
            return {"code": -1, "msg": "订单不存在"}

        if order.get("status") == "fulfilled" and order.get("card_payload"):
            return {"code": 1, "msg": "ALREADY_FULFILLED",
                    "order": self.public_order_view(order)}
        if order.get("status") in ("expired", "failed"):
            return {"code": -1, "msg": f"订单状态不可发货: {order.get('status')}"}

        product = self.db.get_product(order["product_id"])
        if not product:
            self.db.update_order(order["id"], status="failed", fail_reason="商品已删除")
            return {"code": -1, "msg": "商品已删除"}

        qty = int(order.get("qty") or 1)
        mode = product.get("fulfill_mode") or "stock"
        now = _now()

        if order.get("status") == "pending":
            order = self.db.update_order(order["id"], status="paid", paid_at=now) or order

        try:
            if mode == "generate":
                gen_cfg = product.get("generate_config") or {}
                per_unit = max(1, min(int(gen_cfg.get("quantity") or 1), 20))
                codes = generate_codes(gen_cfg, qty * per_unit)
                self.db.insert_sold_cards(product["id"], codes, order["id"])
            else:
                codes = self.db.pop_available_cards(product["id"], qty, order["id"])
                if not codes:
                    self.db.update_order(
                        order["id"], status="failed", fail_reason="库存不足，无法发货",
                        paid_at=order.get("paid_at") or now,
                    )
                    return {"code": -1, "msg": "库存不足，无法发货"}
        except Exception as e:
            self.db.update_order(
                order["id"], status="failed", fail_reason=str(e),
                paid_at=order.get("paid_at") or now,
            )
            return {"code": -1, "msg": f"发货失败: {e}"}

        payload = "\n".join(codes)
        order = self.db.update_order(
            order["id"],
            status="fulfilled",
            card_payload=payload,
            paid_at=order.get("paid_at") or now,
            fulfilled_at=now,
            fail_reason="",
        )
        if notify:
            self._notify_admin(order, payload)
            self._send_buyer_email(order, payload)
        return {"code": 1, "msg": "SUCCESS", "order": self.public_order_view(order)}

    def _send_buyer_email(self, order, payload):
        """异步发送买家邮件，不阻塞发货流程。"""
        from core.tasks import submit_mail

        submit_mail(self._send_buyer_email_sync, dict(order), payload)

    def _send_buyer_email_sync(self, order, payload):
        """发货后向买家邮箱发送订单与卡密（未启用时记录状态）。"""
        email_addr = str(order.get("contact") or "").strip()
        if not mailer.is_email(email_addr):
            self.db.update_email_status(order["id"], 4)
            return
        cfg = load_email_config()
        if not cfg.get("enabled"):
            self.db.update_email_status(order["id"], 3)
            return
        title = self.settings().get("shop_title") or "卡密商城"
        subject, html_body, text_body = mailer.order_email_content(title, order, payload)
        ok, error = mailer.send_email(cfg, email_addr, subject, html_body, text_body)
        self.db.update_email_status(order["id"], 1 if ok else 2, "" if ok else error)

    def _notify_admin(self, order, payload):
        settings = self.settings()
        if not settings.get("notify_admin_pushplus", True):
            return
        try:
            from alipay_pay.notify_pushplus import PushPlusNotifier

            cfg = load_alipay_config()
            notifier = PushPlusNotifier(cfg)
            if not notifier.enabled():
                return
            money = f"{float(order.get('price') or 0) * int(order.get('qty') or 1):.2f}"
            notifier.send(
                "【卡密商城】新成交",
                f"**商品:** {order.get('product_name_snapshot')}\n"
                f"**订单:** {order.get('order_no')}\n"
                f"**金额:** {money} 元\n"
                f"**数量:** {order.get('qty')}\n"
                f"**买家备注:** {order.get('contact') or '-'}",
                "markdown",
            )
        except Exception as e:
            logger.warning("商城成交通知失败: %s", e)

    def try_fulfill_by_payment(self, payment_out_trade_no, notify=True):
        order = self.db.get_order_by_payment(payment_out_trade_no)
        if not order:
            return {"code": -1, "msg": "商城订单不存在"}
        return self.fulfill_order(order, notify=notify)

    def try_fulfill_by_order_no(self, order_no, notify=True):
        order = self.db.get_order_by_no(order_no)
        if not order:
            return {"code": -1, "msg": "订单不存在"}
        return self.fulfill_order(order, notify=notify)

    def expire_pending_by_payment(self, payment_nos):
        return self.db.expire_pending_by_payment(payment_nos)

    def _sync_payment_status(self, order) -> dict:
        """查询码支付订单状态并与商城订单同步。"""
        payment_no = order.get("payment_out_trade_no")
        if not payment_no:
            return order
        from alipay_pay.orders import OrderStore

        pay_order = OrderStore().get_order(out_trade_no=payment_no)
        if not pay_order:
            return order
        if pay_order["status"] == 1 and order.get("status") == "pending":
            self.fulfill_order(order, notify=True)
        elif pay_order["status"] == 2 and order.get("status") == "pending":
            self.db.update_order(order["id"], status="expired")
        return self.db.get_order_by_no(order["order_no"])

    def query_order(self, order_no, sync_payment=True):
        order = self.db.get_order_by_no(order_no)
        if not order:
            return {"code": -1, "msg": "订单不存在"}
        if sync_payment and order.get("status") == "pending":
            order = self._sync_payment_status(order)
        return {"code": 1, "msg": "SUCCESS", "order": self.public_order_view(order)}

    def mark_paid_by_order_no(self, order_no):
        """后台/MCP 补单：未支付的支付单先标记支付，否则直接补发。"""
        order = self.db.get_order_by_no(order_no)
        if not order:
            return {"ok": False, "reason": "订单不存在"}
        if order.get("status") not in ("pending", "failed", "paid"):
            return {"ok": False, "reason": f"当前状态不可补单: {order.get('status')}"}

        from alipay_pay.monitor import mark_paid
        from alipay_pay.orders import OrderStore

        pay_order = OrderStore().get_order(
            out_trade_no=order.get("payment_out_trade_no")
        )
        if pay_order and pay_order["status"] == 0:
            mark_paid(pay_order["id"], detected_by="admin")
        else:
            result = self.fulfill_order(order, notify=True)
            if result.get("code") != 1 and result.get("msg") != "ALREADY_FULFILLED":
                return {"ok": False, "reason": result.get("msg", "发货失败")}
        fresh = self.db.get_order_by_no(order_no)
        return {"ok": True, "order": self.public_order_view(fresh)}

    def refulfill(self, order_no):
        order = self.db.get_order_by_no(order_no)
        if not order:
            return {"code": -1, "msg": "订单不存在"}
        if order.get("status") == "fulfilled" and order.get("card_payload"):
            return {"code": 1, "msg": "ALREADY_FULFILLED",
                    "order": self.public_order_view(order)}
        if order.get("status") in ("pending", "expired"):
            return {"code": -1, "msg": "订单未支付或已过期，无法补发"}
        if order.get("status") == "failed":
            self.db.update_order(order["id"], status="paid", fail_reason="")
            order = self.db.get_order_by_no(order_no)
        return self.fulfill_order(order, notify=True)

    # ---------- 公开查询 ----------

    def public_search(self, query, limit=5):
        q = str(query or "").strip()
        if not q:
            return {"code": -1, "msg": "请输入订单号或备注（邮箱/QQ/手机号）"}
        if len(q) < 4:
            return {"code": -1, "msg": "查询内容至少 4 个字符"}
        if len(q) > 200:
            return {"code": -1, "msg": "查询内容过长"}

        not_found = {"code": -1, "msg": "未找到相关订单"}
        orders = []
        exact = self.db.get_order_by_no(q)
        if exact:
            synced = self.query_order(exact["order_no"], sync_payment=True)
            orders = [synced["order"]] if synced.get("code") == 1 else [self.public_order_view(exact)]
        else:
            for o in self.db.list_orders_by_contact_exact(q, limit=min(int(limit or 5), 10)):
                if o.get("status") == "pending":
                    synced = self.query_order(o["order_no"], sync_payment=True)
                    if synced.get("code") == 1 and synced.get("order"):
                        orders.append(synced["order"])
                        continue
                orders.append(self.public_order_view(o))

        if not orders:
            return not_found
        safe = []
        for o in orders:
            if not o:
                continue
            item = dict(o)
            item.pop("payment_out_trade_no", None)
            safe.append(item)
        return {"code": 1, "msg": "SUCCESS", "orders": safe, "total": len(safe)}

    # ---------- 客服 ----------

    def _validate_contact_has_order(self, contact):
        contact = str(contact or "").strip()
        if len(contact) < 4:
            return None, {"code": -1, "msg": "请填写完整备注（至少 4 个字符）"}
        if not self.db.list_orders_by_contact_exact(contact, limit=1):
            return None, {"code": -1, "msg": "该备注在订单中无记录，请填写下单时的备注后再试"}
        return contact, None

    def user_start_feedback(self, contact):
        contact, err = self._validate_contact_has_order(contact)
        if err:
            return err
        ticket = self.db.get_or_create_ticket(contact)
        return {
            "code": 1,
            "msg": "SUCCESS",
            "ticket": self._ticket_view(ticket),
            "messages": [self._msg_view(m) for m in self.db.list_messages(ticket["id"])],
            "order_count": len(self.db.list_orders_by_contact_exact(contact, limit=50)),
            "closed": ticket.get("status") == "closed",
        }

    def user_send_feedback(self, contact, content):
        contact, err = self._validate_contact_has_order(contact)
        if err:
            return err
        content = str(content or "").strip()
        if not content:
            return {"code": -1, "msg": "消息不能为空"}
        ticket = self.db.get_ticket_by_contact(contact) or self.db.get_or_create_ticket(contact)
        if not ticket:
            return {"code": -1, "msg": "无法创建会话"}
        if ticket.get("status") == "closed":
            return {"code": -1, "msg": "该会话已被管理员关闭，暂不能发送消息", "closed": True}
        try:
            msg = self.db.add_message(ticket["id"], "user", content)
        except Exception as e:
            return {"code": -1, "msg": str(e)}
        return {
            "code": 1,
            "msg": "SUCCESS",
            "ticket": self._ticket_view(self.db.get_ticket(ticket["id"])),
            "message": self._msg_view(msg),
        }

    def user_pull_feedback(self, contact):
        contact, err = self._validate_contact_has_order(contact)
        if err:
            return err
        ticket = self.db.get_ticket_by_contact(contact)
        if not ticket:
            return {"code": -1, "msg": "暂无会话，请先发送消息"}
        return {
            "code": 1,
            "msg": "SUCCESS",
            "ticket": self._ticket_view(ticket),
            "messages": [self._msg_view(m) for m in self.db.list_messages(ticket["id"])],
        }

    def admin_list_tickets(self, **kwargs):
        return self.db.list_tickets(**kwargs)

    def admin_get_ticket(self, ticket_id, mark_read=True):
        ticket = self.db.get_ticket(ticket_id)
        if not ticket:
            return {"code": -1, "msg": "工单不存在"}
        if mark_read:
            ticket = self.db.mark_ticket_read(ticket_id) or ticket
        return {
            "code": 1,
            "ticket": self._ticket_view(ticket),
            "messages": [self._msg_view(m) for m in self.db.list_messages(ticket_id)],
        }

    def admin_reply_ticket(self, ticket_id, content):
        if not self.db.get_ticket(ticket_id):
            return {"code": -1, "msg": "工单不存在"}
        try:
            msg = self.db.add_message(ticket_id, "admin", content)
        except Exception as e:
            return {"code": -1, "msg": str(e)}
        return {
            "code": 1,
            "msg": "SUCCESS",
            "ticket": self._ticket_view(self.db.get_ticket(ticket_id)),
            "message": self._msg_view(msg),
        }

    def admin_set_ticket_status(self, ticket_id, status):
        try:
            ticket = self.db.update_ticket_status(ticket_id, status)
        except Exception as e:
            return {"code": -1, "msg": str(e)}
        if not ticket:
            return {"code": -1, "msg": "工单不存在"}
        return {"code": 1, "msg": "SUCCESS", "ticket": self._ticket_view(ticket)}

    def feedback_stats(self):
        return self.db.feedback_stats()

    # ---------- 批量 ----------

    def delete_orders_many(self, order_nos):
        return self.db.delete_orders_many(order_nos)

    def delete_products_many(self, ids):
        return self.db.delete_products_many(ids)

    def set_products_status_many(self, ids, status):
        return self.db.set_products_status_many(ids, status)

    def delete_tickets_many(self, ids):
        return self.db.delete_tickets_many(ids)

    def set_tickets_status_many(self, ids, status):
        return self.db.set_tickets_status_many(ids, status)

    def _ticket_view(self, t):
        if not t:
            return None
        return {
            "id": t.get("id"),
            "contact": t.get("contact"),
            "status": t.get("status"),
            "last_message": t.get("last_message") or "",
            "last_sender": t.get("last_sender") or "",
            "unread": bool(t.get("unread")),
            "created_at": t.get("created_at"),
            "updated_at": t.get("updated_at"),
        }

    def _msg_view(self, m):
        if not m:
            return None
        return {
            "id": m.get("id"),
            "ticket_id": m.get("ticket_id"),
            "sender": m.get("sender"),
            "content": m.get("content"),
            "created_at": m.get("created_at"),
        }
