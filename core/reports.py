"""后台报表：收入趋势与订单导出。"""

from __future__ import annotations

from datetime import datetime, timedelta

from core.storage import db


def revenue_trend(days: int = 7) -> list:
    """近 N 天支付/商城收入与订单数（按自然日聚合）。"""
    days = max(1, min(int(days or 7), 90))
    today = datetime.now().date()
    start = today - timedelta(days=days - 1)
    start_str = start.strftime("%Y-%m-%d")

    c = db.conn()
    pay_rows = c.execute(
        """SELECT substr(COALESCE(pay_time, add_time), 1, 10) AS d,
                  COUNT(*) AS n,
                  COALESCE(SUM(price), 0) AS amount
           FROM codepay_orders
           WHERE status = 1 AND COALESCE(pay_time, add_time) >= ?
           GROUP BY d""",
        (start_str,),
    ).fetchall()
    shop_rows = c.execute(
        """SELECT substr(COALESCE(paid_at, created_at), 1, 10) AS d,
                  COUNT(*) AS n,
                  COALESCE(SUM(price * qty), 0) AS amount
           FROM shop_orders
           WHERE status IN ('paid', 'fulfilled') AND COALESCE(paid_at, created_at) >= ?
           GROUP BY d""",
        (start_str,),
    ).fetchall()

    pay_map = {r["d"]: (int(r["n"]), float(r["amount"])) for r in pay_rows}
    shop_map = {r["d"]: (int(r["n"]), float(r["amount"])) for r in shop_rows}

    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        pn, pa = pay_map.get(d, (0, 0.0))
        sn, sa = shop_map.get(d, (0, 0.0))
        out.append({
            "date": d,
            "pay_count": pn,
            "pay_amount": round(pa, 2),
            "shop_count": sn,
            "shop_amount": round(sa, 2),
            "total_amount": round(pa + sa, 2),
            "total_count": pn + sn,
        })
    return out


def pay_orders_for_export(status=None, search=None, limit=10000) -> list:
    c = db.conn()
    conditions, params = [], []
    if status is not None and status != "":
        conditions.append("status = ?")
        params.append(int(status))
    if search:
        conditions.append("(id LIKE ? OR out_trade_no LIKE ? OR name LIKE ?)")
        kw = f"%{search}%"
        params.extend([kw, kw, kw])
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = c.execute(
        f"SELECT * FROM codepay_orders{where} ORDER BY add_time DESC LIMIT ?",
        params + [int(limit)],
    ).fetchall()
    return [dict(r) for r in rows]


def shop_orders_for_export(status=None, search=None, limit=10000) -> list:
    c = db.conn()
    conditions, params = [], []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if search:
        conditions.append(
            "(order_no LIKE ? OR product_name_snapshot LIKE ? OR contact LIKE ? "
            "OR payment_out_trade_no LIKE ?)"
        )
        params.extend([f"%{search}%"] * 4)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = c.execute(
        f"SELECT * FROM shop_orders{where} ORDER BY id DESC LIMIT ?",
        params + [int(limit)],
    ).fetchall()
    return [dict(r) for r in rows]
