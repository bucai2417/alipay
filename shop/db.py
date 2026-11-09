"""卡密商城数据层（与码支付共用 data/alipay.db）。"""

from __future__ import annotations

import json
import sqlite3
import uuid

from core.logger import get_logger
from core.storage import Database, db as _db, now_str

logger = get_logger("shop.db")


class ShopDB:
    def __init__(self, database: Database | None = None):
        self.db = database or _db

    def _c(self):
        return self.db.conn()

    # ---------- 商品 ----------

    def create_product(self, name, description, price, fulfill_mode="stock",
                       generate_config=None, status=1, sort_order=0, icon_path=""):
        c = self._c()
        now = now_str()
        gen_cfg = json.dumps(generate_config or {}, ensure_ascii=False)
        with self.db.write_lock:
            cur = c.execute(
                """INSERT INTO products
                   (name, description, price, status, fulfill_mode, generate_config,
                    stock_count, sold_count, sort_order, icon_path, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?)""",
                (name, description or "", float(price), int(status), fulfill_mode,
                 gen_cfg, int(sort_order or 0), icon_path or "", now, now),
            )
            c.commit()
            pid = cur.lastrowid
        return self.get_product(pid)

    def update_product(self, product_id, **fields):
        allowed = {
            "name", "description", "price", "status", "fulfill_mode",
            "generate_config", "sort_order", "stock_count", "sold_count",
            "icon_path",
        }
        updates, params = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "generate_config" and not isinstance(v, str):
                v = json.dumps(v or {}, ensure_ascii=False)
            if k == "price":
                v = float(v)
            if k in ("status", "sort_order", "stock_count", "sold_count"):
                v = int(v)
            updates.append(f"{k} = ?")
            params.append(v)
        if not updates:
            return self.get_product(product_id)
        updates.append("updated_at = ?")
        params.extend([now_str(), product_id])
        c = self._c()
        with self.db.write_lock:
            c.execute(f"UPDATE products SET {', '.join(updates)} WHERE id = ?", params)
            c.commit()
        return self.get_product(product_id)

    def delete_product(self, product_id):
        c = self._c()
        order_count = c.execute(
            "SELECT COUNT(*) AS cnt FROM shop_orders WHERE product_id = ?", (product_id,)
        ).fetchone()["cnt"]
        if order_count:
            return {"ok": False, "msg": "该商品已有订单记录，无法删除，建议改为下架"}
        with self.db.write_lock:
            c.execute("DELETE FROM cards WHERE product_id = ?", (product_id,))
            c.execute("DELETE FROM products WHERE id = ?", (product_id,))
            c.commit()
        return {"ok": True}

    def get_product(self, product_id):
        row = self._c().execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        return self._product_row(row)

    def list_products(self, only_on_sale=False, page=1, page_size=50, search=None):
        c = self._c()
        conditions, params = [], []
        if only_on_sale:
            conditions.append("status = 1")
        if search:
            conditions.append("(name LIKE ? OR description LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        total = c.execute(f"SELECT COUNT(*) AS cnt FROM products{where}", params).fetchone()["cnt"]
        offset = (max(int(page or 1), 1) - 1) * int(page_size or 50)
        rows = c.execute(
            f"""SELECT * FROM products{where}
                ORDER BY sort_order ASC, id DESC LIMIT ? OFFSET ?""",
            params + [int(page_size or 50), offset],
        ).fetchall()
        return {
            "products": [self._product_row(r) for r in rows],
            "total": total,
            "page": int(page or 1),
            "page_size": int(page_size or 50),
        }

    def refresh_stock_count(self, product_id):
        c = self._c()
        with self.db.write_lock:
            row = c.execute(
                "SELECT COUNT(*) AS cnt FROM cards WHERE product_id = ? AND status = 'available'",
                (product_id,),
            ).fetchone()
            cnt = int(row["cnt"] if row else 0)
            c.execute(
                "UPDATE products SET stock_count = ?, updated_at = ? WHERE id = ?",
                (cnt, now_str(), product_id),
            )
            c.commit()
        return cnt

    def _product_row(self, row):
        if not row:
            return None
        d = dict(row)
        try:
            d["generate_config"] = json.loads(d.get("generate_config") or "{}")
        except Exception:
            d["generate_config"] = {}
        d["price"] = float(d.get("price") or 0)
        d["icon_path"] = d.get("icon_path") or ""
        return d

    # ---------- 卡密 ----------

    def import_cards(self, product_id, codes):
        cleaned, seen = [], set()
        for c0 in codes:
            code = str(c0 or "").strip()
            if not code or code in seen:
                continue
            seen.add(code)
            cleaned.append(code)
        if not cleaned:
            return {"imported": 0, "skipped": 0,
                    "stock_count": self.refresh_stock_count(product_id)}

        c = self._c()
        now = now_str()
        imported = skipped = 0
        with self.db.write_lock:
            for code in cleaned:
                try:
                    c.execute(
                        """INSERT INTO cards (product_id, code, status, created_at)
                           VALUES (?, ?, 'available', ?)""",
                        (product_id, code, now),
                    )
                    imported += 1
                except sqlite3.IntegrityError:
                    skipped += 1
            c.commit()
        return {"imported": imported, "skipped": skipped,
                "stock_count": self.refresh_stock_count(product_id)}

    def list_cards(self, product_id, status=None, page=1, page_size=50, search=None):
        c = self._c()
        conditions, params = ["c.product_id = ?"], [product_id]
        if status:
            conditions.append("c.status = ?")
            params.append(status)
        if search:
            conditions.append(
                "(c.code LIKE ? OR CAST(c.sold_order_id AS TEXT) LIKE ? OR IFNULL(o.order_no,'') LIKE ?)"
            )
            kw = f"%{search}%"
            params.extend([kw, kw, kw])
        where = " WHERE " + " AND ".join(conditions)
        total = c.execute(
            f"""SELECT COUNT(*) AS cnt FROM cards c
                LEFT JOIN shop_orders o ON o.id = c.sold_order_id {where}""",
            params,
        ).fetchone()["cnt"]
        offset = (max(int(page or 1), 1) - 1) * int(page_size or 50)
        rows = c.execute(
            f"""SELECT c.*, o.order_no AS sold_order_no
                FROM cards c LEFT JOIN shop_orders o ON o.id = c.sold_order_id
                {where} ORDER BY c.id DESC LIMIT ? OFFSET ?""",
            params + [int(page_size or 50), offset],
        ).fetchall()
        cards = []
        for r in rows:
            d = dict(r)
            if not d.get("sold_order_no") and d.get("sold_order_id"):
                d["sold_order_no"] = str(d.get("sold_order_id"))
            cards.append(d)
        return {"cards": cards, "total": total, "page": int(page or 1),
                "page_size": int(page_size or 50)}

    def _get_card(self, card_id):
        return self._c().execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()

    def disable_card(self, card_id):
        c = self._c()
        with self.db.write_lock:
            row = self._get_card(card_id)
            if not row:
                return None
            if row["status"] == "sold":
                return dict(row)
            c.execute(
                "UPDATE cards SET status = 'disabled' WHERE id = ? AND status = 'available'",
                (card_id,),
            )
            c.commit()
            product_id = row["product_id"]
        self.refresh_stock_count(product_id)
        return dict(self._get_card(card_id))

    def enable_card(self, card_id):
        c = self._c()
        with self.db.write_lock:
            row = self._get_card(card_id)
            if not row:
                return None
            if row["status"] != "disabled":
                return dict(row)
            c.execute(
                "UPDATE cards SET status = 'available' WHERE id = ? AND status = 'disabled'",
                (card_id,),
            )
            c.commit()
            product_id = row["product_id"]
        self.refresh_stock_count(product_id)
        return dict(self._get_card(card_id))

    def delete_card(self, card_id):
        c = self._c()
        with self.db.write_lock:
            row = self._get_card(card_id)
            if not row:
                return None
            if row["status"] == "sold":
                return {"ok": False, "msg": "已售出的卡密不可删除", "card": dict(row)}
            product_id = row["product_id"]
            c.execute("DELETE FROM cards WHERE id = ? AND status != 'sold'", (card_id,))
            c.commit()
        stock = self.refresh_stock_count(product_id)
        return {"ok": True, "stock_count": stock, "product_id": product_id}

    def batch_cards(self, product_id, action, card_ids):
        ids, seen = [], set()
        for x in card_ids or []:
            try:
                i = int(x)
            except (TypeError, ValueError):
                continue
            if i <= 0 or i in seen:
                continue
            seen.add(i)
            ids.append(i)
        if action not in ("disable", "enable", "delete"):
            return {"ok": False, "msg": "action 无效"}
        if not ids:
            return {"ok": True, "affected": 0, "skipped": 0,
                    "stock_count": self.refresh_stock_count(product_id)}

        c = self._c()
        affected = skipped = 0
        with self.db.write_lock:
            for cid in ids:
                row = c.execute(
                    "SELECT id, status FROM cards WHERE id = ? AND product_id = ?",
                    (cid, product_id),
                ).fetchone()
                if not row:
                    skipped += 1
                    continue
                st = row["status"]
                if action == "disable":
                    if st != "available":
                        skipped += 1
                        continue
                    c.execute(
                        "UPDATE cards SET status = 'disabled' WHERE id = ? AND status = 'available'",
                        (cid,),
                    )
                    affected += 1
                elif action == "enable":
                    if st != "disabled":
                        skipped += 1
                        continue
                    c.execute(
                        "UPDATE cards SET status = 'available' WHERE id = ? AND status = 'disabled'",
                        (cid,),
                    )
                    affected += 1
                else:
                    if st == "sold":
                        skipped += 1
                        continue
                    c.execute("DELETE FROM cards WHERE id = ? AND status != 'sold'", (cid,))
                    affected += 1
            c.commit()
        return {"ok": True, "affected": affected, "skipped": skipped,
                "stock_count": self.refresh_stock_count(product_id)}

    def pop_available_cards(self, product_id, qty, order_id):
        c = self._c()
        now = now_str()
        with self.db.write_lock:
            rows = c.execute(
                """SELECT id, code FROM cards
                   WHERE product_id = ? AND status = 'available'
                   ORDER BY id ASC LIMIT ?""",
                (product_id, qty),
            ).fetchall()
            if len(rows) < qty:
                return []
            codes = []
            for r in rows:
                cur = c.execute(
                    """UPDATE cards SET status = 'sold', sold_order_id = ?, sold_at = ?
                       WHERE id = ? AND status = 'available'""",
                    (order_id, now, r["id"]),
                )
                if cur.rowcount != 1:
                    c.rollback()
                    return []
                codes.append(r["code"])
            c.execute(
                """UPDATE products SET
                       stock_count = (SELECT COUNT(*) FROM cards
                                      WHERE product_id = ? AND status = 'available'),
                       sold_count = sold_count + ?,
                       updated_at = ?
                   WHERE id = ?""",
                (product_id, qty, now, product_id),
            )
            c.commit()
            return codes

    def insert_sold_cards(self, product_id, codes, order_id):
        c = self._c()
        now = now_str()
        with self.db.write_lock:
            for code in codes:
                try:
                    c.execute(
                        """INSERT INTO cards
                           (product_id, code, status, sold_order_id, sold_at, created_at)
                           VALUES (?, ?, 'sold', ?, ?, ?)""",
                        (product_id, code, order_id, now, now),
                    )
                except sqlite3.IntegrityError:
                    alt = f"{code}-{uuid.uuid4().hex[:6]}"
                    c.execute(
                        """INSERT INTO cards
                           (product_id, code, status, sold_order_id, sold_at, created_at)
                           VALUES (?, ?, 'sold', ?, ?, ?)""",
                        (product_id, alt, order_id, now, now),
                    )
            c.execute(
                "UPDATE products SET sold_count = sold_count + ?, updated_at = ? WHERE id = ?",
                (len(codes), now, product_id),
            )
            c.commit()

    # ---------- 商城订单 ----------

    def create_order(self, order_no, product_id, product_name, price, qty,
                     payment_out_trade_no, buyer_platform="", buyer_user_id="", contact=""):
        c = self._c()
        with self.db.write_lock:
            cur = c.execute(
                """INSERT INTO shop_orders
                   (order_no, product_id, product_name_snapshot, price, qty,
                    buyer_platform, buyer_user_id, contact, payment_out_trade_no,
                    status, card_payload, fail_reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', '', ?)""",
                (order_no, product_id, product_name, float(price), int(qty),
                 buyer_platform or "", buyer_user_id or "", contact or "",
                 payment_out_trade_no, now_str()),
            )
            c.commit()
            oid = cur.lastrowid
        return self.get_order_by_id(oid)

    def get_order_by_id(self, order_id):
        row = self._c().execute(
            "SELECT * FROM shop_orders WHERE id = ?", (order_id,)
        ).fetchone()
        return self._order_row(row)

    def get_order_by_no(self, order_no):
        row = self._c().execute(
            "SELECT * FROM shop_orders WHERE order_no = ?", (order_no,)
        ).fetchone()
        return self._order_row(row)

    def get_order_by_payment(self, payment_out_trade_no):
        row = self._c().execute(
            "SELECT * FROM shop_orders WHERE payment_out_trade_no = ?",
            (payment_out_trade_no,),
        ).fetchone()
        return self._order_row(row)

    def update_order(self, order_id, **fields):
        allowed = {
            "status", "card_payload", "fail_reason", "paid_at", "fulfilled_at",
            "buyer_platform", "buyer_user_id", "contact",
        }
        updates, params = [], []
        for k, v in fields.items():
            if k in allowed:
                updates.append(f"{k} = ?")
                params.append(v)
        if not updates:
            return self.get_order_by_id(order_id)
        params.append(order_id)
        c = self._c()
        with self.db.write_lock:
            c.execute(f"UPDATE shop_orders SET {', '.join(updates)} WHERE id = ?", params)
            c.commit()
        return self.get_order_by_id(order_id)

    def list_orders(self, status=None, page=1, page_size=20, search=None,
                    buyer_platform=None, buyer_user_id=None):
        c = self._c()
        conditions, params = [], []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if buyer_platform:
            conditions.append("buyer_platform = ?")
            params.append(buyer_platform)
        if buyer_user_id:
            conditions.append("buyer_user_id = ?")
            params.append(buyer_user_id)
        if search:
            conditions.append(
                "(order_no LIKE ? OR payment_out_trade_no LIKE ? "
                "OR product_name_snapshot LIKE ? OR contact LIKE ?)"
            )
            params.extend([f"%{search}%"] * 4)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        total = c.execute(f"SELECT COUNT(*) AS cnt FROM shop_orders{where}", params).fetchone()["cnt"]
        offset = (max(int(page or 1), 1) - 1) * int(page_size or 20)
        rows = c.execute(
            f"SELECT * FROM shop_orders{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [int(page_size or 20), offset],
        ).fetchall()
        return {"orders": [self._order_row(r) for r in rows], "total": total,
                "page": int(page or 1), "page_size": int(page_size or 20)}

    def list_orders_by_contact_exact(self, contact, limit=5):
        contact = str(contact or "").strip()
        if not contact:
            return []
        rows = self._c().execute(
            """SELECT * FROM shop_orders
               WHERE lower(contact) = lower(?)
               ORDER BY id DESC LIMIT ?""",
            (contact, max(1, min(int(limit or 5), 10))),
        ).fetchall()
        return [self._order_row(r) for r in rows]

    def expire_pending_by_payment(self, payment_nos):
        if not payment_nos:
            return 0
        c = self._c()
        n = 0
        with self.db.write_lock:
            for pno in payment_nos:
                cur = c.execute(
                    "UPDATE shop_orders SET status = 'expired' WHERE payment_out_trade_no = ? AND status = 'pending'",
                    (pno,),
                )
                n += cur.rowcount
            c.commit()
        return n

    def get_stats(self):
        c = self._c()
        row = c.execute(
            """SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status IN ('paid','fulfilled') THEN 1 ELSE 0 END) AS paid,
                SUM(CASE WHEN status='fulfilled' THEN 1 ELSE 0 END) AS fulfilled,
                SUM(CASE WHEN status='expired' THEN 1 ELSE 0 END) AS expired,
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                COALESCE(SUM(CASE WHEN status IN ('paid','fulfilled') THEN price * qty ELSE 0 END), 0) AS total_revenue
            FROM shop_orders"""
        ).fetchone()
        prod = c.execute(
            """SELECT COUNT(*) AS product_total,
                      SUM(CASE WHEN status=1 THEN 1 ELSE 0 END) AS product_on_sale,
                      COALESCE(SUM(stock_count), 0) AS stock_total
               FROM products"""
        ).fetchone()
        stats = dict(row) if row else {}
        stats.update(dict(prod) if prod else {})
        for k in list(stats.keys()):
            if stats[k] is None:
                stats[k] = 0
        stats["total_revenue"] = float(stats.get("total_revenue") or 0)
        return stats

    def _order_row(self, row):
        if not row:
            return None
        d = dict(row)
        d["price"] = float(d.get("price") or 0)
        d["qty"] = int(d.get("qty") or 1)
        d["email_status"] = int(d.get("email_status") or 0)
        d["email_error"] = d.get("email_error") or ""
        d["email_sent_at"] = d.get("email_sent_at")
        return d

    def update_email_status(self, order_id, status, error=""):
        """邮件状态：0 未处理 / 1 成功 / 2 失败 / 3 未启用 / 4 无有效邮箱。"""
        c = self._c()
        sent_at = now_str() if int(status) == 1 else None
        with self.db.write_lock:
            c.execute(
                """UPDATE shop_orders
                   SET email_status = ?, email_error = ?, email_sent_at = ?
                   WHERE id = ?""",
                (int(status), (error or "")[:255], sent_at, order_id),
            )
            c.commit()

    # ---------- 客服工单 ----------

    def get_or_create_ticket(self, contact):
        contact = str(contact or "").strip()
        if not contact:
            return None
        c = self._c()
        row = c.execute(
            "SELECT * FROM feedback_tickets WHERE lower(contact) = lower(?) ORDER BY id DESC LIMIT 1",
            (contact,),
        ).fetchone()
        if row:
            return self._ticket_row(row)
        now = now_str()
        with self.db.write_lock:
            cur = c.execute(
                """INSERT INTO feedback_tickets
                   (contact, status, last_message, last_sender, created_at, updated_at)
                   VALUES (?, 'open', '', 'user', ?, ?)""",
                (contact, now, now),
            )
            c.commit()
            tid = cur.lastrowid
        return self._ticket_row(
            c.execute("SELECT * FROM feedback_tickets WHERE id = ?", (tid,)).fetchone()
        )

    def get_ticket(self, ticket_id):
        row = self._c().execute(
            "SELECT * FROM feedback_tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        return self._ticket_row(row)

    def get_ticket_by_contact(self, contact):
        contact = str(contact or "").strip()
        if not contact:
            return None
        row = self._c().execute(
            "SELECT * FROM feedback_tickets WHERE lower(contact) = lower(?) ORDER BY id DESC LIMIT 1",
            (contact,),
        ).fetchone()
        return self._ticket_row(row)

    def list_tickets(self, status=None, page=1, page_size=20, search=None, unread_only=False):
        c = self._c()
        conditions, params = [], []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if search:
            conditions.append("(contact LIKE ? OR last_message LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        if unread_only:
            conditions.append(
                "last_sender = 'user' AND (admin_read_at IS NULL OR admin_read_at < updated_at)"
            )
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        total = c.execute(f"SELECT COUNT(*) AS cnt FROM feedback_tickets{where}", params).fetchone()["cnt"]
        offset = (max(int(page or 1), 1) - 1) * int(page_size or 20)
        rows = c.execute(
            f"""SELECT * FROM feedback_tickets{where}
                ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?""",
            params + [int(page_size or 20), offset],
        ).fetchall()
        return {"tickets": [self._ticket_row(r) for r in rows], "total": total,
                "page": int(page or 1), "page_size": int(page_size or 20)}

    def _ticket_row(self, row):
        if not row:
            return None
        d = dict(row)
        d["unread"] = self._ticket_unread(d)
        return d

    def _ticket_unread(self, t):
        if not t or str(t.get("last_sender") or "") != "user":
            return False
        if str(t.get("status") or "") == "closed":
            return False
        read_at = t.get("admin_read_at") or ""
        updated = t.get("updated_at") or ""
        if not read_at:
            return True
        return str(read_at) < str(updated)

    def mark_ticket_read(self, ticket_id):
        c = self._c()
        with self.db.write_lock:
            c.execute(
                "UPDATE feedback_tickets SET admin_read_at = ? WHERE id = ?",
                (now_str(), ticket_id),
            )
            c.commit()
        return self.get_ticket(ticket_id)

    def list_messages(self, ticket_id, limit=200):
        rows = self._c().execute(
            """SELECT * FROM feedback_messages WHERE ticket_id = ?
               ORDER BY id ASC LIMIT ?""",
            (ticket_id, max(1, min(int(limit or 200), 500))),
        ).fetchall()
        return [dict(r) for r in rows]

    def add_message(self, ticket_id, sender, content):
        content = str(content or "").strip()
        if not content:
            raise ValueError("消息不能为空")
        if sender not in ("user", "admin"):
            raise ValueError("sender 无效")
        if len(content) > 2000:
            raise ValueError("消息过长（最多 2000 字）")
        c = self._c()
        now = now_str()
        status = "open" if sender == "user" else "replied"
        with self.db.write_lock:
            cur = c.execute(
                """INSERT INTO feedback_messages (ticket_id, sender, content, created_at)
                   VALUES (?, ?, ?, ?)""",
                (ticket_id, sender, content, now),
            )
            c.execute(
                """UPDATE feedback_tickets
                   SET status = ?, last_message = ?, last_sender = ?, updated_at = ?
                   WHERE id = ?""",
                (status, content[:200], sender, now, ticket_id),
            )
            c.commit()
            mid = cur.lastrowid
        row = c.execute("SELECT * FROM feedback_messages WHERE id = ?", (mid,)).fetchone()
        return dict(row) if row else None

    def update_ticket_status(self, ticket_id, status):
        if status not in ("open", "replied", "closed"):
            raise ValueError("status 无效")
        c = self._c()
        with self.db.write_lock:
            c.execute(
                "UPDATE feedback_tickets SET status = ?, updated_at = ? WHERE id = ?",
                (status, now_str(), ticket_id),
            )
            c.commit()
        return self.get_ticket(ticket_id)

    def feedback_stats(self):
        row = self._c().execute(
            """SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_count,
                SUM(CASE WHEN status='replied' THEN 1 ELSE 0 END) AS replied_count,
                SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closed_count,
                SUM(CASE WHEN last_sender = 'user' AND status != 'closed'
                             AND (admin_read_at IS NULL OR admin_read_at < updated_at)
                         THEN 1 ELSE 0 END) AS unread_count
            FROM feedback_tickets"""
        ).fetchone()
        d = dict(row) if row else {}
        for k in list(d.keys()):
            if d[k] is None:
                d[k] = 0
        return d

    # ---------- 批量操作 ----------

    @staticmethod
    def _clean_str_ids(ids):
        out, seen = [], set()
        for x in ids or []:
            val = str(x or "").strip()
            if not val or val in seen:
                continue
            seen.add(val)
            out.append(val)
        return out

    @staticmethod
    def _clean_int_ids(ids):
        out, seen = [], set()
        for x in ids or []:
            try:
                val = int(x)
            except (TypeError, ValueError):
                continue
            if val <= 0 or val in seen:
                continue
            seen.add(val)
            out.append(val)
        return out

    def delete_orders_many(self, order_nos) -> int:
        cleaned = self._clean_str_ids(order_nos)
        if not cleaned:
            return 0
        c = self._c()
        placeholders = ",".join("?" for _ in cleaned)
        with self.db.write_lock:
            cur = c.execute(
                f"DELETE FROM shop_orders WHERE order_no IN ({placeholders})", cleaned
            )
            c.commit()
            return cur.rowcount or 0

    def set_products_status_many(self, ids, status) -> int:
        cleaned = self._clean_int_ids(ids)
        if not cleaned:
            return 0
        c = self._c()
        placeholders = ",".join("?" for _ in cleaned)
        with self.db.write_lock:
            cur = c.execute(
                f"UPDATE products SET status = ?, updated_at = ? WHERE id IN ({placeholders})",
                [int(status), now_str()] + cleaned,
            )
            c.commit()
            return cur.rowcount or 0

    def delete_products_many(self, ids):
        deleted = 0
        skipped = []
        for pid in self._clean_int_ids(ids):
            result = self.delete_product(pid)
            if result.get("ok"):
                deleted += 1
            else:
                skipped.append({"id": pid, "reason": result.get("msg", "删除失败")})
        return {"deleted": deleted, "skipped": skipped}

    def delete_tickets_many(self, ids) -> int:
        cleaned = self._clean_int_ids(ids)
        if not cleaned:
            return 0
        c = self._c()
        placeholders = ",".join("?" for _ in cleaned)
        with self.db.write_lock:
            cur = c.execute(
                f"DELETE FROM feedback_tickets WHERE id IN ({placeholders})", cleaned
            )
            c.commit()
            return cur.rowcount or 0

    def set_tickets_status_many(self, ids, status) -> int:
        if status not in ("open", "replied", "closed"):
            raise ValueError("status 无效")
        cleaned = self._clean_int_ids(ids)
        if not cleaned:
            return 0
        c = self._c()
        placeholders = ",".join("?" for _ in cleaned)
        with self.db.write_lock:
            cur = c.execute(
                f"""UPDATE feedback_tickets
                    SET status = ?, updated_at = ?
                    WHERE id IN ({placeholders})""",
                [status, now_str()] + cleaned,
            )
            c.commit()
            return cur.rowcount or 0
