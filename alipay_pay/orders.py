"""码支付订单数据访问。

状态：0=待支付，1=已支付，2=已过期。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from core.storage import Database, db as _db, now_str


class OrderStore:
    def __init__(self, database: Database | None = None):
        self.db = database or _db

    # ---------- 写入 ----------

    def insert_order(self, order_id, out_trade_no, order_type, pid, name, price,
                     payment_amount, status=0, notify_url="", return_url="",
                     sitename="", source="api"):
        c = self.db.conn()
        with self.db.write_lock:
            c.execute(
                """INSERT INTO codepay_orders
                   (id, out_trade_no, type, pid, name, price, payment_amount, status,
                    add_time, notify_url, return_url, sitename, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (order_id, out_trade_no, order_type, pid, name, float(price),
                 float(payment_amount), int(status), now_str(),
                 notify_url or "", return_url or "", sitename or "", source or "api"),
            )
            c.commit()

    def update_order_status(self, order_id, status, pay_time=None):
        c = self.db.conn()
        with self.db.write_lock:
            c.execute(
                "UPDATE codepay_orders SET status = ?, pay_time = ? WHERE id = ?",
                (int(status), pay_time or now_str(), order_id),
            )
            c.commit()

    def mark_paid(self, order_id) -> bool:
        """幂等标记已支付；返回本次是否发生状态迁移。"""
        c = self.db.conn()
        with self.db.write_lock:
            cur = c.execute(
                "UPDATE codepay_orders SET status = 1, pay_time = ? WHERE id = ? AND status = 0",
                (now_str(), order_id),
            )
            c.commit()
            return cur.rowcount == 1

    def delete_order(self, order_id):
        c = self.db.conn()
        with self.db.write_lock:
            c.execute("DELETE FROM codepay_orders WHERE id = ?", (order_id,))
            c.commit()

    # ---------- 查询 ----------

    def get_order(self, out_trade_no=None, pid=None, order_id=None):
        c = self.db.conn()
        if order_id:
            row = c.execute("SELECT * FROM codepay_orders WHERE id = ?", (order_id,)).fetchone()
        elif out_trade_no and pid:
            row = c.execute(
                """SELECT * FROM codepay_orders WHERE out_trade_no = ? AND pid = ?
                   ORDER BY status DESC, add_time DESC""",
                (out_trade_no, pid),
            ).fetchone()
        elif out_trade_no:
            row = c.execute(
                """SELECT * FROM codepay_orders WHERE out_trade_no = ?
                   ORDER BY status DESC, add_time DESC""",
                (out_trade_no,),
            ).fetchone()
        else:
            return None
        return dict(row) if row else None

    def get_pending_orders(self, pid=None):
        c = self.db.conn()
        if pid:
            rows = c.execute(
                "SELECT * FROM codepay_orders WHERE status = 0 AND pid = ? ORDER BY add_time ASC",
                (pid,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM codepay_orders WHERE status = 0 ORDER BY add_time ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_orders(self, pid, limit=20):
        c = self.db.conn()
        rows = c.execute(
            "SELECT * FROM codepay_orders WHERE pid = ? ORDER BY add_time DESC LIMIT ?",
            (pid, min(int(limit or 20), 100)),
        ).fetchall()
        return [dict(r) for r in rows]

    def find_order_by_amount(self, amount, after_time=None):
        c = self.db.conn()
        if after_time:
            row = c.execute(
                """SELECT * FROM codepay_orders
                   WHERE payment_amount = ? AND status = 0 AND add_time >= ?
                   ORDER BY add_time ASC LIMIT 1""",
                (float(amount), after_time),
            ).fetchone()
        else:
            row = c.execute(
                """SELECT * FROM codepay_orders
                   WHERE payment_amount = ? AND status = 0
                   ORDER BY add_time ASC LIMIT 1""",
                (float(amount),),
            ).fetchone()
        return dict(row) if row else None

    def paginated(self, pid=None, status=None, page=1, page_size=20, search=None):
        c = self.db.conn()
        conditions, params = [], []
        if pid:
            conditions.append("pid = ?")
            params.append(pid)
        if status is not None and status != "":
            conditions.append("status = ?")
            params.append(int(status))
        if search:
            conditions.append("(out_trade_no LIKE ? OR name LIKE ? OR id LIKE ?)")
            kw = f"%{search}%"
            params.extend([kw, kw, kw])
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        total = c.execute(
            f"SELECT COUNT(*) AS cnt FROM codepay_orders{where}", params
        ).fetchone()["cnt"]
        offset = (max(int(page or 1), 1) - 1) * int(page_size or 20)
        rows = c.execute(
            f"SELECT * FROM codepay_orders{where} ORDER BY add_time DESC LIMIT ? OFFSET ?",
            params + [int(page_size or 20), offset],
        ).fetchall()
        return {
            "orders": [dict(r) for r in rows],
            "total": total,
            "page": int(page or 1),
            "page_size": int(page_size or 20),
        }

    def stats(self, pid=None):
        c = self.db.conn()
        sql = """SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status=0 THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN status=1 THEN 1 ELSE 0 END) AS paid,
                    SUM(CASE WHEN status=2 THEN 1 ELSE 0 END) AS expired,
                    COALESCE(SUM(CASE WHEN status=1 THEN price ELSE 0 END), 0) AS total_revenue
                 FROM codepay_orders"""
        params = ()
        if pid:
            sql += " WHERE pid = ?"
            params = (pid,)
        row = c.execute(sql, params).fetchone()
        d = dict(row) if row else {}
        for k in ("total", "pending", "paid", "expired"):
            d[k] = int(d.get(k) or 0)
        d["total_revenue"] = float(d.get("total_revenue") or 0)
        return d

    # ---------- 维护 ----------

    def expire_stale(self, timeout_seconds=300):
        """把超时未支付订单标记为过期；返回被标记的订单列表。"""
        cutoff = (datetime.now() - timedelta(seconds=int(timeout_seconds))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        c = self.db.conn()
        rows = c.execute(
            "SELECT * FROM codepay_orders WHERE status = 0 AND add_time < ?", (cutoff,)
        ).fetchall()
        if not rows:
            return []
        with self.db.write_lock:
            c.execute(
                "UPDATE codepay_orders SET status = 2 WHERE status = 0 AND add_time < ?",
                (cutoff,),
            )
            c.commit()
        return [dict(r) for r in rows]

    def allocate_unique_amount(self, original_amount, offset=0.01,
                               timeout_seconds=300, max_attempts=100):
        start_time = (datetime.now() - timedelta(seconds=timeout_seconds)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        amount = round(float(original_amount), 2)
        c = self.db.conn()
        for _ in range(max_attempts):
            exists = c.execute(
                """SELECT id FROM codepay_orders
                   WHERE payment_amount = ? AND status = 0 AND add_time >= ? LIMIT 1""",
                (amount, start_time),
            ).fetchone()
            if not exists:
                return amount
            amount = round(amount + float(offset), 2)
        raise Exception("无法分配唯一的支付金额，请稍后重试")

    # ---------- 回调状态 ----------
    # 0 未回调 / 1 成功 / 2 待重试 / 3 无需回调 / 4 重试耗尽

    def mark_notify_done(self, order_id):
        c = self.db.conn()
        with self.db.write_lock:
            c.execute(
                """UPDATE codepay_orders
                   SET notify_status = 1, notify_attempts = notify_attempts + 1,
                       notify_last_error = '', notify_next_at = NULL
                   WHERE id = ?""",
                (order_id,),
            )
            c.commit()

    def mark_notify_skipped(self, order_id):
        c = self.db.conn()
        with self.db.write_lock:
            c.execute(
                """UPDATE codepay_orders
                   SET notify_status = 3, notify_last_error = '', notify_next_at = NULL
                   WHERE id = ?""",
                (order_id,),
            )
            c.commit()

    def schedule_notify_retry(self, order_id, error, max_attempts=5) -> int:
        c = self.db.conn()
        row = c.execute(
            "SELECT notify_attempts FROM codepay_orders WHERE id = ?", (order_id,)
        ).fetchone()
        attempts = int(row["notify_attempts"] if row else 0) + 1
        with self.db.write_lock:
            if attempts >= max_attempts:
                c.execute(
                    """UPDATE codepay_orders
                       SET notify_status = 4, notify_attempts = ?,
                           notify_last_error = ?, notify_next_at = NULL
                       WHERE id = ?""",
                    (attempts, (error or "")[:255], order_id),
                )
            else:
                delay = [60, 300, 900, 1800, 3600][min(attempts - 1, 4)]
                next_at = (datetime.now() + timedelta(seconds=delay)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                c.execute(
                    """UPDATE codepay_orders
                       SET notify_status = 2, notify_attempts = ?,
                           notify_last_error = ?, notify_next_at = ?
                       WHERE id = ?""",
                    (attempts, (error or "")[:255], next_at, order_id),
                )
            c.commit()
        return attempts

    def find_due_notifications(self, limit=20):
        c = self.db.conn()
        rows = c.execute(
            """SELECT * FROM codepay_orders
               WHERE status = 1 AND notify_status = 2
                 AND notify_next_at IS NOT NULL AND notify_next_at <= ?
               ORDER BY notify_next_at ASC LIMIT ?""",
            (now_str(), max(1, min(int(limit or 20), 100))),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 批量操作 ----------

    @staticmethod
    def _clean_ids(ids):
        out, seen = [], set()
        for x in ids or []:
            val = str(x or "").strip()
            if not val or val in seen:
                continue
            seen.add(val)
            out.append(val)
        return out

    def delete_many(self, ids) -> int:
        cleaned = self._clean_ids(ids)
        if not cleaned:
            return 0
        c = self.db.conn()
        placeholders = ",".join("?" for _ in cleaned)
        with self.db.write_lock:
            cur = c.execute(
                f"DELETE FROM codepay_orders WHERE id IN ({placeholders})", cleaned
            )
            c.commit()
            return cur.rowcount or 0

    def set_status_many(self, ids, status) -> int:
        cleaned = self._clean_ids(ids)
        if not cleaned:
            return 0
        c = self.db.conn()
        placeholders = ",".join("?" for _ in cleaned)
        with self.db.write_lock:
            cur = c.execute(
                f"UPDATE codepay_orders SET status = ? WHERE id IN ({placeholders})",
                [int(status)] + cleaned,
            )
            c.commit()
            return cur.rowcount or 0


def gen_trade_no() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S") + str(random.randint(1, 999999)).zfill(6)
