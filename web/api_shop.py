"""商城前台页面与公开 API。"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from core.feedback_files import save_feedback_file
from core.settings import load_shop_settings
from shop.guard import guard
from shop.service import ShopService
from web.helpers import client_ip, json_body

bp = Blueprint("shop", __name__)


def _token_ok(data: dict, kind="search"):
    allowed, reason = guard.check(client_ip(), kind=kind)
    if not allowed:
        return False, reason
    token = (
        (data or {}).get("token")
        or request.headers.get("X-Shop-Token", "")
        or (request.form.get("token") if request.form else "")
    )
    if not guard.validate_token(token):
        return False, "页面凭证已过期，请刷新页面后重试"
    return True, ""


@bp.route("/")
@bp.route("/shop")
def index():
    settings = load_shop_settings()
    token = guard.issue_token()
    if not settings.get("enabled", True) or not settings.get("public_shop_enabled", True):
        return render_template(
            "shop_index.html", products=[], settings=settings, token=token,
            maintenance=True,
            notice=(settings.get("shop_notice") or "").strip() or "商城暂时关闭，请稍后再来。",
        )
    products = ShopService().list_products(only_on_sale=True, page=1, page_size=100)["products"]
    return render_template("shop_index.html", products=products, settings=settings,
                           token=token, maintenance=False, notice="")


@bp.route("/shop/p/<int:product_id>")
def product_detail(product_id):
    settings = load_shop_settings()
    token = guard.issue_token()
    if not settings.get("enabled", True) or not settings.get("public_shop_enabled", True):
        return render_template("error.html", title="商城维护中",
                               message="商城暂时关闭，请稍后再来。"), 403
    product = ShopService().get_product(product_id)
    if not product or int(product.get("status") or 0) != 1:
        return render_template("error.html", title="商品不可用",
                               message="商品不存在或已下架"), 404
    return render_template("shop_product.html", product=product, settings=settings, token=token)


@bp.route("/orders")
def order_query_page():
    settings = load_shop_settings()
    return render_template("order_query.html", settings=settings, token=guard.issue_token())


@bp.route("/support")
def support_page():
    settings = load_shop_settings()
    return render_template("support.html", settings=settings, token=guard.issue_token())


@bp.route("/api/shop/public/products")
def public_products():
    settings = load_shop_settings()
    if not settings.get("enabled", True) or not settings.get("public_shop_enabled", True):
        return jsonify({"products": [], "total": 0, "closed": True, "msg": "商城暂未开放"})
    result = ShopService().list_products(only_on_sale=True, page=1, page_size=100)
    items = [{
        "id": p.get("id"),
        "name": p.get("name"),
        "description": p.get("description"),
        "price": p.get("price"),
        "fulfill_mode": p.get("fulfill_mode"),
        "stock_count": p.get("stock_count"),
        "sold_count": p.get("sold_count"),
    } for p in result.get("products") or []]
    return jsonify({
        "products": items,
        "total": result.get("total", 0),
        "shop_title": settings.get("shop_title"),
        "shop_notice": settings.get("shop_notice"),
    })


@bp.route("/api/shop/order", methods=["POST"])
def create_order():
    data = json_body()
    ok, reason = _token_ok(data, kind="search")
    if not ok:
        return jsonify({"code": -1, "msg": reason}), 429
    try:
        product_id = int(data.get("product_id") or 0)
        qty = int(data.get("qty") or 1)
    except (TypeError, ValueError):
        return jsonify({"code": -1, "msg": "参数无效"}), 400
    result = ShopService().create_order(
        product_id=product_id,
        qty=qty,
        contact=str(data.get("contact") or "")[:200],
        buyer_platform=str(data.get("buyer_platform") or "")[:32],
        buyer_user_id=str(data.get("buyer_user_id") or "")[:128],
        source="web",
    )
    return jsonify(result), (200 if result.get("code") == 1 else 400)


@bp.route("/api/shop/order/<order_no>")
def order_detail(order_no):
    result = ShopService().query_order(order_no, sync_payment=True)
    return jsonify(result), (200 if result.get("code") == 1 else 404)


@bp.route("/api/shop/query", methods=["POST"])
def public_search():
    data = json_body()
    ok, reason = _token_ok(data, kind="search")
    if not ok:
        return jsonify({"code": -1, "msg": reason}), 429
    result = ShopService().public_search(data.get("query") or data.get("q") or "")
    return jsonify(result)


@bp.route("/api/shop/feedback/start", methods=["POST"])
def feedback_start():
    data = json_body()
    ok, reason = _token_ok(data, kind="feedback")
    if not ok:
        return jsonify({"code": -1, "msg": reason}), 429
    return jsonify(ShopService().user_start_feedback(data.get("contact") or ""))


@bp.route("/api/shop/feedback/upload", methods=["POST"])
def feedback_upload():
    """客服附件上传（multipart，最大 10MB，图片/文档）。"""
    form = dict(request.form)
    ok, reason = _token_ok(form, kind="feedback")
    if not ok:
        return jsonify({"code": -1, "msg": reason}), 429

    svc = ShopService()
    contact, err = svc.validate_feedback_contact(form.get("contact") or "")
    if err:
        return jsonify(err), 400

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"code": -1, "msg": "未选择文件"}), 400
    result, error = save_feedback_file(f.read(), f.filename)
    if error:
        return jsonify({"code": -1, "msg": error}), 400
    return jsonify({"code": 1, "msg": "SUCCESS", "attachment": result})


@bp.route("/api/shop/feedback/send", methods=["POST"])
def feedback_send():
    data = json_body()
    ok, reason = _token_ok(data, kind="feedback")
    if not ok:
        return jsonify({"code": -1, "msg": reason}), 429
    return jsonify(ShopService().user_send_feedback(
        data.get("contact") or "",
        data.get("content") or "",
        attachment=data.get("attachment"),
        base_url=request.host_url,
    ))


@bp.route("/api/shop/feedback/notify-settings", methods=["GET", "POST"])
def feedback_notify_settings():
    svc = ShopService()
    if request.method == "GET":
        ok, reason = _token_ok({"token": request.args.get("token", "")}, kind="search")
        if not ok:
            return jsonify({"code": -1, "msg": reason}), 429
        return jsonify(svc.user_notify_settings(request.args.get("contact") or ""))

    data = json_body()
    ok, reason = _token_ok(data, kind="feedback")
    if not ok:
        return jsonify({"code": -1, "msg": reason}), 429
    result = svc.user_update_notify_settings(
        data.get("contact") or "",
        data.get("email") or "",
        bool(data.get("enabled")),
    )
    return jsonify(result)


@bp.route("/api/shop/feedback/pull", methods=["POST"])
def feedback_pull():
    data = json_body()
    ok, reason = _token_ok(data, kind="feedback")
    if not ok:
        return jsonify({"code": -1, "msg": reason}), 429
    return jsonify(ShopService().user_pull_feedback(data.get("contact") or ""))
