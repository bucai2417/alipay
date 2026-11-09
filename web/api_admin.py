"""管理后台：页面骨架 + 管理 API（全部需要登录）。"""

from __future__ import annotations

import csv
import io
import json
import os
import time

from flask import (Blueprint, Response, jsonify, render_template, request,
                   send_file, session)

from alipay_pay.codepay import resolve_qr_path
from alipay_pay.monitor import mark_paid, monitoring_status, run_monitoring_cycle
from alipay_pay.orders import OrderStore
from alipay_pay.protocol import generate_sign
from config import BASE_DIR, DATA_DIR
from core import mailer
from core.feedback_files import save_feedback_file
from core.html_utils import clean_description
from core.images import save_image_bytes
from core.reports import pay_orders_for_export, revenue_trend, shop_orders_for_export
from core.settings import (
    change_admin_password,
    ensure_business_qr_dir,
    gen_merchant_credentials,
    get_mcp_settings,
    is_configured,
    load_alipay_config,
    load_email_config,
    load_shop_settings,
    payment_readiness,
    reset_mcp_token,
    save_alipay_config,
    save_email_config,
    save_shop_settings,
    set_mcp_enabled,
    EMAIL_PRESETS,
)
from core.storage import db
from shop.service import ShopService
from web.auth import login_required
from web.helpers import fail, int_arg, json_body, ok

bp = Blueprint("admin", __name__)


# ---------------- 页面 ----------------

@bp.route("/admin/")
@login_required
def dashboard():
    return render_template("admin_dashboard.html", page="dashboard",
                           username=session.get("admin"))


@bp.route("/admin/pay-config")
@login_required
def pay_config_page():
    return render_template("admin_pay_config.html", page="pay_config",
                           username=session.get("admin"))


@bp.route("/admin/orders")
@login_required
def orders_page():
    return render_template("admin_orders.html", page="orders",
                           username=session.get("admin"))


@bp.route("/admin/products")
@login_required
def products_page():
    return render_template("admin_products.html", page="products",
                           username=session.get("admin"))


@bp.route("/admin/shop-orders")
@login_required
def shop_orders_page():
    return render_template("admin_shop_orders.html", page="shop_orders",
                           username=session.get("admin"))


@bp.route("/admin/feedback")
@login_required
def feedback_page():
    return render_template("admin_feedback.html", page="feedback",
                           username=session.get("admin"))


@bp.route("/admin/settings")
@login_required
def settings_page():
    return render_template("admin_settings.html", page="settings",
                           username=session.get("admin"))


@bp.route("/admin/api-docs")
@login_required
def api_docs_page():
    cfg = load_alipay_config()
    ready, reason = payment_readiness(cfg)
    return render_template(
        "admin_api_docs.html",
        page="api_docs",
        username=session.get("admin"),
        base_url=request.host_url.rstrip("/"),
        app_id=cfg.get("app_id", ""),
        merchant_id=cfg.get("merchant_id", ""),
        merchant_key=cfg.get("merchant_key", ""),
        merchant_ready=bool(cfg.get("merchant_id") and cfg.get("merchant_key")),
        payment_ready=ready,
        payment_reason=reason,
        business_qr_mode=bool((cfg.get("business_qr_mode") or {}).get("enabled")),
        order_timeout=int(cfg.get("order_timeout") or 300),
    )


# ---------------- 概览 / 健康 ----------------

@bp.route("/api/admin/stats")
@login_required
def stats():
    pay_stats = OrderStore().stats()
    shop_stats = ShopService().get_stats()
    feedback = ShopService().feedback_stats()
    cfg = load_alipay_config()
    ready, reason = payment_readiness(cfg)
    return jsonify({
        "pay": pay_stats,
        "shop": shop_stats,
        "feedback": feedback,
        "monitor": monitoring_status(),
        "payment_ready": ready,
        "payment_reason": reason,
        "configured": is_configured(cfg),
    })


@bp.route("/api/admin/health")
@login_required
def health_detail():
    cfg = load_alipay_config()
    checks = {"dependencies": {}, "config": {}, "overall": True}

    for name, label in (("alipay", "alipay-sdk-python"), ("qrcode", "qrcode"),
                        ("PIL", "Pillow"), ("cryptography", "cryptography"),
                        ("rsa", "rsa"), ("requests", "requests")):
        try:
            __import__(name)
            checks["dependencies"][label] = "OK"
        except Exception:
            checks["dependencies"][label] = "MISSING"
            checks["overall"] = False

    checks["config"]["app_id"] = "OK" if cfg.get("app_id") else "MISSING"
    checks["config"]["private_key"] = (
        "OK" if cfg.get("private_key") and len(cfg["private_key"]) > 20 else "MISSING"
    )
    checks["config"]["alipay_public_key"] = (
        "OK" if cfg.get("alipay_public_key") and len(cfg["alipay_public_key"]) > 20 else "MISSING"
    )
    checks["config"]["merchant_id"] = "OK" if cfg.get("merchant_id") else "MISSING"
    checks["config"]["merchant_key"] = "OK" if cfg.get("merchant_key") else "MISSING"

    if cfg.get("private_key"):
        try:
            from alipay_pay.client import AlipayClient

            normalized = AlipayClient.normalize_private_key(cfg["private_key"])
            if "BEGIN RSA PRIVATE KEY" in normalized:
                checks["config"]["private_key_format"] = "PKCS#1 (OK)"
            elif "BEGIN PRIVATE KEY" in normalized:
                checks["config"]["private_key_format"] = "PKCS#8 (已兼容)"
            else:
                checks["config"]["private_key_format"] = "UNKNOWN"
        except Exception as e:
            checks["config"]["private_key_format"] = f"ERROR: {e}"
            checks["overall"] = False

    qr_path = (cfg.get("business_qr_mode") or {}).get("qr_code_path") or ""
    if qr_path:
        checks["config"]["qr_code"] = "OK" if os.path.exists(resolve_qr_path(qr_path)) else "NOT FOUND"
    else:
        checks["config"]["qr_code"] = "NOT CONFIGURED"

    checks["payment_ready"], checks["payment_reason"] = payment_readiness(cfg)
    checks["monitor"] = monitoring_status()
    checks["db_path"] = db.db_path
    try:
        checks["database"] = {"status": "OK", **OrderStore().stats()}
    except Exception as e:
        checks["database"] = {"status": f"ERROR: {e}"}
        checks["overall"] = False
    return jsonify(checks)


@bp.route("/api/admin/monitor/run", methods=["POST"])
@login_required
def monitor_run():
    result = run_monitoring_cycle(cleanup=False)
    return jsonify({"success": True, "result": result})


# ---------------- 支付配置 ----------------

@bp.route("/api/admin/pay-config", methods=["GET"])
@login_required
def pay_config_get():
    cfg = load_alipay_config()
    flat = {k: cfg.get(k, "") for k in (
        "app_id", "private_key", "alipay_public_key", "transfer_user_id",
        "merchant_id", "merchant_key", "server_url", "sign_type",
        "order_timeout", "monitor_interval", "query_minutes_back",
    )}
    qr = cfg.get("business_qr_mode") or {}
    flat.update({
        "business_qr_mode": bool(qr.get("enabled")),
        "qr_code_path": qr.get("qr_code_path", ""),
        "amount_offset": qr.get("amount_offset", 0.01),
        "match_tolerance": qr.get("match_tolerance", 300),
    })
    pp = cfg.get("pushplus") or {}
    flat.update({
        "pushplus_enabled": bool(pp.get("enabled")),
        "pushplus_token": pp.get("token", ""),
        "pushplus_template": pp.get("template", "markdown"),
        "pushplus_title_prefix": pp.get("title_prefix", "码支付"),
    })
    return jsonify(flat)


@bp.route("/api/admin/pay-config", methods=["POST"])
@login_required
def pay_config_save():
    """增量保存：只更新请求中出现的字段，未提交的字段保持原值。"""
    data = json_body()
    if not data:
        return fail("无数据")

    patch = {}
    for key in ("app_id", "transfer_user_id", "merchant_id",
                "server_url", "sign_type", "charset"):
        if key in data:
            patch[key] = str(data.get(key) or "").strip()
    for key in ("private_key", "alipay_public_key", "merchant_key"):
        if key in data:
            val = str(data.get(key) or "")
            if val.strip():  # 留空表示保持原值
                patch[key] = val
    for key in ("order_timeout", "monitor_interval", "query_minutes_back"):
        if key in data:
            patch[key] = data.get(key)

    if "business_qr_mode" in data:
        qr_patch = {"enabled": bool(data.get("business_qr_mode"))}
        if "qr_code_path" in data:
            qr_patch["qr_code_path"] = str(data.get("qr_code_path") or "").strip()
        if "amount_offset" in data:
            qr_patch["amount_offset"] = data.get("amount_offset")
        if "match_tolerance" in data:
            qr_patch["match_tolerance"] = data.get("match_tolerance")
        patch["business_qr_mode"] = qr_patch

    pp_keys = ("pushplus_enabled", "pushplus_token",
               "pushplus_template", "pushplus_title_prefix")
    if any(k in data for k in pp_keys):
        pp = {}
        if "pushplus_enabled" in data:
            pp["enabled"] = bool(data.get("pushplus_enabled"))
        if "pushplus_token" in data:
            token = str(data.get("pushplus_token") or "")
            if token.strip():
                pp["token"] = token
        if "pushplus_template" in data:
            pp["template"] = str(data.get("pushplus_template") or "markdown")
        if "pushplus_title_prefix" in data:
            pp["title_prefix"] = str(data.get("pushplus_title_prefix") or "码支付")
        patch["pushplus"] = pp

    save_alipay_config(patch)
    return ok(message="配置已保存")


@bp.route("/api/admin/generate-merchant", methods=["POST"])
@login_required
def generate_merchant():
    data = json_body()
    app_id = str(data.get("app_id") or "").strip()
    if not app_id:
        return fail("缺少 AppId")
    creds = gen_merchant_credentials(app_id)
    return ok(creds)


@bp.route("/api/admin/upload-qr", methods=["POST"])
@login_required
def upload_qr():
    f = request.files.get("file")
    if not f or not f.filename:
        return fail("未选择文件")
    if not f.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")):
        return fail("不支持的图片格式")
    ext = os.path.splitext(f.filename)[1].lower() or ".png"
    save_dir = ensure_business_qr_dir()
    filename = f"business_qr{ext}"
    save_path = os.path.abspath(os.path.join(save_dir, filename))
    f.save(save_path)
    # 保存绝对路径，兼容自定义 ALIPAY_DATA_DIR；迁移服务器后需重新上传
    save_alipay_config({"business_qr_mode": {
        "qr_code_path": save_path,
        "enabled": True,
    }})
    return ok(path=save_path, filename=filename)


@bp.route("/api/admin/qr-image")
@login_required
def qr_image():
    path = request.args.get("path", "")
    if not path:
        return "", 404
    full = resolve_qr_path(path)
    allowed_roots = {os.path.abspath(DATA_DIR), os.path.abspath(os.path.join(BASE_DIR, "data"))}
    try:
        if not any(os.path.commonpath([os.path.abspath(full), root]) == root for root in allowed_roots):
            return "", 403
    except ValueError:
        return "", 403
    if not os.path.exists(full):
        return "", 404
    return send_file(full)


# ---------------- 码支付订单 ----------------

@bp.route("/api/admin/orders")
@login_required
def admin_orders():
    return jsonify(OrderStore().paginated(
        status=request.args.get("status", "") if request.args.get("status") not in (None, "") else None,
        page=int_arg("page", 1, 1),
        page_size=int_arg("page_size", 20, 1, 100),
        search=request.args.get("search") or None,
    ))


@bp.route("/api/admin/orders/<order_id>/status", methods=["POST"])
@login_required
def admin_order_status(order_id):
    data = json_body()
    try:
        new_status = int(data.get("status", 0))
    except (TypeError, ValueError):
        return fail("状态无效")
    store = OrderStore()
    order = store.get_order(order_id=order_id)
    if not order:
        return fail("订单不存在", 404)
    if new_status == 1:
        mark_paid(order_id, detected_by="admin")
    else:
        store.update_order_status(order_id, new_status)
    return ok(message="状态已更新")


@bp.route("/api/admin/orders/<order_id>/delete", methods=["POST"])
@login_required
def admin_order_delete(order_id):
    OrderStore().delete_order(order_id)
    return ok(message="已删除")


# ---------------- 商品 / 卡密 ----------------

@bp.route("/api/admin/products", methods=["GET", "POST"])
@login_required
def admin_products():
    svc = ShopService()
    if request.method == "GET":
        return jsonify(svc.list_products(
            only_on_sale=request.args.get("only_on_sale") in ("1", "true"),
            page=int_arg("page", 1, 1),
            page_size=int_arg("page_size", 50, 1, 100),
            search=request.args.get("search") or None,
        ))
    data = json_body()
    name = str(data.get("name") or "").strip()
    if not name:
        return fail("商品名称必填")
    try:
        price = float(data.get("price"))
    except (TypeError, ValueError):
        return fail("价格无效")
    if price < 0.01:
        return fail("价格至少 0.01")
    fulfill_mode = data.get("fulfill_mode") or "stock"
    if fulfill_mode not in ("stock", "generate"):
        return fail("发货模式无效")
    product = svc.create_product(
        name=name,
        description=clean_description(data.get("description")),
        price=price,
        fulfill_mode=fulfill_mode,
        generate_config=data.get("generate_config") or {},
        status=int(data.get("status", 1)),
        sort_order=int(data.get("sort_order") or 0),
        icon_path=str(data.get("icon_path") or "").strip(),
    )
    return ok(product=product)


@bp.route("/api/admin/products/<int:product_id>", methods=["GET", "PUT", "DELETE"])
@login_required
def admin_product_detail(product_id):
    svc = ShopService()
    if request.method == "GET":
        product = svc.get_product(product_id)
        if not product:
            return fail("商品不存在", 404)
        return ok(product=product)
    if request.method == "DELETE":
        result = svc.delete_product(product_id)
        if not result.get("ok"):
            return fail(result.get("msg", "删除失败"))
        return ok(message="已删除")
    data = json_body()
    fields = {}
    for k in ("name", "fulfill_mode", "generate_config"):
        if k in data:
            fields[k] = data[k]
    if "description" in data:
        fields["description"] = clean_description(data.get("description"))
    if "icon_path" in data:
        fields["icon_path"] = str(data.get("icon_path") or "").strip()
    if "price" in data:
        fields["price"] = float(data["price"])
    if "status" in data:
        fields["status"] = int(data["status"])
    if "sort_order" in data:
        fields["sort_order"] = int(data["sort_order"])
    product = svc.update_product(product_id, **fields)
    if not product:
        return fail("商品不存在", 404)
    return ok(product=product)


@bp.route("/api/admin/products/<int:product_id>/cards")
@login_required
def admin_cards(product_id):
    return jsonify(ShopService().list_cards(
        product_id,
        status=request.args.get("status") or None,
        page=int_arg("page", 1, 1),
        page_size=int_arg("page_size", 50, 1, 100),
        search=request.args.get("search") or None,
    ))


@bp.route("/api/admin/products/<int:product_id>/cards/import", methods=["POST"])
@login_required
def admin_cards_import(product_id):
    data = json_body()
    raw = data.get("codes") or data.get("text") or ""
    codes = raw if isinstance(raw, list) else str(raw).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    result = ShopService().import_cards(product_id, codes)
    if not result.get("ok"):
        return fail(result.get("msg", "导入失败"))
    return ok(result)


@bp.route("/api/admin/cards/<int:card_id>/status", methods=["POST"])
@login_required
def admin_card_status(card_id):
    data = json_body()
    status = str(data.get("status") or data.get("action") or "").lower()
    svc = ShopService()
    if status in ("available", "enable", "enabled"):
        card = svc.enable_card(card_id)
        action = "enable"
    elif status in ("disabled", "disable"):
        card = svc.disable_card(card_id)
        action = "disable"
    else:
        return fail("status 仅支持 available / disabled")
    if not card:
        return fail("卡密不存在", 404)
    product = svc.get_product(card.get("product_id")) if card.get("product_id") else {}
    return ok(card=card, action=action, stock_count=int((product or {}).get("stock_count") or 0))


@bp.route("/api/admin/cards/<int:card_id>", methods=["DELETE"])
@login_required
def admin_card_delete(card_id):
    result = ShopService().delete_card(card_id)
    if not result:
        return fail("卡密不存在", 404)
    if not result.get("ok"):
        return fail(result.get("msg", "删除失败"))
    return ok(stock_count=result.get("stock_count", 0), product_id=result.get("product_id"))


@bp.route("/api/admin/products/<int:product_id>/cards/batch", methods=["POST"])
@login_required
def admin_cards_batch(product_id):
    data = json_body()
    action = str(data.get("action") or "").lower()
    ids = data.get("ids") or data.get("card_ids") or []
    if not isinstance(ids, list):
        ids = [ids]
    result = ShopService().batch_cards(product_id, action, ids)
    if not result.get("ok"):
        return fail(result.get("msg", "批量操作失败"))
    return ok(result)


# ---------------- 商城订单 ----------------

@bp.route("/api/admin/shop-orders")
@login_required
def admin_shop_orders():
    return jsonify(ShopService().list_orders(
        status=request.args.get("status") or None,
        page=int_arg("page", 1, 1),
        page_size=int_arg("page_size", 20, 1, 100),
        search=request.args.get("search") or None,
    ))


@bp.route("/api/admin/shop-orders/<order_no>/refulfill", methods=["POST"])
@login_required
def admin_refulfill(order_no):
    result = ShopService().refulfill(order_no)
    if result.get("code") == 1:
        return ok(result)
    return fail(result.get("msg", "补发失败"))


@bp.route("/api/admin/orders/batch", methods=["POST"])
@login_required
def admin_orders_batch():
    data = json_body()
    action = str(data.get("action") or "").lower()
    ids = data.get("ids") or []
    if not isinstance(ids, list):
        ids = [ids]
    store = OrderStore()

    if action == "delete":
        n = store.delete_many(ids)
        return ok(affected=n, message=f"已删除 {n} 笔订单")

    if action == "expire":
        n = store.set_status_many(ids, 2)
        return ok(affected=n, message=f"已置为过期 {n} 笔")

    if action in ("mark_paid", "repay"):
        changed = 0
        for oid in ids:
            order = store.get_order(order_id=str(oid))
            if order and order["status"] == 0:
                if mark_paid(str(oid), detected_by="admin_batch").get("changed"):
                    changed += 1
        return ok(affected=changed, message=f"补单成功 {changed} 笔")

    return fail("不支持的批量操作")


@bp.route("/api/admin/orders/<order_id>/notify", methods=["POST"])
@login_required
def admin_order_notify(order_id):
    """手动重发商户回调（补单/排障时使用）。"""
    from alipay_pay.codepay import CodePay

    store = OrderStore()
    order = store.get_order(order_id=order_id)
    if not order:
        return fail("订单不存在", 404)
    if order["status"] != 1:
        return fail("订单未支付，无法发送回调")

    result = CodePay().send_notification(order)
    fresh = store.get_order(order_id=order_id)
    if result.get("ok"):
        return ok(result=result, order=fresh,
                  message="无需回调" if result.get("skipped") else "回调发送成功")
    return fail(result.get("error") or "回调发送失败", order=fresh, result=result)


@bp.route("/api/admin/shop-orders/batch", methods=["POST"])
@login_required
def admin_shop_orders_batch():
    data = json_body()
    action = str(data.get("action") or "").lower()
    order_nos = data.get("ids") or data.get("order_nos") or []
    if not isinstance(order_nos, list):
        order_nos = [order_nos]

    svc = ShopService()

    if action == "delete":
        n = svc.delete_orders_many(order_nos)
        return ok(affected=n, message=f"已删除 {n} 笔商城订单")

    if action == "mark_paid":
        success, failed = 0, []
        for no in order_nos:
            result = _shop_mark_paid(svc, str(no))
            if result.get("ok"):
                success += 1
            else:
                failed.append({"order_no": no, "reason": result.get("reason")})
        return ok(affected=success, failed=failed,
                  message=f"补单成功 {success} 笔" + (f"，失败 {len(failed)} 笔" if failed else ""))

    if action == "refulfill":
        success, failed = 0, []
        for no in order_nos:
            result = svc.refulfill(str(no))
            if result.get("code") == 1:
                success += 1
            else:
                failed.append({"order_no": no, "reason": result.get("msg")})
        return ok(affected=success, failed=failed,
                  message=f"补发成功 {success} 笔" + (f"，失败 {len(failed)} 笔" if failed else ""))

    return fail("不支持的批量操作")


def _shop_mark_paid(svc: ShopService, order_no: str) -> dict:
    """商城订单补单（复用 ShopService 逻辑）。"""
    return svc.mark_paid_by_order_no(order_no)


@bp.route("/api/admin/products/batch", methods=["POST"])
@login_required
def admin_products_batch():
    data = json_body()
    action = str(data.get("action") or "").lower()
    ids = data.get("ids") or []
    if not isinstance(ids, list):
        ids = [ids]
    svc = ShopService()

    if action == "delete":
        result = svc.delete_products_many(ids)
        return ok(deleted=result.get("deleted", 0), skipped=result.get("skipped", []),
                  message=f"已删除 {result.get('deleted', 0)} 个商品")
    if action in ("on_sale", "off_sale"):
        n = svc.set_products_status_many(ids, 1 if action == "on_sale" else 0)
        return ok(affected=n, message=("已上架" if action == "on_sale" else "已下架") + f" {n} 个商品")
    return fail("不支持的批量操作")


@bp.route("/api/admin/feedback/tickets/batch", methods=["POST"])
@login_required
def admin_tickets_batch():
    data = json_body()
    action = str(data.get("action") or "").lower()
    ids = data.get("ids") or []
    if not isinstance(ids, list):
        ids = [ids]
    svc = ShopService()

    if action == "delete":
        n = svc.delete_tickets_many(ids)
        return ok(affected=n, message=f"已删除 {n} 个工单")
    if action in ("close", "open"):
        n = svc.set_tickets_status_many(ids, "closed" if action == "close" else "open")
        return ok(affected=n, message=f"已{'关闭' if action == 'close' else '开启'} {n} 个工单")
    return fail("不支持的批量操作")


@bp.route("/api/admin/shop-orders/<order_no>/mark-paid", methods=["POST"])
@login_required
def admin_shop_mark_paid(order_no):
    """线下收款/外部支付后，手动补单并发货。"""
    svc = ShopService()
    result = _shop_mark_paid(svc, order_no)
    if not result.get("ok"):
        return fail(result.get("reason", "补单失败"))
    return ok(order=result.get("order"), message="补单成功，已标记支付并发货")


# ---------------- 客服 ----------------

@bp.route("/api/admin/feedback/stats")
@login_required
def admin_feedback_stats():
    return jsonify(ShopService().feedback_stats())


@bp.route("/api/admin/feedback/tickets")
@login_required
def admin_feedback_tickets():
    result = ShopService().admin_list_tickets(
        status=request.args.get("status") or None,
        page=int_arg("page", 1, 1),
        page_size=int_arg("page_size", 20, 1, 100),
        search=request.args.get("search") or None,
        unread_only=request.args.get("unread") in ("1", "true"),
    )
    return jsonify(result)


@bp.route("/api/admin/feedback/tickets/<int:ticket_id>")
@login_required
def admin_feedback_ticket(ticket_id):
    mark = request.args.get("mark_read", "1") not in ("0", "false")
    result = ShopService().admin_get_ticket(ticket_id, mark_read=mark)
    return jsonify(result), (200 if result.get("code") == 1 else 404)


@bp.route("/api/admin/feedback/upload", methods=["POST"])
@login_required
def admin_feedback_upload():
    """客服回复附件上传（图片/文档，最大 10MB）。"""
    f = request.files.get("file")
    if not f or not f.filename:
        return fail("未选择文件")
    result, error = save_feedback_file(f.read(), f.filename)
    if error:
        return fail(error)
    return ok(attachment=result)


@bp.route("/api/admin/feedback/notify", methods=["GET", "POST"])
@login_required
def admin_feedback_notify():
    svc = ShopService()
    if request.method == "GET":
        return ok(**svc.admin_notify_settings())
    data = json_body()
    result = svc.admin_update_notify_settings(
        data.get("email") or "", bool(data.get("enabled"))
    )
    if result.get("code") != 1:
        return fail(result.get("msg", "保存失败"))
    return ok(**{k: v for k, v in result.items() if k not in ("code", "msg")})


@bp.route("/api/admin/feedback/tickets/<int:ticket_id>/reply", methods=["POST"])
@login_required
def admin_feedback_reply(ticket_id):
    data = json_body()
    result = ShopService().admin_reply_ticket(
        ticket_id,
        data.get("content") or "",
        attachment=data.get("attachment"),
        base_url=request.host_url,
    )
    return jsonify(result), (200 if result.get("code") == 1 else 400)


@bp.route("/api/admin/feedback/tickets/<int:ticket_id>/status", methods=["POST"])
@login_required
def admin_feedback_status(ticket_id):
    result = ShopService().admin_set_ticket_status(ticket_id, json_body().get("status") or "")
    return jsonify(result), (200 if result.get("code") == 1 else 400)


# ---------------- 商城设置 / 账号 ----------------

@bp.route("/api/admin/shop-settings", methods=["GET", "POST"])
@login_required
def admin_shop_settings():
    if request.method == "GET":
        return ok(settings=load_shop_settings())
    return ok(settings=save_shop_settings(json_body()))


@bp.route("/api/admin/upload-image", methods=["POST"])
@login_required
def upload_image():
    """商品图标 / 描述图片上传，返回可公开访问的 /uploads/<file> 地址。"""
    f = request.files.get("file")
    if not f or not f.filename:
        return fail("未选择文件")
    result, error = save_image_bytes(f.read())
    if error:
        return fail(error)
    return ok(url=result["url"], filename=result["filename"])


@bp.route("/api/admin/email-config", methods=["GET", "POST"])
@login_required
def admin_email_config():
    if request.method == "GET":
        return ok(config=load_email_config(), presets=EMAIL_PRESETS)
    cfg = save_email_config(json_body())
    return ok(config=cfg, message="邮件配置已保存")


@bp.route("/api/admin/email-test", methods=["POST"])
@login_required
def admin_email_test():
    data = json_body()
    to = str(data.get("to") or "").strip()
    if not mailer.is_email(to):
        return fail("收件邮箱格式不正确")
    cfg = load_email_config()
    title = load_shop_settings().get("shop_title") or "卡密商城"
    subject, html_body, text_body = mailer.test_email_content(title)
    ok_flag, error = mailer.send_email(cfg, to, subject, html_body, text_body)
    if ok_flag:
        return ok(message="测试邮件已发送")
    return fail(f"发送失败: {error}")


@bp.route("/api/admin/mcp-info")
@login_required
def admin_mcp_info():
    from web.mcp_server import RESOURCES, TOOLS

    cfg = get_mcp_settings()
    return ok(
        enabled=cfg["enabled"],
        token=cfg["token"],
        url=request.host_url.rstrip("/") + "/mcp",
        tools=len(TOOLS),
        resources=len(RESOURCES),
    )


@bp.route("/api/admin/mcp-token/reset", methods=["POST"])
@login_required
def admin_mcp_token_reset():
    cfg = reset_mcp_token()
    return ok(enabled=cfg["enabled"], token=cfg["token"], message="MCP Token 已重置")


@bp.route("/api/admin/mcp-enabled", methods=["POST"])
@login_required
def admin_mcp_enable():
    data = json_body()
    cfg = set_mcp_enabled(bool(data.get("enabled")))
    return ok(enabled=cfg["enabled"])


@bp.route("/api/admin/password", methods=["POST"])
@login_required
def admin_password():
    data = json_body()
    ok_flag, msg = change_admin_password(
        data.get("old_password") or "", data.get("new_password") or ""
    )
    if not ok_flag:
        return fail(msg)
    return ok(message=msg)


# ---------------- 趋势 / 导出 / 开发接口调试 ----------------

@bp.route("/api/admin/stats/trend")
@login_required
def admin_trend():
    days = int_arg("days", 7, 1, 90)
    items = revenue_trend(days)
    return jsonify({
        "days": days,
        "items": items,
        "total_amount": round(sum(i["total_amount"] for i in items), 2),
        "total_count": sum(i["total_count"] for i in items),
    })


def _csv_response(rows, headers, filename):
    buf = io.StringIO()
    buf.write("﻿")
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return Response(
        buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.route("/api/admin/orders/export")
@login_required
def admin_orders_export():
    status = request.args.get("status", "")
    search = request.args.get("search") or None
    rows = pay_orders_for_export(
        status=status if status != "" else None, search=search
    )
    status_text = {0: "待支付", 1: "已支付", 2: "已过期"}
    data = [[
        r["id"], r["out_trade_no"], r["name"], f"{float(r['price']):.2f}",
        f"{float(r.get('payment_amount') or 0):.2f}",
        status_text.get(r["status"], r["status"]),
        r["add_time"], r.get("pay_time") or "",
    ] for r in rows]
    return _csv_response(
        data,
        ["平台订单号", "商户单号", "商品", "金额", "实付", "状态", "创建时间", "支付时间"],
        "codepay_orders.csv",
    )


@bp.route("/api/admin/shop-orders/export")
@login_required
def admin_shop_orders_export():
    status = request.args.get("status") or None
    search = request.args.get("search") or None
    rows = shop_orders_for_export(status=status, search=search)
    status_text = {"pending": "待支付", "paid": "已支付", "fulfilled": "已发货",
                   "expired": "已过期", "failed": "发货失败"}
    data = [[
        r["order_no"], r["product_name_snapshot"], r["qty"],
        f"{float(r['price']) * int(r['qty']):.2f}",
        r.get("contact") or "", r["payment_out_trade_no"],
        status_text.get(r["status"], r["status"]),
        r["created_at"], r.get("paid_at") or "", r.get("fulfilled_at") or "",
        (r.get("card_payload") or "").replace("\n", " | "),
        (r.get("fail_reason") or "").replace("\n", " "),
    ] for r in rows]
    return _csv_response(
        data,
        ["订单号", "商品", "数量", "金额", "备注", "支付单号", "状态",
         "创建时间", "支付时间", "发货时间", "卡密", "失败原因"],
        "shop_orders.csv",
    )


@bp.route("/api/admin/api-sign", methods=["POST"])
@login_required
def admin_api_sign():
    """仅生成完整请求参数与签名，不执行任何订单操作。"""
    data = json_body()
    act = str(data.get("act") or "submit")
    params = data.get("params") or {}
    if not isinstance(params, dict):
        return fail("params 必须是对象")

    cfg = load_alipay_config()
    pid = str(cfg.get("merchant_id") or "")
    key = str(cfg.get("merchant_key") or "")
    if not pid or not key:
        return fail("商户凭据未配置，请先到「支付配置」生成并保存商户 ID / 密钥")

    try:
        if act == "query":
            payload = {"pid": pid, "key": key}

        elif act == "order":
            out_trade_no = str(params.get("out_trade_no") or "").strip()
            if not out_trade_no:
                return fail("请填写 out_trade_no")
            payload = {"pid": pid, "out_trade_no": out_trade_no, "key": key}

        elif act == "orders":
            try:
                limit = max(1, min(int(params.get("limit") or 20), 100))
            except (TypeError, ValueError):
                limit = 20
            payload = {"pid": pid, "key": key, "limit": limit}

        elif act in ("submit", "create"):
            payload = {
                "pid": pid,
                "type": "alipay",
                "out_trade_no": str(params.get("out_trade_no")
                                    or f"ORDER{int(time.time())}"),
                "name": str(params.get("name") or "测试商品"),
                "money": str(params.get("money") or "0.01"),
                "notify_url": str(params.get("notify_url") or ""),
                "return_url": "",
                "sitename": str(params.get("sitename") or ""),
            }
            payload["sign"] = generate_sign(payload, key)
            payload["sign_type"] = "MD5"

        elif act == "notify":
            out_trade_no = str(params.get("out_trade_no") or "").strip()
            if not out_trade_no:
                return fail("请填写 out_trade_no")
            order = OrderStore().get_order(out_trade_no=out_trade_no)
            if not order:
                return fail("订单不存在，无法生成通知参数")
            payload = {
                "out_trade_no": order["out_trade_no"],
                "trade_no": order["id"],
                "trade_status": "TRADE_SUCCESS",
                "name": order["name"],
                "money": f"{float(order['price']):.2f}",
            }
            payload["sign"] = generate_sign(payload, key)
            payload["sign_type"] = "MD5"

        else:
            return fail(f"不支持的 act: {act}")

    except Exception as e:
        return fail(f"生成失败: {e}")

    clean = {k: v for k, v in payload.items() if v is not None and str(v) != ""}
    form_string = "&".join(
        [f"act={act}"] + [f"{k}={v}" for k, v in clean.items()]
    )
    json_string = json.dumps({"act": act, **clean}, ensure_ascii=False, indent=2)
    return ok(act=act, form_string=form_string, json=json_string,
              params={"act": act, **clean})


@bp.route("/api/admin/api-try", methods=["POST"])
@login_required
def admin_api_try():
    """接口文档页的在线调试：服务端补签名后执行协议逻辑。

    默认在进程内直接执行；勾选 over_http 时通过 HTTP 请求本服务，
    可验证完整链路（含路由、签名校验、状态码）。
    """
    data = json_body()
    act = str(data.get("act") or "query")
    params = data.get("params") or {}
    over_http = bool(data.get("over_http"))
    if not isinstance(params, dict):
        return fail("params 必须是对象")

    cfg = load_alipay_config()
    pid = str(cfg.get("merchant_id") or "")
    key = str(cfg.get("merchant_key") or "")
    started = time.perf_counter()

    def _elapsed():
        return round((time.perf_counter() - started) * 1000, 1)

    # ---- health ----
    if act == "health":
        request_echo = {"act": "health"}
        if over_http:
            import requests

            url = request.host_url.rstrip("/") + "/health"
            try:
                r = requests.get(url, timeout=15)
                try:
                    resp = r.json()
                except ValueError:
                    resp = {"raw": (r.text or "")[:2000]}
                return ok(act=act, request=request_echo, response=resp,
                          meta={"mode": "http", "method": "GET", "url": url,
                                "status": r.status_code, "elapsed_ms": _elapsed()})
            except Exception as e:
                return fail(f"HTTP 请求失败: {e}", act=act, request=request_echo,
                            meta={"mode": "http", "method": "GET", "url": url})
        ready, reason = payment_readiness(cfg)
        return ok(act=act, request=request_echo,
                  response={
                      "configured": is_configured(cfg),
                      "payment_ready": ready,
                      "payment_reason": reason,
                      "business_qr_mode": bool((cfg.get("business_qr_mode") or {}).get("enabled")),
                  },
                  meta={"mode": "internal", "method": "internal",
                        "elapsed_ms": _elapsed()})

    if not pid or not key:
        return fail("商户凭据未配置，请先到「支付配置」生成并保存商户 ID / 密钥")

    # ---- 构造请求参数 ----
    payload = None
    try:
        if act == "query":
            payload = {"act": "query", "pid": pid, "key": key}

        elif act == "order":
            out_trade_no = str(params.get("out_trade_no") or "").strip()
            if not out_trade_no:
                return fail("请填写 out_trade_no")
            payload = {"act": "order", "pid": pid, "key": key,
                       "out_trade_no": out_trade_no}

        elif act == "orders":
            try:
                limit = max(1, min(int(params.get("limit") or 10), 50))
            except (TypeError, ValueError):
                limit = 10
            payload = {"act": "orders", "pid": pid, "key": key, "limit": limit}

        elif act in ("submit", "create"):
            payload = {
                "act": "submit",
                "pid": pid,
                "type": "alipay",
                "out_trade_no": str(params.get("out_trade_no")
                                    or f"TEST{int(time.time())}"),
                "name": str(params.get("name") or "调试商品"),
                "money": str(params.get("money") or "0.01"),
                "notify_url": str(params.get("notify_url") or ""),
                "return_url": str(params.get("return_url") or ""),
                "sitename": str(params.get("sitename") or ""),
            }
            payload["sign"] = generate_sign(payload, key)
            payload["sign_type"] = "MD5"
            if over_http:
                payload["format"] = "json"

        elif act == "notify":
            out_trade_no = str(params.get("out_trade_no") or "").strip()
            if not out_trade_no:
                return fail("请填写 out_trade_no")
            order = OrderStore().get_order(out_trade_no=out_trade_no)
            if not order:
                return fail("订单不存在，无法生成支付通知")
            payload = {
                "act": "notify",
                "out_trade_no": order["out_trade_no"],
                "trade_no": order["id"],
                "trade_status": "TRADE_SUCCESS",
                "name": order["name"],
                "money": f"{float(order['price']):.2f}",
            }
            payload["sign"] = generate_sign(payload, key)
            payload["sign_type"] = "MD5"

        else:
            return fail(f"不支持的 act: {act}")
    except Exception as e:
        return fail(f"构造请求失败: {e}")

    # ---- HTTP 模式：走真实接口 ----
    if over_http:
        import requests

        url = request.host_url.rstrip("/") + "/api/codepay"
        try:
            r = requests.post(url, data=payload, timeout=20)
        except Exception as e:
            return fail(f"HTTP 请求失败: {e}", act=act, request=payload,
                        meta={"mode": "http", "method": "POST", "url": url})
        try:
            resp = r.json()
        except ValueError:
            text = (r.text or "").strip()
            if text == "success":
                resp = {"code": 1, "msg": "SUCCESS", "raw": text}
            else:
                resp = {"code": -1, "msg": text[:500] or "非 JSON 响应"}
        if isinstance(resp, dict) and resp.get("qr_code"):
            resp["qr_code_preview"] = "data:image/png;base64," + resp.pop("qr_code")
        return ok(act=act, request=payload, response=resp,
                  meta={"mode": "http", "method": "POST", "url": url,
                        "status": r.status_code, "elapsed_ms": _elapsed()})

    # ---- 进程内模式 ----
    from alipay_pay.codepay import CodePay

    codepay = CodePay(cfg)
    try:
        if act == "query":
            response = codepay.query_merchant(pid, key)
        elif act == "order":
            response = codepay.query_order(pid, key, payload["out_trade_no"])
        elif act == "orders":
            response = codepay.query_orders(pid, key, payload["limit"])
        elif act in ("submit", "create"):
            response = codepay.create_payment(payload)
            if response.get("qr_code"):
                response["qr_code_preview"] = "data:image/png;base64," + response.pop("qr_code")
        else:  # notify
            notify_data = {k: v for k, v in payload.items() if k != "act"}
            response = codepay.process_notification(notify_data)
            if response.get("code") == 1 and payload["out_trade_no"].startswith("SHOP_"):
                ShopService().try_fulfill_by_payment(payload["out_trade_no"])
    except Exception as e:
        return fail(f"执行失败: {e}", act=act, request=payload)

    return ok(act=act, request=payload, response=response,
              meta={"mode": "internal", "method": "internal",
                    "elapsed_ms": _elapsed()})
