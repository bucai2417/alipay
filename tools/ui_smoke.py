"""可选：真实浏览器 UI 冒烟测试（需要 playwright + 本机 Edge/Chrome）。

建议使用独立数据目录启动服务后运行，避免影响正式数据：
    ALIPAY_DATA_DIR=./data_uitest ALIPAY_PORT=5000 ALIPAY_MONITOR=0 python app.py
    python tools/ui_smoke.py [http://127.0.0.1:5000]

脚本会自动完成：后台登录（含记住账号密码）、确保测试商品与支付配置、
商城下单、核对支付二维码、后台标记支付、验证自动发货、订单查询、
仪表盘、接口文档、参数拼装与在线调试（内部 + HTTP 两种模式）。
"""

import json
import os
import sys

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000").rstrip("/")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_run")
os.makedirs(OUT, exist_ok=True)

CONTACT = "ui-tester@example.com"
JSON_HEADERS = {"Content-Type": "application/json"}


def _post_json(ctx, path, payload):
    r = ctx.request.post(BASE + path, data=json.dumps(payload), headers=JSON_HEADERS)
    return r.json()


def _get_json(ctx, path):
    return ctx.request.get(BASE + path).json()


def _make_keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv, pub


def ensure_payment_config(ctx):
    """确保存在可用的测试支付配置；不覆盖已配置的 AppId。"""
    cfg = _get_json(ctx, "/api/admin/pay-config")
    patch = {}

    if not cfg.get("app_id") or not cfg.get("private_key") or not cfg.get("alipay_public_key"):
        priv, pub = _make_keypair()
        patch.update({
            "app_id": cfg.get("app_id") or "2021000000000000",
            "private_key": priv,
            "alipay_public_key": pub,
        })
    if not cfg.get("merchant_id") or not cfg.get("merchant_key"):
        patch.update({
            "merchant_id": cfg.get("merchant_id") or "1234567890123456",
            "merchant_key": cfg.get("merchant_key") or "0123456789abcdef0123456789abcdef",
        })

    qr_on = bool(cfg.get("business_qr_mode"))
    qr_path = cfg.get("qr_code_path") or ""
    if qr_on and (not qr_path or not os.path.exists(qr_path)):
        patch["business_qr_mode"] = False
        qr_on = False
    if not qr_on and not cfg.get("transfer_user_id"):
        patch["transfer_user_id"] = "2088000000000000"

    if patch:
        _post_json(ctx, "/api/admin/pay-config", patch)

    health = _get_json(ctx, "/health")
    assert health.get("payment_ready"), f"支付未就绪: {health.get('payment_reason')}"


def ensure_product(ctx):
    products = _get_json(ctx, "/api/shop/public/products").get("products", [])
    for p in products:
        if p.get("fulfill_mode") == "generate" or int(p.get("stock_count") or 0) > 0:
            return p

    product = _post_json(ctx, "/api/admin/products", {
        "name": "UI 测试商品",
        "description": "浏览器冒烟测试使用，可删除",
        "price": 0.01,
        "fulfill_mode": "stock",
        "status": 1,
    })["product"]
    _post_json(ctx, f"/api/admin/products/{product['id']}/cards/import",
               {"text": "UI-CARD-1\nUI-CARD-2\nUI-CARD-3"})
    for p in _get_json(ctx, "/api/shop/public/products").get("products", []):
        if p.get("id") == product["id"]:
            return p
    raise AssertionError("测试商品创建失败")


def main():
    errors = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge", headless=True, args=["--no-sandbox"])
        except Exception:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])

        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.on("console", lambda m: errors.append(
            f"{m.text} @ {m.location}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("response", lambda r: errors.append(f"HTTP {r.status} {r.url}")
                if r.status >= 400 else None)
        page.on("requestfailed", lambda r: errors.append(f"FAILED {r.url}"))

        # ---------- 后台登录（勾选记住账号密码） ----------
        page.goto(BASE + "/admin/login", wait_until="networkidle")
        page.fill("#loginUser", "admin")
        page.fill("#loginPass", "admin123")
        page.check("#rememberMe")
        page.click("button[type=submit]")
        page.wait_for_url("**/admin/", timeout=15000)

        # 记住账号密码：同一上下文新开页面应自动填充
        page2 = context.new_page()
        page2.goto(BASE + "/admin/login", wait_until="networkidle")
        assert page2.input_value("#loginUser") == "admin", "账号未自动填充"
        assert page2.input_value("#loginPass") == "admin123", "密码未自动填充"
        assert page2.is_checked("#rememberMe"), "记住勾选状态丢失"
        page2.screenshot(path=os.path.join(OUT, "ui_login_remember.png"))
        page2.close()

        # ---------- 准备支付配置与商品 ----------
        ensure_payment_config(context)
        product = ensure_product(context)

        # ---------- 前台下单 ----------
        page.goto(f"{BASE}/shop/p/{product['id']}", wait_until="networkidle")
        page.wait_for_selector("#contact")
        assert page.locator("#contact[type=email]").count() == 1, "下单缺少邮箱输入"
        page.fill("#contact", CONTACT)
        page.click("#buyBtn")
        page.wait_for_url("**/pay/**", timeout=15000)

        trade_no = page.url.rstrip("/").split("/")[-1]
        page.wait_for_selector(".pay-qr img", timeout=10000)
        qr_src = page.get_attribute(".pay-qr img", "src") or ""
        assert qr_src.startswith("data:image") or qr_src.startswith("/api/codepay/qrcode"), \
            f"支付二维码未显示: {qr_src[:60]}"
        assert page.locator("#simulateBtn").count() == 0, "仍存在演示支付按钮"
        page.screenshot(path=os.path.join(OUT, "ui_pay_pending.png"))

        # ---------- 后台标记支付（模拟真实到账） ----------
        _post_json(context, f"/api/admin/orders/{trade_no}/status", {"status": 1})

        # 支付页自动轮询到已支付并展示卡密
        page.wait_for_selector("text=支付成功", timeout=20000)
        page.wait_for_selector("#cardsText", timeout=15000)
        page.screenshot(path=os.path.join(OUT, "ui_pay_success.png"))

        # ---------- 订单查询 ----------
        page.goto(BASE + "/orders", wait_until="networkidle")
        page.fill("#queryInput", CONTACT)
        page.click("#queryBtn")
        page.wait_for_selector("text=已发货", timeout=15000)

        # ---------- 仪表盘 ----------
        page.goto(BASE + "/admin/", wait_until="networkidle")
        page.wait_for_selector(".trend-col", timeout=15000)
        assert page.locator(".trend-col").count() == 7, "趋势图未渲染 7 天"
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(OUT, "ui_admin_dashboard.png"))

        # ---------- 接口文档 / 参数拼装 / 在线调试 ----------
        page.goto(BASE + "/admin/api-docs", wait_until="networkidle")
        assert page.locator(".docs-layout").count() == 1, "文档布局缺失"
        page.wait_for_selector("text=签名与认证", timeout=10000)
        page.screenshot(path=os.path.join(OUT, "ui_api_docs.png"))

        page.locator("#builder").scroll_into_view_if_needed()
        page.click("#buildBtn")
        page.wait_for_function(
            "document.getElementById('buildResult').textContent.includes('sign=')",
            timeout=15000,
        )
        page.click("#builder .tab[data-view='json']")
        page.wait_for_function(
            "document.getElementById('buildResult').textContent.includes('\"sign\"')",
            timeout=15000,
        )
        page.locator("#builder").screenshot(path=os.path.join(OUT, "ui_api_builder.png"))

        page.locator("#try").scroll_into_view_if_needed()
        page.click("#tryBtn")
        page.wait_for_function(
            "document.getElementById('tryResult').textContent.includes('response')",
            timeout=15000,
        )
        page.locator("#try").screenshot(path=os.path.join(OUT, "ui_api_try.png"))

        # HTTP 完整链路调试
        page.select_option("#tryAct", "query")
        page.check("#tryOverHttp")
        page.click("#tryBtn")
        page.wait_for_function(
            "document.getElementById('tryMeta').textContent.includes('HTTP 请求')",
            timeout=20000,
        )
        meta_text = page.locator("#tryMeta").inner_text()
        assert "状态 200" in meta_text, f"HTTP 调试状态异常: {meta_text}"
        page.locator("#try").screenshot(path=os.path.join(OUT, "ui_api_try_http.png"))

        # ---------- 批量操作控件 ----------
        page.goto(BASE + "/admin/orders", wait_until="networkidle")
        assert page.locator(".order-check").count() >= 1, "订单列表缺少批量选择"
        assert page.locator("text=批量补单").count() >= 1, "订单列表缺少批量补单"
        assert page.locator("th:has-text('回调')").count() == 1, "订单列表缺少回调状态列"
        page.check(".order-check >> nth=0")
        assert page.locator("#checkAll").count() == 1, "缺少全选"
        page.screenshot(path=os.path.join(OUT, "ui_admin_orders_batch.png"))

        page.goto(BASE + "/admin/shop-orders", wait_until="networkidle")
        assert page.locator(".shop-check").count() >= 1, "商城订单缺少批量选择"
        assert page.locator("text=批量补单").count() >= 1, "商城订单缺少批量补单"

        page.goto(BASE + "/admin/products", wait_until="networkidle")
        assert page.locator(".product-check").count() >= 1, "商品列表缺少批量选择"
        assert page.locator("text=批量删除").count() >= 1, "商品列表缺少批量删除"

        page.goto(BASE + "/admin/feedback", wait_until="networkidle")
        assert page.locator("text=批量关闭").count() >= 1, "工单缺少批量关闭"

        page.goto(BASE + "/admin/settings", wait_until="networkidle")
        assert page.locator("#email_enabled").count() == 1, "设置页缺少邮件开关"
        assert page.locator("#email_preset").count() == 1, "设置页缺少邮箱服务商"
        assert page.locator("#testEmailBtn").count() == 1, "设置页缺少测试邮件按钮"

        page.goto(BASE + "/admin/products", wait_until="networkidle")
        page.click("#newProductBtn")
        assert page.locator("#p_desc_editor").count() == 1, "商品编辑缺少富文本描述"
        assert page.locator("#p_icon_btn").count() == 1, "商品编辑缺少图标上传"
        page.locator("#productModal .actions .btn").first.click()

        # ---------- 后台各页面 ----------
        for path in ["/admin/pay-config", "/admin/products", "/admin/shop-orders",
                     "/admin/orders", "/admin/feedback", "/admin/settings",
                     "/admin/api-docs"]:
            page.goto(BASE + path, wait_until="networkidle")
            assert page.locator(".sidebar").count() == 1, f"{path} 后台布局缺失"

        browser.close()

    print("页面 console/page 错误:", errors if errors else "无")
    print("截图输出:", OUT)
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
