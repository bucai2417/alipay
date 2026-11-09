"""码支付协议接口与支付页。"""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, redirect, render_template, request, send_file, url_for

from alipay_pay.codepay import CodePay, resolve_qr_path
from alipay_pay.monitor import request_quick_check
from alipay_pay.orders import OrderStore
from alipay_pay.qr import generate_qr_base64
from alipay_pay.transfer import AlipayTransfer
from core.logger import get_logger
from core.settings import is_configured, load_alipay_config, payment_readiness

bp = Blueprint("codepay", __name__)
logger = get_logger("web.codepay")


def _payment_view(order: dict, cfg: dict) -> dict:
    """构造支付页展示数据（数据库不存历史二维码，按需重建）。"""
    view = {
        "trade_no": order["id"],
        "out_trade_no": order["out_trade_no"],
        "name": order["name"],
        "money": f"{float(order['price']):.2f}",
        "payment_amount": f"{float(order.get('payment_amount') or order['price']):.2f}",
        "status": int(order["status"]),
        "business_qr_mode": bool((cfg.get("business_qr_mode") or {}).get("enabled")),
        "qr_code": "",
        "qr_code_url": "",
        "payment_url": "",
        "payment_instruction": "",
    }
    if view["status"] != 0:
        return view

    if view["business_qr_mode"]:
        view["qr_code_url"] = url_for("codepay.qrcode_image")
        view["payment_instruction"] = f"请使用支付宝扫码，支付精确金额 {view['payment_amount']} 元"
        qr_path = (cfg.get("business_qr_mode") or {}).get("qr_code_path") or ""
        full = resolve_qr_path(qr_path)
        if qr_path and os.path.exists(full):
            import base64
            try:
                with open(full, "rb") as f:
                    view["qr_code"] = base64.b64encode(f.read()).decode("utf-8")
            except Exception:
                pass
        if not view["qr_code"]:
            view["payment_instruction"] = "经营码二维码未上传或文件不存在，请联系管理员处理"
    else:
        try:
            link = AlipayTransfer(cfg).create_order(
                order["out_trade_no"],
                float(order.get("payment_amount") or order["price"]),
                order["name"],
            )
            view["payment_url"] = link
            view["qr_code"] = generate_qr_base64(link) or ""
            view["payment_instruction"] = "请使用支付宝扫码完成转账，备注会自动填写"
        except Exception as e:
            view["payment_instruction"] = f"生成支付链接失败: {e}"
    return view


@bp.route("/api/codepay", methods=["GET", "POST"])
@bp.route("/submit.php", methods=["GET", "POST"])
@bp.route("/api.php", methods=["GET", "POST"])
def codepay_api():
    """CodePay 兼容协议端点（含 /submit.php、/api.php 兼容路径）。"""
    cfg = load_alipay_config()
    ready, reason = payment_readiness(cfg)
    if not ready:
        return jsonify({"code": -1, "msg": f"支付未就绪: {reason}"}), 503

    codepay = CodePay(cfg)

    if request.method == "GET":
        params = dict(request.args)
    else:
        params = dict(request.form)
        if not params:
            params = request.get_json(silent=True) or {}

    action = params.get("act") or params.get("action", "")
    if request.method == "POST" and not action:
        action = "submit"
    if not action:
        return jsonify({"code": -1, "msg": "缺少 action 参数"}), 400

    if action == "query":
        pid, key = params.get("pid", ""), params.get("key", "")
        if not pid or not key:
            return jsonify({"code": -1, "msg": "缺少 pid/key"}), 400
        return jsonify(codepay.query_merchant(pid, key))

    if action == "order":
        pid = params.get("pid", "")
        out_trade_no = params.get("out_trade_no", "")
        if not pid or not out_trade_no:
            return jsonify({"code": -1, "msg": "缺少参数"}), 400
        result = codepay.query_order(pid, params.get("key", ""), out_trade_no,
                                     validate_key=bool(params.get("key")))
        if result.get("code") == 1 and result.get("status") == 0:
            request_quick_check()
        return jsonify(result)

    if action == "orders":
        pid, key = params.get("pid", ""), params.get("key", "")
        if not pid or not key:
            return jsonify({"code": -1, "msg": "缺少参数"}), 400
        try:
            limit = int(params.get("limit", 20))
        except (TypeError, ValueError):
            limit = 20
        return jsonify(codepay.query_orders(pid, key, limit))

    if action in ("submit", "create"):
        payload = {
            "pid": params.get("pid", ""),
            "type": params.get("type", ""),
            "out_trade_no": params.get("out_trade_no", ""),
            "notify_url": params.get("notify_url", ""),
            "return_url": params.get("return_url", ""),
            "name": params.get("name", ""),
            "money": params.get("money", ""),
            "sitename": params.get("sitename", ""),
            "sign": params.get("sign", ""),
            "sign_type": params.get("sign_type", "MD5"),
        }
        result = codepay.create_payment(payload)
        if params.get("format", "html") != "json" and result.get("code") == 1:
            return redirect(url_for("codepay.pay_page", trade_no=result["trade_no"]))
        return jsonify(result)

    if action == "notify":
        result = codepay.process_notification(params)
        if result.get("code") == 1:
            if params.get("out_trade_no", "").startswith("SHOP_"):
                from shop.service import ShopService
                ShopService().try_fulfill_by_payment(params["out_trade_no"])
            return "success", 200
        return "fail", 400

    if action == "health":
        return jsonify({"code": 1, "msg": "ok"})

    return jsonify({"code": -1, "msg": "未知 action"}), 400


@bp.route("/api/codepay/submit")
def codepay_submit():
    out_trade_no = request.args.get("out_trade_no", "")
    if not out_trade_no:
        return render_template("error.html", title="参数错误", message="缺少订单号"), 400
    order = OrderStore().get_order(out_trade_no=out_trade_no)
    if not order:
        return render_template("error.html", title="订单不存在", message="未找到该订单"), 404
    return redirect(url_for("codepay.pay_page", trade_no=order["id"]))


@bp.route("/api/codepay/status/<out_trade_no>")
def codepay_status(out_trade_no):
    order = OrderStore().get_order(out_trade_no=out_trade_no)
    if not order:
        return jsonify({"code": -1, "msg": "订单不存在"}), 404
    if order["status"] == 0:
        request_quick_check()
    return jsonify({
        "code": 1,
        "status": order["status"],
        "trade_no": order["id"],
        "out_trade_no": order["out_trade_no"],
        "payment_amount": f"{float(order.get('payment_amount') or order['price']):.2f}",
    })


@bp.route("/api/codepay/qrcode")
def qrcode_image():
    cfg = load_alipay_config()
    qr_path = (cfg.get("business_qr_mode") or {}).get("qr_code_path") or ""
    full = resolve_qr_path(qr_path)
    if not qr_path or not os.path.exists(full):
        return jsonify({"code": -1, "msg": "经营码二维码未配置"}), 404
    return send_file(full, mimetype="image/png")


@bp.route("/pay/<trade_no>")
def pay_page(trade_no):
    order = OrderStore().get_order(order_id=trade_no)
    if not order:
        return render_template("error.html", title="订单不存在", message="支付订单不存在或已删除"), 404
    cfg = load_alipay_config()
    view = _payment_view(order, cfg)
    shop_order = None
    if str(order.get("out_trade_no") or "").startswith("SHOP_"):
        from shop.service import ShopService
        shop_order = ShopService().db.get_order_by_payment(order["out_trade_no"])

    remaining = 0
    if view["status"] == 0:
        try:
            from datetime import datetime, timedelta

            created = datetime.strptime(order["add_time"], "%Y-%m-%d %H:%M:%S")
            deadline = created + timedelta(seconds=int(cfg.get("order_timeout") or 300))
            remaining = max(0, int((deadline - datetime.now()).total_seconds()))
        except (ValueError, TypeError):
            remaining = 0
    return render_template("pay.html", payment=view, shop_order=shop_order, remaining=remaining)


@bp.route("/api/pay/status/<trade_no>")
def pay_status(trade_no):
    order = OrderStore().get_order(order_id=trade_no)
    if not order:
        return jsonify({"code": -1, "msg": "订单不存在"}), 404
    if order["status"] == 0:
        request_quick_check()
    shop_order = None
    if str(order.get("out_trade_no") or "").startswith("SHOP_"):
        from shop.service import ShopService
        shop_order = ShopService().db.get_order_by_payment(order["out_trade_no"])
    return jsonify({
        "code": 1,
        "status": order["status"],
        "paid": order["status"] == 1,
        "shop_order_no": (shop_order or {}).get("order_no"),
        "shop_status": (shop_order or {}).get("status"),
        "shop_cards": (shop_order or {}).get("card_payload") if (shop_order or {}).get("status") == "fulfilled" else "",
    })


@bp.route("/health")
def health():
    cfg = load_alipay_config()
    ready, reason = payment_readiness(cfg)
    from core.storage import db

    deps = {}
    for name in ("alipay", "qrcode", "PIL", "cryptography", "requests"):
        try:
            __import__(name)
            deps[name] = "OK"
        except Exception:
            deps[name] = "MISSING"
    try:
        stats = OrderStore().stats()
        database = {"status": "OK", "orders": stats.get("total", 0)}
    except Exception as e:
        database = {"status": f"ERROR: {e}"}

    checks = {
        "configured": is_configured(cfg),
        "payment_ready": ready,
        "payment_reason": reason,
        "business_qr_mode": bool((cfg.get("business_qr_mode") or {}).get("enabled")),
        "dependencies": deps,
        "database": database,
        "db_path": db.db_path,
    }
    return jsonify(checks)
