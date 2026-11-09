"""MCP 服务（Model Context Protocol），与 Web 同端口、路径 /mcp。

传输：Streamable HTTP（JSON-RPC 2.0，直接返回 JSON）。
鉴权：Authorization: Bearer <MCP Token>（也支持 X-MCP-Token 或 ?token=）。
Token 在后台「系统设置 → MCP 服务」查看/重置。

工具覆盖：统计健康、商品/卡密、码支付订单（补单/回调）、商城订单、客服工单、
支付配置、邮件配置、商城设置、监控执行等。
"""

from __future__ import annotations

import base64
import binascii
import csv
import hmac
import io
import json
import os

from flask import Blueprint, jsonify, request

from alipay_pay.codepay import CodePay
from alipay_pay.monitor import mark_paid, monitoring_status, run_monitoring_cycle
from alipay_pay.orders import OrderStore
from config import BASE_DIR
from core import mailer
from core.html_utils import clean_description
from core.images import save_image_bytes
from config import APP_VERSION
from core.logger import get_logger
from core.reports import shop_orders_for_export
from core.settings import (
    EMAIL_PRESETS,
    gen_merchant_credentials,
    get_mcp_settings,
    is_configured,
    load_alipay_config,
    load_email_config,
    load_shop_settings,
    payment_readiness,
    save_alipay_config,
    save_email_config,
    save_shop_settings,
)
from shop.service import ShopService

logger = get_logger("mcp")

mcp_bp = Blueprint("mcp", __name__)

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "alipay-pay-mcp", "version": APP_VERSION}
SERVER_INSTRUCTIONS = (
    "码支付与卡密商城管理服务。可查询/管理商品、卡密、支付订单、商城订单、客服工单、"
    "支付配置、邮件配置与商城设置。补单、删除、修改配置等操作会产生真实影响，请谨慎调用。"
    "敏感配置读取时已掩码；修改时只有传入非空值才会覆盖。"
)

# ---------------- JSON-RPC 辅助 ----------------

def _ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id, code, message, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": error}


def _token_from_request() -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("X-MCP-Token", "")
            or request.args.get("token", "")).strip()


def _authorized():
    try:
        cfg = get_mcp_settings()
    except Exception as e:
        logger.error("读取 MCP 配置失败: %s", e)
        return False, "config_error"
    if not cfg.get("enabled"):
        return False, "disabled"
    token = _token_from_request()
    if not token or not hmac.compare_digest(token, cfg.get("token", "")):
        return False, "invalid_token"
    return True, ""


# ---------------- 参数辅助 ----------------

def _s(args, key, default=""):
    value = args.get(key, default)
    if value is None:
        return default
    return str(value).strip()


def _i(args, key, default, low=None, high=None):
    try:
        value = int(args.get(key, default))
    except (TypeError, ValueError):
        value = int(default)
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def _b(args, key, default=False):
    value = args.get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _arr(args, key):
    value = args.get(key)
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _mask(value):
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}…{text[-4:]}"


def _tool(name, description, properties=None, required=None):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
        },
    }


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}
NUM = {"type": "number"}
ARR_I = {"type": "array", "items": I}
ARR_S = {"type": "array", "items": S}

P_PAGE = {"type": "integer", "minimum": 1, "description": "页码，默认 1"}
P_PAGE_SIZE = {"type": "integer", "minimum": 1, "maximum": 100, "description": "每页数量，默认 20/50"}
P_SEARCH = {"type": "string", "description": "搜索关键词"}
P_STATUS = {"type": "string", "description": "状态筛选，留空为全部"}

PRODUCT_PROPS = {
    "name": {"type": "string", "maxLength": 128, "description": "商品名称"},
    "price": {"type": "number", "minimum": 0.01, "description": "价格（元）"},
    "description": {"type": "string", "description": "富文本描述（允许图片，服务端会清洗）"},
    "icon_path": {"type": "string", "description": "商品图标 URL，例如 /uploads/xxx.png 或 https://..."},
    "fulfill_mode": {"type": "string", "enum": ["stock", "generate"], "description": "发货方式"},
    "generate_config": {
        "type": "object",
        "description": "自动生成规则：prefix/length/charset/quantity",
        "properties": {
            "prefix": S, "length": I,
            "charset": {"type": "string", "enum": ["hex", "digits", "upper", "alnum"]},
            "quantity": I,
        },
    },
    "status": {"type": "integer", "enum": [0, 1], "description": "1 上架 / 0 下架"},
    "sort_order": {"type": "integer", "description": "排序，越小越靠前"},
}

TOOLS = [
    _tool("get_stats", "获取支付订单、商城、客服、监控与收款就绪状态统计"),
    _tool("get_health", "检查依赖、配置、数据库、监控与收款就绪状态"),
    _tool("get_monitor_status", "获取支付监控线程运行状态与最近一次检查结果"),
    _tool("run_monitor", "立即执行一次支付宝账单检查（会产生真实查询）",
          {"cleanup": {"type": "boolean", "description": "是否同时清理过期订单，默认 true"}}),

    _tool("list_products", "商品列表（支持分页、搜索、仅在售）", {
        "page": P_PAGE, "page_size": P_PAGE_SIZE, "search": P_SEARCH,
        "only_on_sale": {"type": "boolean", "description": "仅看在售"},
    }),
    _tool("get_product", "获取商品详情", {"product_id": I}, ["product_id"]),
    _tool("create_product", "创建商品（写操作）", PRODUCT_PROPS, ["name", "price"]),
    _tool("update_product", "更新商品，只需传要修改的字段（写操作）",
          dict(product_id=I, **PRODUCT_PROPS), ["product_id"]),
    _tool("delete_products", "批量删除商品；已有订单的商品会被跳过（不可恢复）",
          {"ids": ARR_I}, ["ids"]),
    _tool("set_products_status", "批量上架/下架商品", {
        "ids": ARR_I, "status": {"type": "integer", "enum": [0, 1]},
    }, ["ids", "status"]),

    _tool("list_cards", "查询某商品的卡密列表", {
        "product_id": I, "status": {"type": "string", "enum": ["available", "sold", "disabled"]},
        "page": P_PAGE, "page_size": P_PAGE_SIZE, "search": P_SEARCH,
    }, ["product_id"]),
    _tool("import_cards", "批量导入卡密（重复自动跳过）", {
        "product_id": I, "codes": {"type": "array", "items": S},
    }, ["product_id", "codes"]),
    _tool("set_card_status", "启用/禁用卡密", {
        "card_id": I, "status": {"type": "string", "enum": ["available", "disabled"]},
    }, ["card_id", "status"]),
    _tool("delete_cards", "删除卡密；已售出的卡密不可删除", {"card_ids": ARR_I}, ["card_ids"]),
    _tool("upload_image", "上传图片（商品图标/描述图），支持 base64 或 data URI，返回 URL", {
        "filename": S, "content_base64": {"type": "string", "description": "图片 base64"},
    }, ["content_base64"]),

    _tool("list_orders", "码支付订单列表", {
        "status": {"type": "string", "description": "0 待支付 / 1 已支付 / 2 已过期，留空全部"},
        "page": P_PAGE, "page_size": P_PAGE_SIZE, "search": P_SEARCH,
    }),
    _tool("get_order", "查询码支付订单详情（平台订单号或商户订单号）", {
        "trade_no": {"type": "string", "description": "平台订单号或商户订单号"},
    }, ["trade_no"]),
    _tool("mark_order_paid", "补单：标记码支付订单已支付，触发商城发货与商户回调（写操作）", {
        "trade_no": {"type": "string", "description": "平台订单号"},
    }, ["trade_no"]),
    _tool("expire_orders", "批量把码支付订单置为已过期（写操作）", {"ids": ARR_S}, ["ids"]),
    _tool("delete_orders", "批量删除码支付订单（不可恢复）", {"ids": ARR_S}, ["ids"]),
    _tool("resend_notify", "重新发送某订单的商户回调", {"trade_no": S}, ["trade_no"]),

    _tool("list_shop_orders", "商城订单列表", {
        "status": {"type": "string", "description": "pending/paid/fulfilled/expired/failed，留空全部"},
        "page": P_PAGE, "page_size": P_PAGE_SIZE, "search": P_SEARCH,
    }),
    _tool("get_shop_order", "商城订单详情（含卡密与邮件状态）", {"order_no": S}, ["order_no"]),
    _tool("mark_shop_order_paid", "商城补单：标记支付并补发卡密（写操作）", {"order_no": S}, ["order_no"]),
    _tool("refulfill_shop_order", "商城订单重新发货（仅已支付/失败订单）", {"order_no": S}, ["order_no"]),
    _tool("delete_shop_orders", "批量删除商城订单（不可恢复）", {"order_nos": ARR_S}, ["order_nos"]),
    _tool("export_shop_orders_csv", "导出商城订单 CSV 文本", {
        "status": S, "search": S,
    }),

    _tool("list_tickets", "客服工单列表", {
        "status": {"type": "string", "enum": ["open", "replied", "closed"]},
        "page": P_PAGE, "page_size": P_PAGE_SIZE, "search": P_SEARCH,
        "unread_only": {"type": "boolean", "description": "仅未读"},
    }),
    _tool("get_ticket", "客服工单详情与会话消息", {
        "ticket_id": I, "mark_read": {"type": "boolean", "description": "是否标记已读，默认 true"},
    }, ["ticket_id"]),
    _tool("reply_ticket", "回复客服工单（写操作）", {"ticket_id": I, "content": S},
          ["ticket_id", "content"]),
    _tool("set_ticket_status", "设置工单状态", {
        "ticket_id": I, "status": {"type": "string", "enum": ["open", "replied", "closed"]},
    }, ["ticket_id", "status"]),
    _tool("delete_tickets", "批量删除客服工单（含会话消息，不可恢复）", {"ids": ARR_I}, ["ids"]),

    _tool("get_pay_config", "获取支付配置；私钥/公钥/商户密钥/推送 token 已掩码"),
    _tool("update_pay_config", "更新支付配置，只更新传入字段；敏感字段留空不修改（写操作）", {
        "app_id": S, "private_key": S, "alipay_public_key": S,
        "transfer_user_id": S, "merchant_id": S, "merchant_key": S,
        "business_qr_mode": {
            "type": "object",
            "properties": {
                "enabled": B, "qr_code_path": S,
                "amount_offset": NUM, "match_tolerance": I,
            },
        },
        "order_timeout": I, "monitor_interval": I, "query_minutes_back": I,
        "pushplus": {
            "type": "object",
            "properties": {
                "enabled": B, "token": S, "template": S, "title_prefix": S,
            },
        },
    }),
    _tool("generate_merchant", "按 AppId 生成商户凭据；save=true 时直接保存到支付配置", {
        "app_id": S, "save": {"type": "boolean", "description": "是否立即保存，默认 true"},
    }, ["app_id"]),
    _tool("get_email_config", "获取 SMTP 邮件配置；密码已掩码"),
    _tool("update_email_config", "更新 SMTP 邮件配置；密码留空不修改（写操作）", {
        "enabled": B, "preset": S, "host": S, "port": I,
        "security": {"type": "string", "enum": ["ssl", "starttls", "none"]},
        "username": S, "password": S, "from_name": S, "from_email": S,
    }),
    _tool("test_email", "使用当前邮件配置发送测试邮件", {"to": S}, ["to"]),
    _tool("get_shop_settings", "获取商城设置"),
    _tool("update_shop_settings", "更新商城设置（写操作）", {
        "enabled": B, "public_shop_enabled": B, "shop_title": S,
        "shop_notice": S, "max_qty_per_order": I, "notify_admin_pushplus": B,
    }),
]

TOOL_NAMES = {tool["name"] for tool in TOOLS}


# ---------------- 工具实现 ----------------

def _pay_config_view():
    cfg = load_alipay_config()
    ready, reason = payment_readiness(cfg)
    qr = cfg.get("business_qr_mode") or {}
    pp = cfg.get("pushplus") or {}
    return {
        "app_id": cfg.get("app_id", ""),
        "private_key": _mask(cfg.get("private_key")),
        "alipay_public_key": _mask(cfg.get("alipay_public_key")),
        "transfer_user_id": cfg.get("transfer_user_id", ""),
        "merchant_id": cfg.get("merchant_id", ""),
        "merchant_key": _mask(cfg.get("merchant_key")),
        "server_url": cfg.get("server_url", ""),
        "sign_type": cfg.get("sign_type", ""),
        "business_qr_mode": {
            "enabled": bool(qr.get("enabled")),
            "qr_code_path": qr.get("qr_code_path", ""),
            "amount_offset": qr.get("amount_offset", 0.01),
            "match_tolerance": qr.get("match_tolerance", 300),
        },
        "order_timeout": cfg.get("order_timeout"),
        "monitor_interval": cfg.get("monitor_interval"),
        "query_minutes_back": cfg.get("query_minutes_back"),
        "pushplus": {
            "enabled": bool(pp.get("enabled")),
            "token": _mask(pp.get("token", "")),
            "template": pp.get("template", "markdown"),
            "title_prefix": pp.get("title_prefix", ""),
        },
        "payment_ready": ready,
        "payment_reason": reason,
    }


def _email_config_view():
    cfg = load_email_config()
    return {
        **cfg,
        "password": _mask(cfg.get("password")),
        "presets": EMAIL_PRESETS,
    }


def _h_get_stats(_args):
    svc = ShopService()
    cfg = load_alipay_config()
    ready, _reason = payment_readiness(cfg)
    return {
        "pay": OrderStore().stats(),
        "shop": svc.get_stats(),
        "feedback": svc.feedback_stats(),
        "payment_ready": ready,
        "monitor": monitoring_status(),
    }


def _h_get_health(_args):
    cfg = load_alipay_config()
    ready, reason = payment_readiness(cfg)
    deps = {}
    for name in ("alipay", "qrcode", "PIL", "cryptography", "rsa",
                 "requests", "bleach", "waitress"):
        try:
            __import__(name)
            deps[name] = "OK"
        except Exception:
            deps[name] = "MISSING"
    try:
        db_stats = OrderStore().stats()
    except Exception as e:
        db_stats = {"error": str(e)}
    return {
        "configured": is_configured(cfg),
        "payment_ready": ready,
        "payment_reason": reason,
        "business_qr_mode": bool((cfg.get("business_qr_mode") or {}).get("enabled")),
        "dependencies": deps,
        "database": db_stats,
        "monitor": monitoring_status(),
    }


def _h_list_products(a):
    return ShopService().list_products(
        only_on_sale=_b(a, "only_on_sale"),
        page=_i(a, "page", 1, 1),
        page_size=_i(a, "page_size", 50, 1, 100),
        search=_s(a, "search") or None,
    )


def _h_get_product(a):
    product = ShopService().get_product(_i(a, "product_id", 0))
    if not product:
        raise ValueError("商品不存在")
    return product


def _h_create_product(a):
    name = _s(a, "name")
    if not name:
        raise ValueError("缺少 name")
    try:
        price = float(a.get("price"))
    except (TypeError, ValueError):
        raise ValueError("price 无效")
    if price < 0.01:
        raise ValueError("price 至少 0.01")
    mode = _s(a, "fulfill_mode", "stock") or "stock"
    if mode not in ("stock", "generate"):
        raise ValueError("fulfill_mode 必须是 stock 或 generate")
    return ShopService().create_product(
        name=name,
        description=clean_description(a.get("description")),
        price=price,
        fulfill_mode=mode,
        generate_config=a.get("generate_config") or {},
        status=_i(a, "status", 1, 0, 1),
        sort_order=_i(a, "sort_order", 0),
        icon_path=_s(a, "icon_path"),
    )


def _h_update_product(a):
    product_id = _i(a, "product_id", 0)
    if not product_id:
        raise ValueError("缺少 product_id")
    fields = {}
    if "name" in a:
        fields["name"] = _s(a, "name")
    if "price" in a:
        try:
            fields["price"] = float(a["price"])
        except (TypeError, ValueError):
            raise ValueError("price 无效")
    if "description" in a:
        fields["description"] = clean_description(a.get("description"))
    if "icon_path" in a:
        fields["icon_path"] = _s(a, "icon_path")
    if "fulfill_mode" in a:
        fields["fulfill_mode"] = _s(a, "fulfill_mode")
    if "generate_config" in a:
        fields["generate_config"] = a.get("generate_config") or {}
    if "status" in a:
        fields["status"] = _i(a, "status", 1, 0, 1)
    if "sort_order" in a:
        fields["sort_order"] = _i(a, "sort_order", 0)
    product = ShopService().update_product(product_id, **fields)
    if not product:
        raise ValueError("商品不存在")
    return product


def _h_delete_products(a):
    ids = [int(x) for x in _arr(a, "ids")]
    if not ids:
        raise ValueError("缺少 ids")
    return ShopService().delete_products_many(ids)


def _h_set_products_status(a):
    ids = [int(x) for x in _arr(a, "ids")]
    status = _i(a, "status", 1, 0, 1)
    if not ids:
        raise ValueError("缺少 ids")
    return {"affected": ShopService().set_products_status_many(ids, status)}


def _h_list_cards(a):
    product_id = _i(a, "product_id", 0)
    if not product_id:
        raise ValueError("缺少 product_id")
    return ShopService().list_cards(
        product_id,
        status=_s(a, "status") or None,
        page=_i(a, "page", 1, 1),
        page_size=_i(a, "page_size", 50, 1, 100),
        search=_s(a, "search") or None,
    )


def _h_import_cards(a):
    product_id = _i(a, "product_id", 0)
    codes = [str(x) for x in _arr(a, "codes") if str(x).strip()]
    if not product_id or not codes:
        raise ValueError("缺少 product_id 或 codes")
    result = ShopService().import_cards(product_id, codes)
    if not result.get("ok"):
        raise ValueError(result.get("msg", "导入失败"))
    return result


def _h_set_card_status(a):
    card_id = _i(a, "card_id", 0)
    status = _s(a, "status")
    svc = ShopService()
    card = svc.enable_card(card_id) if status == "available" else svc.disable_card(card_id)
    if not card:
        raise ValueError("卡密不存在")
    return card


def _h_delete_cards(a):
    deleted, skipped = 0, []
    for card_id in [int(x) for x in _arr(a, "card_ids")]:
        result = ShopService().delete_card(card_id)
        if result and result.get("ok"):
            deleted += 1
        else:
            skipped.append({"card_id": card_id, "reason": (result or {}).get("msg", "删除失败")})
    return {"deleted": deleted, "skipped": skipped}


def _h_upload_image(a):
    raw_b64 = _s(a, "content_base64")
    if not raw_b64:
        raise ValueError("缺少 content_base64")
    if "," in raw_b64 and raw_b64.startswith("data:"):
        raw_b64 = raw_b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(raw_b64)
    except (binascii.Error, ValueError):
        raise ValueError("content_base64 解析失败")
    result, error = save_image_bytes(raw)
    if error:
        raise ValueError(error)
    return result


def _h_list_orders(a):
    status = _s(a, "status")
    return OrderStore().paginated(
        status=status if status != "" else None,
        page=_i(a, "page", 1, 1),
        page_size=_i(a, "page_size", 20, 1, 100),
        search=_s(a, "search") or None,
    )


def _h_get_order(a):
    key = _s(a, "trade_no")
    if not key:
        raise ValueError("缺少 trade_no")
    store = OrderStore()
    order = store.get_order(order_id=key) or store.get_order(out_trade_no=key)
    if not order:
        raise ValueError("订单不存在")
    return order


def _h_mark_order_paid(a):
    trade_no = _s(a, "trade_no")
    if not trade_no:
        raise ValueError("缺少 trade_no")
    result = mark_paid(trade_no, detected_by="mcp")
    if not result.get("order"):
        raise ValueError("订单不存在")
    return result


def _h_expire_orders(a):
    ids = [str(x) for x in _arr(a, "ids")]
    if not ids:
        raise ValueError("缺少 ids")
    return {"affected": OrderStore().set_status_many(ids, 2)}


def _h_delete_orders(a):
    ids = [str(x) for x in _arr(a, "ids")]
    if not ids:
        raise ValueError("缺少 ids")
    return {"affected": OrderStore().delete_many(ids)}


def _h_resend_notify(a):
    trade_no = _s(a, "trade_no")
    if not trade_no:
        raise ValueError("缺少 trade_no")
    order = OrderStore().get_order(order_id=trade_no)
    if not order:
        raise ValueError("订单不存在")
    if order["status"] != 1:
        raise ValueError("订单未支付，无法发送回调")
    return CodePay().send_notification(order)


def _h_list_shop_orders(a):
    return ShopService().list_orders(
        status=_s(a, "status") or None,
        page=_i(a, "page", 1, 1),
        page_size=_i(a, "page_size", 20, 1, 100),
        search=_s(a, "search") or None,
    )


def _h_get_shop_order(a):
    order_no = _s(a, "order_no")
    if not order_no:
        raise ValueError("缺少 order_no")
    order = ShopService().db.get_order_by_no(order_no)
    if not order:
        raise ValueError("订单不存在")
    return order


def _h_mark_shop_order_paid(a):
    order_no = _s(a, "order_no")
    if not order_no:
        raise ValueError("缺少 order_no")
    return ShopService().mark_paid_by_order_no(order_no)


def _h_refulfill_shop_order(a):
    order_no = _s(a, "order_no")
    if not order_no:
        raise ValueError("缺少 order_no")
    result = ShopService().refulfill(order_no)
    if result.get("code") != 1:
        raise ValueError(result.get("msg", "补发失败"))
    return result


def _h_delete_shop_orders(a):
    order_nos = [str(x) for x in _arr(a, "order_nos")]
    if not order_nos:
        raise ValueError("缺少 order_nos")
    return {"affected": ShopService().delete_orders_many(order_nos)}


def _h_export_shop_orders_csv(a):
    rows = shop_orders_for_export(status=_s(a, "status") or None,
                                  search=_s(a, "search") or None)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["订单号", "商品", "数量", "金额", "邮箱", "支付单号",
                     "状态", "创建时间", "支付时间", "发货时间", "卡密", "失败原因"])
    for r in rows:
        writer.writerow([
            r["order_no"], r["product_name_snapshot"], r["qty"],
            f"{float(r['price']) * int(r['qty']):.2f}",
            r.get("contact") or "", r["payment_out_trade_no"], r["status"],
            r["created_at"], r.get("paid_at") or "", r.get("fulfilled_at") or "",
            (r.get("card_payload") or "").replace("\n", " | "),
            (r.get("fail_reason") or "").replace("\n", " "),
        ])
    return {"count": len(rows), "csv": buf.getvalue()}


def _h_list_tickets(a):
    return ShopService().admin_list_tickets(
        status=_s(a, "status") or None,
        page=_i(a, "page", 1, 1),
        page_size=_i(a, "page_size", 20, 1, 100),
        search=_s(a, "search") or None,
        unread_only=_b(a, "unread_only"),
    )


def _h_get_ticket(a):
    result = ShopService().admin_get_ticket(
        _i(a, "ticket_id", 0), mark_read=_b(a, "mark_read", True)
    )
    if result.get("code") != 1:
        raise ValueError(result.get("msg", "工单不存在"))
    return result


def _h_reply_ticket(a):
    result = ShopService().admin_reply_ticket(
        _i(a, "ticket_id", 0), _s(a, "content")
    )
    if result.get("code") != 1:
        raise ValueError(result.get("msg", "回复失败"))
    return result


def _h_set_ticket_status(a):
    result = ShopService().admin_set_ticket_status(
        _i(a, "ticket_id", 0), _s(a, "status")
    )
    if result.get("code") != 1:
        raise ValueError(result.get("msg", "操作失败"))
    return result


def _h_delete_tickets(a):
    ids = [int(x) for x in _arr(a, "ids")]
    if not ids:
        raise ValueError("缺少 ids")
    return {"affected": ShopService().delete_tickets_many(ids)}


def _h_get_pay_config(_args):
    return _pay_config_view()


def _h_update_pay_config(a):
    patch = {}
    for key in ("app_id", "transfer_user_id", "merchant_id",
                "server_url", "sign_type", "charset"):
        if key in a:
            patch[key] = _s(a, key)
    for key in ("private_key", "alipay_public_key", "merchant_key"):
        if key in a and _s(a, key):
            patch[key] = _s(a, key)
    for key in ("order_timeout", "monitor_interval", "query_minutes_back"):
        if key in a:
            patch[key] = a[key]
    if isinstance(a.get("business_qr_mode"), dict):
        patch["business_qr_mode"] = a["business_qr_mode"]
    if isinstance(a.get("pushplus"), dict):
        patch["pushplus"] = a["pushplus"]
    if not patch:
        raise ValueError("没有可更新的字段")
    save_alipay_config(patch)
    return _pay_config_view()


def _h_generate_merchant(a):
    app_id = _s(a, "app_id")
    if not app_id:
        raise ValueError("缺少 app_id")
    creds = gen_merchant_credentials(app_id)
    if _b(a, "save", True):
        save_alipay_config({
            "merchant_id": creds["merchant_id"],
            "merchant_key": creds["merchant_key"],
        })
        creds["saved"] = True
    else:
        creds["saved"] = False
    return creds


def _h_get_email_config(_args):
    return _email_config_view()


def _h_update_email_config(a):
    patch = {}
    for key in ("enabled", "preset", "host", "port", "security",
                "username", "from_name", "from_email"):
        if key in a:
            patch[key] = a[key]
    if _s(a, "password"):
        patch["password"] = _s(a, "password")
    if not patch:
        raise ValueError("没有可更新的字段")
    save_email_config(patch)
    return _email_config_view()


def _h_test_email(a):
    to = _s(a, "to")
    if not mailer.is_email(to):
        raise ValueError("收件邮箱格式不正确")
    cfg = load_email_config()
    title = load_shop_settings().get("shop_title") or "卡密商城"
    subject, html_body, text_body = mailer.test_email_content(title)
    ok_flag, error = mailer.send_email(cfg, to, subject, html_body, text_body)
    if not ok_flag:
        raise ValueError(error or "发送失败")
    return {"ok": True, "to": to}


def _h_get_shop_settings(_args):
    return load_shop_settings()


def _h_update_shop_settings(a):
    patch = {}
    for key in ("enabled", "public_shop_enabled", "shop_title",
                "shop_notice", "max_qty_per_order", "notify_admin_pushplus"):
        if key in a:
            patch[key] = a[key]
    if not patch:
        raise ValueError("没有可更新的字段")
    return save_shop_settings(patch)


def _h_run_monitor(a):
    return run_monitoring_cycle(cleanup=_b(a, "cleanup", True))


def _h_monitor_status(_args):
    return monitoring_status()


HANDLERS = {
    "get_stats": _h_get_stats,
    "get_health": _h_get_health,
    "get_monitor_status": _h_monitor_status,
    "run_monitor": _h_run_monitor,
    "list_products": _h_list_products,
    "get_product": _h_get_product,
    "create_product": _h_create_product,
    "update_product": _h_update_product,
    "delete_products": _h_delete_products,
    "set_products_status": _h_set_products_status,
    "list_cards": _h_list_cards,
    "import_cards": _h_import_cards,
    "set_card_status": _h_set_card_status,
    "delete_cards": _h_delete_cards,
    "upload_image": _h_upload_image,
    "list_orders": _h_list_orders,
    "get_order": _h_get_order,
    "mark_order_paid": _h_mark_order_paid,
    "expire_orders": _h_expire_orders,
    "delete_orders": _h_delete_orders,
    "resend_notify": _h_resend_notify,
    "list_shop_orders": _h_list_shop_orders,
    "get_shop_order": _h_get_shop_order,
    "mark_shop_order_paid": _h_mark_shop_order_paid,
    "refulfill_shop_order": _h_refulfill_shop_order,
    "delete_shop_orders": _h_delete_shop_orders,
    "export_shop_orders_csv": _h_export_shop_orders_csv,
    "list_tickets": _h_list_tickets,
    "get_ticket": _h_get_ticket,
    "reply_ticket": _h_reply_ticket,
    "set_ticket_status": _h_set_ticket_status,
    "delete_tickets": _h_delete_tickets,
    "get_pay_config": _h_get_pay_config,
    "update_pay_config": _h_update_pay_config,
    "generate_merchant": _h_generate_merchant,
    "get_email_config": _h_get_email_config,
    "update_email_config": _h_update_email_config,
    "test_email": _h_test_email,
    "get_shop_settings": _h_get_shop_settings,
    "update_shop_settings": _h_update_shop_settings,
}


# ---------------- 资源 ----------------

RESOURCES = [
    {"uri": "alipay://readme", "name": "README", "title": "项目说明",
     "mimeType": "text/markdown"},
    {"uri": "alipay://api-docs", "name": "接口文档", "title": "CodePay 对接文档",
     "mimeType": "text/plain"},
    {"uri": "alipay://stats", "name": "实时统计", "title": "支付/商城统计",
     "mimeType": "application/json"},
]


def _resource_contents(uri):
    if uri == "alipay://readme":
        path = os.path.join(BASE_DIR, "README.md")
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()[:200000]
        except Exception as e:
            raise ValueError(f"读取 README 失败: {e}")
        return [{"uri": uri, "mimeType": "text/markdown", "text": text}]
    if uri == "alipay://api-docs":
        base = request.host_url.rstrip("/")
        return [{
            "uri": uri, "mimeType": "text/plain",
            "text": f"接口文档地址: {base}/admin/api-docs\nMCP 工具列表见 tools/list。",
        }]
    if uri == "alipay://stats":
        svc = ShopService()
        text = json.dumps({
            "pay": OrderStore().stats(),
            "shop": svc.get_stats(),
            "feedback": svc.feedback_stats(),
        }, ensure_ascii=False, indent=2)
        return [{"uri": uri, "mimeType": "application/json", "text": text}]
    raise ValueError("未知资源")


# ---------------- JSON-RPC 处理 ----------------

def _tool_error(text):
    return {"content": [{"type": "text", "text": f"错误: {text}"}], "isError": True}


def _call_tool(params):
    name = params.get("name") if isinstance(params, dict) else None
    arguments = params.get("arguments") if isinstance(params, dict) else None
    if not isinstance(name, str) or name not in TOOL_NAMES:
        return _tool_error(f"未知工具: {name}")
    args = arguments if isinstance(arguments, dict) else {}
    handler = HANDLERS[name]
    logger.info("MCP tools/call %s keys=%s", name, list(args.keys()))
    try:
        result = handler(args)
    except ValueError as e:
        return _tool_error(str(e))
    except Exception as e:
        logger.warning("MCP 工具 %s 执行失败: %s", name, e)
        return _tool_error(f"执行失败: {e}")
    if not isinstance(result, (dict, list, str, int, float, bool)) and result is not None:
        result = str(result)
    text = result if isinstance(result, str) else json.dumps(
        result, ensure_ascii=False, indent=2, default=str
    )
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _handle_message(message):
    if not isinstance(message, dict):
        return _err(None, -32600, "Invalid Request")
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        return _ok(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
            },
            "serverInfo": SERVER_INFO,
            "instructions": SERVER_INSTRUCTIONS,
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return _ok(msg_id, {})

    if method == "tools/list":
        return _ok(msg_id, {"tools": TOOLS})

    if method == "tools/call":
        return _ok(msg_id, _call_tool(params))

    if method == "resources/list":
        return _ok(msg_id, {"resources": RESOURCES})

    if method == "resources/templates/list":
        return _ok(msg_id, {"resourceTemplates": []})

    if method == "resources/read":
        uri = params.get("uri") if isinstance(params, dict) else ""
        try:
            contents = _resource_contents(str(uri or ""))
        except ValueError as e:
            return _err(msg_id, -32602, str(e))
        return _ok(msg_id, {"contents": contents})

    if method == "prompts/list":
        return _ok(msg_id, {"prompts": []})

    if method == "logging/setLevel":
        return _ok(msg_id, {})

    return _err(msg_id, -32601, f"Method not found: {method}")


@mcp_bp.route("/mcp", methods=["POST"])
def mcp_endpoint():
    allowed, reason = _authorized()
    if not allowed:
        status = 403 if reason == "disabled" else 401
        return jsonify(_err(None, -32001, f"Unauthorized: {reason}")), status

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify(_err(None, -32700, "Parse error")), 400

    if isinstance(payload, list):
        results = [r for r in (_handle_message(m) for m in payload) if r is not None]
        if not results:
            return "", 202
        return jsonify(results)

    result = _handle_message(payload)
    if result is None:
        return "", 202
    return jsonify(result)


@mcp_bp.route("/mcp", methods=["GET"])
def mcp_info():
    allowed, _reason = _authorized()
    if not allowed:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({
        "name": SERVER_INFO["name"],
        "protocolVersion": PROTOCOL_VERSION,
        "transport": "streamable-http",
        "endpoint": request.host_url.rstrip("/") + "/mcp",
        "usage": "POST JSON-RPC 2.0 with Authorization: Bearer <token>",
        "tools": len(TOOLS),
        "resources": len(RESOURCES),
    })
