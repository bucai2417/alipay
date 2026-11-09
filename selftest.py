"""端到端自测：配置、商品、下单、支付页、发货、查询、客服、协议与后台。

使用独立数据目录 data_selftest，不影响正式数据。
支付到账由测试代码直接调用内部 mark_paid 模拟（不暴露任何演示接口）。
"""

import hashlib
import io
import json
import os
import shutil
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
TEST_DATA = os.path.join(BASE, "data_selftest")
if os.path.isdir(TEST_DATA):
    shutil.rmtree(TEST_DATA, ignore_errors=True)
os.environ["ALIPAY_DATA_DIR"] = TEST_DATA
os.environ["ALIPAY_MONITOR"] = "0"
os.environ["ALIPAY_DISABLE_QUICK_CHECK"] = "1"
sys.path.insert(0, BASE)

from web.factory import create_app  # noqa: E402

PASS, FAIL = 0, 0
IGNORED_SIGN_KEYS = {"sign", "sign_type", "act", "action", "format"}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name} {detail}")


def wait_until(fn, timeout=6.0, interval=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(interval)
    return False


def make_keypair():
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


def main():
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()

    # ---------- 登录与商品 ----------
    r = c.get("/api/admin/stats")
    check("未登录拦截", r.status_code == 401, r.status_code)

    r = c.post("/admin/login", data={"username": "admin", "password": "admin123"},
               follow_redirects=False)
    check("管理员登录", r.status_code == 302, r.status_code)

    r = c.post("/api/admin/products", json={
        "name": "自动生成商品", "description": "每单生成 2 条卡密",
        "price": 0.02, "fulfill_mode": "generate", "status": 1,
        "generate_config": {"prefix": "TEST-", "length": 8, "charset": "hex", "quantity": 2},
    })
    gen_product = r.get_json().get("product")
    check("创建自动生成商品", bool(gen_product and gen_product.get("id")))

    r = c.post("/api/admin/products", json={
        "name": "库存商品", "description": "库存卡密",
        "price": 0.01, "fulfill_mode": "stock", "status": 1,
    })
    stock_product = r.get_json().get("product")
    check("创建库存商品", bool(stock_product and stock_product.get("id")))

    r = c.post(f"/api/admin/products/{stock_product['id']}/cards/import",
               json={"text": "CARD-A\nCARD-B\nCARD-C\nCARD-A\n"})
    res = r.get_json()
    check("导入卡密去重", res.get("success") and res.get("imported") == 3
          and res.get("stock_count") == 3, res)

    # ---------- 商品图标与富文本描述 ----------
    from PIL import Image as _Image

    icon_buf = io.BytesIO()
    _Image.new("RGB", (64, 64), "#2563eb").save(icon_buf, "PNG")
    r = c.post("/api/admin/upload-image",
               data={"file": (io.BytesIO(icon_buf.getvalue()), "icon.png")},
               content_type="multipart/form-data")
    up = r.get_json()
    check("上传商品图片", up.get("success") and str(up.get("url", "")).startswith("/uploads/"), up)
    icon_url = up.get("url")
    r = c.get(icon_url)
    check("上传图片可访问", r.status_code == 200 and r.mimetype == "image/png", r.status_code)

    r = c.put(f"/api/admin/products/{gen_product['id']}", json={
        "description": '<p>富文本说明</p><script>alert(1)</script>'
                       f'<img src="{icon_url}" alt="示例">',
        "icon_path": icon_url,
    })
    p_desc = (r.get_json().get("product") or {}).get("description") or ""
    check("描述富文本清洗", "富文本说明" in p_desc and "<img" in p_desc
          and "<script" not in p_desc, p_desc)
    html = c.get(f"/shop/p/{gen_product['id']}").get_data(as_text=True)
    check("商品详情渲染图文", "富文本说明" in html and icon_url in html)

    from shop.guard import guard

    guard.max_requests = 1000
    guard.feedback_max_requests = 1000
    token = guard.issue_token()

    # ---------- 下单必须填写邮箱 ----------
    r = c.post("/api/shop/order", json={
        "token": token, "product_id": gen_product["id"], "qty": 1,
        "contact": "not-an-email",
    })
    check("下单强制邮箱", r.status_code == 400
          and "邮箱" in (r.get_json().get("msg") or ""), r.get_json())

    # ---------- 未配置支付时必须拒绝下单 ----------
    r = c.post("/api/shop/order", json={
        "token": token, "product_id": gen_product["id"], "qty": 1,
        "contact": "tester@example.com",
    })
    d = r.get_json()
    check("未配置支付禁止下单", r.status_code == 400
          and "支付未就绪" in (d.get("msg") or ""), d)

    # ---------- 配置真实支付宝（转账链接模式） ----------
    priv, pub = make_keypair()
    payload = {
        "app_id": "2021000000000000",
        "private_key": priv,
        "alipay_public_key": pub,
        "transfer_user_id": "2088000000000000",
        "merchant_id": "1234567890123456",
        "merchant_key": "0123456789abcdef0123456789abcdef",
        "business_qr_mode": False,
        "order_timeout": 300,
        "monitor_interval": 10,
        "query_minutes_back": 30,
        "pushplus_enabled": False,
    }
    r = c.post("/api/admin/pay-config", json=payload)
    check("保存支付配置", r.status_code == 200 and r.get_json().get("success"), r.data[:200])

    cfg = c.get("/api/admin/pay-config").get_json()
    check("配置回读", cfg.get("app_id") == payload["app_id"]
          and cfg.get("transfer_user_id") == payload["transfer_user_id"]
          and "sandbox_mode" not in cfg)
    h = c.get("/health").get_json()
    check("收款就绪", h.get("payment_ready") is True, h)

    # ---------- 商城下单（转账模式） ----------
    r = c.post("/api/shop/order", json={
        "token": token, "product_id": gen_product["id"], "qty": 1,
        "contact": "tester@example.com",
    })
    d = r.get_json()
    check("商城下单", r.status_code == 200 and d.get("code") == 1, d)
    order_no, trade_no = d.get("order_no"), d.get("trade_no")
    check("返回支付订单号", bool(trade_no))

    html = c.get(f"/pay/{trade_no}").get_data(as_text=True)
    check("转账支付页二维码", "data:image/png;base64," in html, html[:200])
    check("支付页无演示按钮", "模拟支付" not in html)

    from alipay_pay.monitor import mark_paid
    from alipay_pay.orders import OrderStore

    res = mark_paid(trade_no, detected_by="selftest")
    check("标记支付成功", res.get("changed") is True, res)

    st = c.get(f"/api/pay/status/{trade_no}").get_json()
    check("支付状态已支付", st.get("paid") is True, st)
    check("商城已发货", st.get("shop_status") == "fulfilled", st)
    check("卡密生成 2 条",
          len([x for x in (st.get("shop_cards") or "").splitlines() if x]) == 2,
          st.get("shop_cards"))

    r = c.post("/api/shop/query", json={"token": token, "query": "tester@example.com"})
    q = r.get_json()
    check("备注查询订单", q.get("code") == 1 and q.get("orders"), q)
    check("查询结果含卡密", bool((q.get("orders") or [{}])[0].get("cards")))

    # ---------- 库存商品下单 ----------
    r = c.post("/api/shop/order", json={
        "token": token, "product_id": stock_product["id"], "qty": 1,
        "contact": "buyer2@example.com",
    })
    d2 = r.get_json()
    check("库存商品下单", d2.get("code") == 1, d2)
    mark_paid(d2.get("trade_no"), detected_by="selftest")
    o2 = c.get(f"/api/shop/order/{d2.get('order_no')}").get_json().get("order") or {}
    check("库存卡密发货", o2.get("status") == "fulfilled"
          and (o2.get("cards") or "").strip() != "", o2)

    # ---------- 客服 ----------
    r = c.post("/api/shop/feedback/start", json={"token": token, "contact": "tester@example.com"})
    fb = r.get_json()
    check("客服会话创建", fb.get("code") == 1 and fb.get("ticket"), fb)
    ticket_id = (fb.get("ticket") or {}).get("id")
    r = c.post("/api/shop/feedback/send", json={
        "token": token, "contact": "tester@example.com", "content": "商品没有收到卡密",
    })
    check("用户发送消息", r.get_json().get("code") == 1)
    r = c.post(f"/api/admin/feedback/tickets/{ticket_id}/reply", json={"content": "已为您补发"})
    check("管理员回复", r.get_json().get("code") == 1)
    r = c.post("/api/shop/feedback/pull", json={"token": token, "contact": "tester@example.com"})
    msgs = r.get_json().get("messages") or []
    check("用户拉取到回复", any(m.get("sender") == "admin" for m in msgs), msgs)

    # ---------- 码支付协议 ----------
    pid, key = payload["merchant_id"], payload["merchant_key"]

    def sign(p):
        s = "&".join(
            f"{k}={p[k]}" for k in sorted(p)
            if k not in IGNORED_SIGN_KEYS and str(p[k]) != ""
        )
        return hashlib.md5((s + key).encode()).hexdigest()

    r = c.get("/api/codepay", query_string={"act": "query", "pid": pid, "key": key})
    check("协议 query", r.get_json().get("code") == 1, r.get_json())

    p = {"pid": pid, "type": "alipay", "out_trade_no": "EXT-0001",
         "name": "外部订单", "money": "0.03", "format": "json"}
    p["sign"] = sign(p)
    p["act"] = "submit"
    r = c.post("/api/codepay", data=p)
    pr = r.get_json()
    check("协议 submit 转账链接", pr.get("code") == 1
          and str(pr.get("payment_url", "")).startswith("https://render.alipay.com")
          and len(pr.get("qr_code") or "") > 100, pr)

    r = c.get("/api/codepay", query_string={
        "act": "order", "pid": pid, "key": key, "out_trade_no": "EXT-0001"})
    check("协议 order 待支付", r.get_json().get("status") == 0, r.get_json())

    bad = {"pid": pid, "type": "alipay", "out_trade_no": "EXT-0002",
           "name": "坏签名", "money": "0.03", "sign": "deadbeef", "act": "submit"}
    r = c.post("/api/codepay", data=bad)
    check("协议拒绝错误签名", r.get_json().get("code") == -1, r.get_json())

    n = {"out_trade_no": "EXT-0001", "trade_no": pr.get("trade_no"),
         "trade_status": "TRADE_SUCCESS", "name": "外部订单", "money": "0.03"}
    n["sign"] = sign(n)
    r = c.post("/api/codepay", data=dict(n, act="notify"))
    check("协议 notify", r.status_code == 200 and r.get_data(as_text=True) == "success", r.data)
    r = c.get("/api/codepay", query_string={
        "act": "order", "pid": pid, "key": key, "out_trade_no": "EXT-0001"})
    check("notify 后已支付", r.get_json().get("status") == 1, r.get_json())

    # ---------- 经营码模式 ----------
    from PIL import Image as _Image

    buf = io.BytesIO()
    _Image.new("RGB", (64, 64), "white").save(buf, "PNG")
    r = c.post("/api/admin/upload-qr",
               data={"file": (io.BytesIO(buf.getvalue()), "qr.png")},
               content_type="multipart/form-data")
    up = r.get_json()
    check("上传经营码", up.get("success") and up.get("path"), up)
    uploaded_path = up.get("path")
    check("二维码文件存在", bool(uploaded_path) and os.path.exists(uploaded_path))
    r = c.get("/api/admin/qr-image", query_string={"path": uploaded_path})
    check("二维码预览", r.status_code == 200 and r.mimetype == "image/png", r.status_code)

    # 部分更新：只提交经营码相关字段，验证不会清空其他配置
    r = c.post("/api/admin/pay-config", json={
        "business_qr_mode": True, "qr_code_path": uploaded_path,
        "amount_offset": 0.01, "match_tolerance": 300,
    })
    check("部分更新配置", r.get_json().get("success"))
    cfg2 = c.get("/api/admin/pay-config").get_json()
    check("增量保存不丢配置", cfg2.get("app_id") == payload["app_id"]
          and cfg2.get("transfer_user_id") == payload["transfer_user_id"]
          and cfg2.get("business_qr_mode") is True, cfg2)

    p = {"pid": pid, "type": "alipay", "out_trade_no": "EXT-QR-1",
         "name": "经营码订单", "money": "1.00", "format": "json"}
    p["sign"] = sign(p)
    p["act"] = "submit"
    r1 = c.post("/api/codepay", data=p)
    pr1 = r1.get_json()
    check("经营码订单创建", pr1.get("code") == 1
          and pr1.get("business_qr_mode") is True
          and pr1.get("qr_code_url") == "/api/codepay/qrcode"
          and len(pr1.get("qr_code") or "") > 100, pr1)

    html = c.get(f"/pay/{pr1.get('trade_no')}").get_data(as_text=True)
    check("经营码支付页显示二维码", "data:image/png;base64," in html and "精确金额" in html)
    r = c.get("/api/codepay/qrcode")
    check("经营码图片接口", r.status_code == 200 and r.mimetype == "image/png", r.status_code)

    p2 = dict(p)
    p2["out_trade_no"] = "EXT-QR-2"
    p2.pop("sign", None)
    p2["sign"] = sign(p2)
    pr2 = c.post("/api/codepay", data=p2).get_json()
    check("经营码金额唯一", pr2.get("code") == 1
          and pr1.get("payment_amount") != pr2.get("payment_amount"),
          (pr1.get("payment_amount"), pr2.get("payment_amount")))

    # ---------- 后台接口 ----------
    r = c.get("/api/admin/stats")
    st = r.get_json()
    check("后台统计", st.get("payment_ready") is True and st.get("pay", {}).get("total") >= 3, st)
    r = c.get("/api/admin/shop-orders")
    check("后台商城订单列表", r.status_code == 200 and r.get_json().get("total") == 2)
    r = c.get("/api/admin/orders")
    check("后台码支付订单列表", r.status_code == 200 and r.get_json().get("total") >= 4)
    r = c.get("/api/admin/health")
    h = r.get_json()
    check("健康检查", r.status_code == 200 and h.get("config", {}).get("app_id") == "OK"
          and h.get("payment_ready") is True, h.get("config"))

    # ---------- 趋势 / 导出 / 文档 / 参数拼装 / 在线调试 ----------
    r = c.get("/api/admin/stats/trend?days=7")
    trend = r.get_json()
    check("收入趋势", r.status_code == 200 and len(trend.get("items", [])) == 7)

    r = c.get("/api/admin/orders/export")
    csv_text = r.get_data(as_text=True)
    check("码支付订单导出", r.status_code == 200 and "平台订单号" in csv_text
          and "EXT-0001" in csv_text)

    r = c.get("/api/admin/shop-orders/export")
    check("商城订单导出", r.status_code == 200
          and "tester@example.com" in r.get_data(as_text=True))

    r = c.get("/admin/api-docs")
    html = r.get_data(as_text=True)
    check("接口文档页", r.status_code == 200 and "签名与认证" in html
          and "参数拼装" in html and "在线调试" in html
          and "对接配置清单" in html
          and payload["merchant_id"] in html)
    check("文档页无演示内容", "演示模式" not in html and "模拟支付" not in html)
    check("文档说明 notify_url 归属",
          "notify_url 怎么填" in html and "不是本系统地址" in html)

    r = c.get("/health")
    check("安全响应头", r.headers.get("X-Content-Type-Options") == "nosniff")

    r = c.post("/api/admin/api-sign", json={
        "act": "submit",
        "params": {"out_trade_no": "SIGN-1", "name": "签名测试", "money": "0.03"},
    })
    d = r.get_json()
    form_str = d.get("form_string") or ""
    check("参数拼装 submit", d.get("success") and form_str.startswith("act=submit&")
          and "sign=" in form_str and '"sign"' in (d.get("json") or ""), d)

    r = c.post("/api/admin/api-sign", json={"act": "order", "params": {"out_trade_no": "SIGN-1"}})
    d = r.get_json()
    check("参数拼装 order", d.get("success")
          and "sign=" not in (d.get("form_string") or ""), d)

    anon = app.test_client()
    r = anon.post("/api/admin/api-sign", json={"act": "submit", "params": {}})
    check("参数拼装需登录", r.status_code == 401, r.status_code)

    r = c.post("/api/admin/api-try", json={"act": "health", "params": {}})
    tr = r.get_json()
    check("调试 health", tr.get("success") and tr.get("response", {}).get("payment_ready") is True,
          tr)

    r = c.post("/api/admin/api-try", json={"act": "query", "params": {}})
    tr = r.get_json()
    check("调试 query", tr.get("success") and tr.get("response", {}).get("code") == 1, tr)
    check("调试返回耗时", isinstance(tr.get("meta", {}).get("elapsed_ms"), (int, float)), tr.get("meta"))

    r = c.post("/api/admin/api-try", json={
        "act": "submit", "params": {"out_trade_no": "TRY-0001", "name": "调试单", "money": "0.02"}})
    tr = r.get_json()
    check("调试 submit", tr.get("success") and tr.get("response", {}).get("code") == 1
          and tr["response"].get("trade_no"), tr)

    r = c.post("/api/admin/api-try", json={"act": "order", "params": {"out_trade_no": "TRY-0001"}})
    check("调试 order 待支付", r.get_json().get("response", {}).get("status") == 0)

    r = c.post("/api/admin/api-try", json={"act": "notify", "params": {"out_trade_no": "TRY-0001"}})
    check("调试 notify", r.get_json().get("response", {}).get("code") == 1, r.get_json())
    r = c.post("/api/admin/api-try", json={"act": "order", "params": {"out_trade_no": "TRY-0001"}})
    check("调试后订单已支付", r.get_json().get("response", {}).get("status") == 1)

    # ================= 回调、补单与批量操作 =================
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs

    class _CallbackHandler(BaseHTTPRequestHandler):
        received = []

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8", "replace")
            data = {k: v[0] for k, v in parse_qs(body).items()}
            type(self).received.append(data)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"success")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    cb_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def submit_order(out_no, money, notify_url=""):
        pl = {"pid": pid, "type": "alipay", "out_trade_no": out_no,
              "name": "回调测试", "money": money, "notify_url": notify_url,
              "format": "json"}
        pl["sign"] = sign(pl)
        pl["act"] = "submit"
        return c.post("/api/codepay", data=pl).get_json()

    # 成功回调（异步发送，等待到达）
    cb = submit_order("CB-0001", "0.06", f"http://127.0.0.1:{cb_port}/cb")
    check("回调用例订单", cb.get("code") == 1, cb)
    mark_paid(cb["trade_no"], detected_by="selftest")
    wait_until(lambda: len(_CallbackHandler.received) >= 1, timeout=8)
    check("回调已收到", len(_CallbackHandler.received) == 1, _CallbackHandler.received)
    if _CallbackHandler.received:
        data = _CallbackHandler.received[-1]
        check("回调字段", data.get("trade_status") == "TRADE_SUCCESS"
              and data.get("out_trade_no") == "CB-0001"
              and data.get("sign_type") == "MD5", data)
        raw = "&".join(
            f"{k}={data[k]}" for k in sorted(data)
            if k not in ("sign", "sign_type", "act", "action", "format")
            and str(data[k]) != ""
        )
        check("回调签名正确",
              data.get("sign") == hashlib.md5((raw + key).encode()).hexdigest(),
              data.get("sign"))
    wait_until(lambda: (OrderStore().get_order(order_id=cb["trade_no"]) or {})
               .get("notify_status") == 1, timeout=6)
    order_cb = OrderStore().get_order(order_id=cb.get("trade_no"))
    check("回调状态已记录", bool(order_cb) and order_cb.get("notify_status") == 1, order_cb)

    # 失败回调自动登记重试（异步）
    fail = submit_order("CB-FAIL", "0.06", "http://127.0.0.1:9/cb")
    mark_paid(fail["trade_no"], detected_by="selftest")
    wait_until(lambda: (OrderStore().get_order(order_id=fail["trade_no"]) or {})
               .get("notify_status") == 2, timeout=8)
    order_fail = OrderStore().get_order(order_id=fail["trade_no"])
    check("回调失败登记重试", bool(order_fail)
          and order_fail.get("notify_status") == 2
          and int(order_fail.get("notify_attempts") or 0) >= 1
          and bool(order_fail.get("notify_next_at")), order_fail)

    # 手动重发回调
    r = c.post(f"/api/admin/orders/{cb['trade_no']}/notify")
    check("重发回调成功", r.status_code == 200 and r.get_json().get("success"), r.get_json())
    r = c.post(f"/api/admin/orders/{fail['trade_no']}/notify")
    check("重发回调失败提示", r.status_code == 400, r.status_code)
    order_fail2 = OrderStore().get_order(order_id=fail["trade_no"])
    check("重试次数递增", int(order_fail2.get("notify_attempts") or 0) >= 2, order_fail2)

    # 兼容路径
    compat = {"pid": pid, "type": "alipay", "out_trade_no": "COMPAT-1",
              "name": "兼容路径", "money": "0.02", "format": "json"}
    compat["sign"] = sign(compat)
    compat["act"] = "submit"
    r = c.post("/submit.php", data=compat)
    check("兼容 /submit.php", r.status_code == 200 and r.get_json().get("code") == 1,
          r.get_json())
    r = c.get("/api.php", query_string={"act": "query", "pid": pid, "key": key})
    check("兼容 /api.php", r.get_json().get("code") == 1, r.get_json())

    # 批量：码支付订单
    b1 = submit_order("BATCH-1", "0.02")
    b2 = submit_order("BATCH-2", "0.02")
    batch_ids = [b1["trade_no"], b2["trade_no"]]
    r = c.post("/api/admin/orders/batch", json={"action": "expire", "ids": batch_ids})
    check("批量置过期", r.get_json().get("affected") == 2, r.get_json())
    r = c.post("/api/admin/orders/batch", json={"action": "mark_paid", "ids": batch_ids})
    check("已过期不可补单", r.get_json().get("affected") == 0, r.get_json())
    r = c.post("/api/admin/orders/batch", json={"action": "delete", "ids": batch_ids})
    check("批量删除订单", r.get_json().get("affected") == 2, r.get_json())

    bp = submit_order("BATCH-PAY", "0.02")
    r = c.post("/api/admin/orders/batch", json={"action": "mark_paid", "ids": [bp["trade_no"]]})
    check("批量补单", r.get_json().get("affected") == 1, r.get_json())
    check("补单后已支付",
          OrderStore().get_order(order_id=bp["trade_no"])["status"] == 1)

    # 批量：商城订单
    r = c.post("/api/shop/order", json={
        "token": token, "product_id": stock_product["id"], "qty": 1,
        "contact": "batch@example.com"})
    bs = r.get_json()
    check("批量商城订单创建", bs.get("code") == 1, bs)
    r = c.post("/api/admin/shop-orders/batch",
               json={"action": "mark_paid", "ids": [bs["order_no"]]})
    check("批量商城补单", r.get_json().get("affected") == 1, r.get_json())
    check("批量补单后发货",
          c.get(f"/api/shop/order/{bs['order_no']}").get_json()
          .get("order", {}).get("status") == "fulfilled")

    r = c.post("/api/shop/order", json={
        "token": token, "product_id": stock_product["id"], "qty": 1,
        "contact": "batch2@example.com"})
    bs2 = r.get_json()
    r = c.post("/api/admin/shop-orders/batch",
               json={"action": "delete", "ids": [bs2["order_no"]]})
    check("批量删除商城订单", r.get_json().get("affected") == 1, r.get_json())

    # 批量：商品上下架/删除
    p1 = c.post("/api/admin/products", json={
        "name": "批量商品1", "price": 0.01, "fulfill_mode": "stock", "status": 1,
    }).get_json()["product"]
    p2 = c.post("/api/admin/products", json={
        "name": "批量商品2", "price": 0.01, "fulfill_mode": "stock", "status": 1,
    }).get_json()["product"]
    r = c.post("/api/admin/products/batch",
               json={"action": "off_sale", "ids": [p1["id"], p2["id"]]})
    check("批量下架商品", r.get_json().get("affected") == 2, r.get_json())
    r = c.post("/api/admin/products/batch",
               json={"action": "delete", "ids": [p1["id"], p2["id"]]})
    check("批量删除商品", r.get_json().get("deleted") == 2, r.get_json())

    # 批量：客服工单
    fb3 = c.post("/api/shop/feedback/start",
                 json={"token": token, "contact": "buyer2@example.com"}).get_json()
    fb4 = c.post("/api/shop/feedback/start",
                 json={"token": token, "contact": "batch@example.com"}).get_json()
    tid3 = (fb3.get("ticket") or {}).get("id")
    tid4 = (fb4.get("ticket") or {}).get("id")
    r = c.post("/api/admin/feedback/tickets/batch",
               json={"action": "close", "ids": [tid3, tid4]})
    check("批量关闭工单", r.get_json().get("affected") == 2, r.get_json())
    r = c.post("/api/admin/feedback/tickets/batch",
               json={"action": "delete", "ids": [tid4]})
    check("批量删除工单", r.get_json().get("affected") == 1, r.get_json())

    server.shutdown()

    # ================= 并发能力 =================
    from concurrent.futures import ThreadPoolExecutor

    def submit_concurrent(i):
        cl = app.test_client()
        pl = {"pid": pid, "type": "alipay", "out_trade_no": f"CONC-{i}",
              "name": "并发下单", "money": "0.02", "format": "json"}
        pl["sign"] = sign(pl)
        pl["act"] = "submit"
        return cl.post("/api/codepay", data=pl).get_json()

    with ThreadPoolExecutor(max_workers=8) as pool:
        conc = list(pool.map(submit_concurrent, range(10)))
    ok_conc = [r for r in conc if r.get("code") == 1]
    amounts = [r.get("payment_amount") for r in ok_conc]
    check("并发下单全部成功", len(ok_conc) == 10, conc)
    check("并发下单金额唯一", len(set(amounts)) == 10, amounts)

    conc_pay = submit_order("CONC-PAY", "0.02")

    def pay_once(_):
        return mark_paid(conc_pay["trade_no"], detected_by="concurrent")

    with ThreadPoolExecutor(max_workers=5) as pool:
        pays = list(pool.map(pay_once, range(5)))
    check("并发补单仅生效一次", sum(1 for r in pays if r.get("changed")) == 1, pays)

    conc_product = c.post("/api/admin/products", json={
        "name": "并发发货商品", "price": 0.01, "fulfill_mode": "stock", "status": 1,
    }).get_json()["product"]
    c.post(f"/api/admin/products/{conc_product['id']}/cards/import",
           json={"text": "CONC-CARD-1\nCONC-CARD-2\nCONC-CARD-3"})
    rr = c.post("/api/shop/order", json={
        "token": token, "product_id": conc_product["id"], "qty": 1,
        "contact": "conc@example.com"})
    cd = rr.get_json()
    check("并发发货订单创建", cd.get("code") == 1, cd)

    from shop.service import ShopService as _ShopService

    svc = _ShopService()

    def fulfill_once(_):
        ord_obj = svc.db.get_order_by_no(cd.get("order_no"))
        return svc.fulfill_order(ord_obj, notify=True)

    with ThreadPoolExecutor(max_workers=5) as pool:
        fres = list(pool.map(fulfill_once, range(5)))
    ok_f = [x for x in fres if x.get("code") == 1 or x.get("msg") == "ALREADY_FULFILLED"]
    check("并发发货全部幂等", len(ok_f) == 5, fres)
    final_o = svc.db.get_order_by_no(cd.get("order_no"))
    lines = [x for x in (final_o.get("card_payload") or "").splitlines() if x]
    check("并发发货只发一份", len(lines) == 1, lines)

    # ================= 邮件通知 =================
    from unittest.mock import patch

    from core.storage import db as _db
    from shop.service import ShopService

    mail_product = c.post("/api/admin/products", json={
        "name": "邮件测试商品", "price": 0.01, "fulfill_mode": "stock", "status": 1,
    }).get_json()["product"]
    c.post(f"/api/admin/products/{mail_product['id']}/cards/import",
           json={"text": "MAIL-1\nMAIL-2\nMAIL-3\nMAIL-4"})

    def buy_mail_product(email_addr):
        rr = c.post("/api/shop/order", json={
            "token": token, "product_id": mail_product["id"], "qty": 1,
            "contact": email_addr,
        })
        dd = rr.get_json()
        if dd.get("code") == 1:
            mark_paid(dd["trade_no"], detected_by="selftest")
        return dd

    # 邮件未启用时的订单状态（异步更新）
    off = buy_mail_product("mail-off@example.com")
    check("邮件测试订单1", off.get("code") == 1, off)
    if off.get("order_no"):
        wait_until(lambda: (ShopService().db.get_order_by_no(off["order_no"]) or {})
                   .get("email_status") == 3, timeout=6)
    o_off = (ShopService().db.get_order_by_no(off.get("order_no"))
             if off.get("order_no") else None)
    check("未启用邮件状态", bool(o_off) and o_off.get("email_status") == 3, o_off)

    # 保存邮件配置
    r = c.post("/api/admin/email-config", json={
        "enabled": True, "preset": "custom", "host": "smtp.test.local", "port": 465,
        "security": "ssl", "username": "shop@test.local", "password": "smtp-secret",
        "from_name": "测试商城", "from_email": "shop@test.local",
    })
    check("保存邮件配置", r.get_json().get("success"), r.get_json())
    ecfg = c.get("/api/admin/email-config").get_json().get("config") or {}
    check("邮件配置回读", ecfg.get("enabled") is True
          and ecfg.get("password") == "smtp-secret", ecfg.get("password"))
    raw_email = _db.settings_get("email_config") or {}
    check("邮件密码加密存储", str(raw_email.get("password", "")).startswith("ENC2:"))

    # 发货邮件（mock SMTP）
    with patch("smtplib.SMTP_SSL") as mock_ssl:
        inst = mock_ssl.return_value
        on = buy_mail_product("mail-on@example.com")
        check("邮件测试订单2", on.get("code") == 1, on)
        if on.get("order_no"):
            wait_until(lambda: (ShopService().db.get_order_by_no(on["order_no"]) or {})
                       .get("email_status") in (1, 2), timeout=10)
        o_on = (ShopService().db.get_order_by_no(on.get("order_no"))
                if on.get("order_no") else None)
        check("发货邮件已发送", bool(o_on) and o_on.get("email_status") == 1
              and bool(o_on.get("email_sent_at")), o_on)
        check("SMTP 已登录", inst.login.called)
        msg = inst.send_message.call_args[0][0] if inst.send_message.called else None
        check("邮件收件人正确", msg is not None and msg.get("To") == "mail-on@example.com")
        check("SMTP 主机正确", mock_ssl.call_args[0][0] == "smtp.test.local")

    # 测试邮件接口
    with patch("smtplib.SMTP_SSL") as mock_ssl2:
        r = c.post("/api/admin/email-test", json={"to": "test@example.com"})
        check("测试邮件接口", r.get_json().get("success")
              and mock_ssl2.return_value.send_message.called, r.get_json())

    # 关闭邮件，避免后续测试触发真实连接
    c.post("/api/admin/email-config", json={"enabled": False})

    # ---------- 页面渲染 ----------
    for path, needle in [("/", "自动生成商品"), ("/shop", "自动生成商品"),
                         ("/orders", "订单查询"), ("/support", "联系客服")]:
        r = c.get(path)
        check(f"页面 {path}", r.status_code == 200 and needle in r.get_data(as_text=True),
              r.status_code)

    for path in ["/admin/", "/admin/pay-config", "/admin/orders", "/admin/products",
                 "/admin/shop-orders", "/admin/feedback", "/admin/settings",
                 "/admin/api-docs"]:
        r = c.get(path)
        check(f"后台页面 {path}", r.status_code == 200, r.status_code)

    # ================= MCP 服务 =================
    from core.settings import get_mcp_settings, reset_mcp_token, set_mcp_enabled

    mcp_token = get_mcp_settings()["token"]
    mcp_headers = {"Authorization": f"Bearer {mcp_token}"}

    def mcp_call(method, params=None, token="__default__"):
        if token == "__default__":
            token = mcp_token
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return c.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                    "method": method, "params": params or {}},
                      headers=headers)

    def mcp_tool(name, args=None, token="__default__"):
        return mcp_call("tools/call", {"name": name, "arguments": args or {}}, token=token)

    def tool_text(resp):
        result = resp.get_json().get("result") or {}
        content = result.get("content") or []
        return (content[0].get("text") if content else ""), result.get("isError")

    r = mcp_call("initialize", token=None)
    check("MCP 未授权拦截", r.status_code == 401, r.status_code)

    r = mcp_call("initialize")
    d = r.get_json()
    check("MCP initialize", r.status_code == 200
          and d.get("result", {}).get("protocolVersion")
          and "tools" in d.get("result", {}).get("capabilities", {}), d)

    r = c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
               headers=mcp_headers)
    check("MCP initialized 通知", r.status_code == 202, r.status_code)

    r = mcp_call("tools/list")
    names = [t["name"] for t in r.get_json().get("result", {}).get("tools", [])]
    check("MCP 工具列表", len(names) >= 35 and "create_product" in names
          and "mark_order_paid" in names and "update_pay_config" in names, len(names))

    r = mcp_tool("get_stats")
    text, is_err = tool_text(r)
    check("MCP get_stats", is_err is False and '"pay"' in text, text[:120])

    r = mcp_tool("get_pay_config")
    text, is_err = tool_text(r)
    check("MCP 配置脱敏", is_err is False and "…" in text
          and payload["private_key"] not in text, text[:120])

    r = mcp_tool("create_product", {"name": "MCP测试商品", "price": 0.02,
                                    "fulfill_mode": "stock", "status": 1})
    product_data = json.loads(tool_text(r)[0])
    check("MCP 创建商品", bool(product_data.get("id")), product_data)
    mcp_product_id = product_data.get("id")

    r = mcp_tool("import_cards", {"product_id": mcp_product_id,
                                  "codes": ["MCP-1", "MCP-2"]})
    check("MCP 导入卡密", tool_text(r)[1] is False)

    r = mcp_tool("list_products", {"search": "MCP测试商品"})
    listed = json.loads(tool_text(r)[0])
    check("MCP 查询商品", listed.get("total") == 1, listed)

    r = mcp_tool("update_product", {"product_id": mcp_product_id, "price": 0.03})
    check("MCP 更新商品", json.loads(tool_text(r)[0]).get("price") == 0.03)

    r = mcp_tool("delete_products", {"ids": [mcp_product_id]})
    check("MCP 删除商品", json.loads(tool_text(r)[0]).get("deleted") == 1)

    mcp_order = submit_order("MCP-PAY-1", "0.02")
    r = mcp_tool("mark_order_paid", {"trade_no": mcp_order["trade_no"]})
    check("MCP 补单", tool_text(r)[1] is False)
    r = mcp_tool("get_order", {"trade_no": mcp_order["trade_no"]})
    order_view = json.loads(tool_text(r)[0])
    check("MCP 补单后已支付", order_view.get("status") == 1, order_view)

    r = mcp_tool("list_tickets", {})
    tickets = json.loads(tool_text(r)[0])
    check("MCP 工单列表", tickets.get("total", 0) >= 1, tickets)
    if tickets.get("tickets"):
        tid = tickets["tickets"][0]["id"]
        r = mcp_tool("reply_ticket", {"ticket_id": tid, "content": "MCP 回复测试"})
        check("MCP 回复工单", tool_text(r)[1] is False)

    r = mcp_call("resources/list")
    uris = [x.get("uri") for x in r.get_json().get("result", {}).get("resources", [])]
    check("MCP 资源列表", "alipay://readme" in uris, uris)
    r = mcp_call("resources/read", {"uri": "alipay://readme"})
    contents = r.get_json().get("result", {}).get("contents") or []
    check("MCP 读取资源", bool(contents and contents[0].get("text")))

    r = mcp_call("unknown/method")
    check("MCP 未知方法", r.get_json().get("error", {}).get("code") == -32601)
    r = mcp_tool("no_such_tool", {})
    check("MCP 未知工具", tool_text(r)[1] is True)

    old_token = mcp_token
    mcp_token = reset_mcp_token()["token"]
    check("MCP 旧 Token 失效", mcp_call("tools/list", token=old_token).status_code == 401)
    check("MCP 新 Token 可用", mcp_call("tools/list").status_code == 200)
    set_mcp_enabled(False)
    check("MCP 禁用拦截", mcp_call("tools/list").status_code == 403)
    set_mcp_enabled(True)

    # ---------- 商城关闭 ----------
    c.post("/api/admin/shop-settings", json={"enabled": False})
    r = c.post("/api/shop/order", json={
        "token": token, "product_id": gen_product["id"], "qty": 1, "contact": "x@example.com"})
    check("商城关闭拦截", r.get_json().get("code") == -1, r.get_json())
    c.post("/api/admin/shop-settings", json={"enabled": True})

    # ---------- 登录失败限速（最后执行） ----------
    for _ in range(5):
        c.post("/admin/login", data={"username": "nobody", "password": "wrong"})
    r = c.post("/admin/login", data={"username": "admin", "password": "admin123"})
    check("登录失败限速", r.status_code == 200
          and "尝试次数过多" in r.get_data(as_text=True))

    print("\n结果: 通过 %d, 失败 %d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
