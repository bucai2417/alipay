# 码支付 · 卡密商城（独立版）

基于支付宝账单查询的**码支付系统**，内置卡密商城、管理后台与客服工单。不依赖任何外部框架，单个 Flask 进程即可运行。

> 支付侦测原理：用户扫码转账（备注=订单号）或扫描经营码支付唯一金额后，后台定时查询支付宝账户流水（`alipay.data.bill.accountlog.query`），命中后自动标记支付、商城自动发货、回调商户。

---

## 功能

- **码支付协议**：兼容 CodePay 协议（`/api/codepay`），支持 `query / order / orders / submit / notify`，MD5 签名。
- **两种收款方式**
  - 转账链接模式：生成带备注的支付宝转账二维码，按备注 + 金额匹配；
  - 经营码模式：固定收款码 + 每单唯一金额，按金额匹配，适合个人收款码。
- **自动支付监控**：后台线程按配置间隔查询账单，修改配置即时生效。
- **卡密商城**：商品管理、库存卡密批量导入、下单自动生成卡密、支付后自动发货、订单查询、客服工单。
- **商品展示**：商品可配置图标（可选）；商品描述支持富文本与内嵌图片（服务端白名单清洗后展示）。
- **邮件通知**：后台可配置主流邮箱 SMTP（QQ / 163 / 126 / 阿里云 / 腾讯企业邮 / Gmail / Outlook / 自定义）；买家下单必须填写邮箱，启用后发货自动发送订单与卡密邮件，后台可查看发送状态并测试发送。
- **批量操作**：订单、商城订单、商品、客服工单均支持批量选择，可批量补单、补发、上下架、关闭、删除。
- **补单**：支付成功但未自动匹配/发货时，可在后台对码支付订单或商城订单手动补单，补单会触发商城发货与商户回调。
- **商户回调**：支付成功自动回调 `notify_url`（POST 表单 + MD5 签名，返回 `success`），失败按 1/5/15/30/60 分钟重试，最多 5 次；后台可查看回调状态并手动重发。
- **管理后台**：概览（含 7 日收入趋势图）、支付配置、订单（支持 CSV 导出）、商品/卡密、商城订单（导出）、客服、系统设置；敏感字段加密存储；登录页支持记住账号密码（仅保存在本机浏览器）。
- **开发接口页**：`/admin/api-docs` 提供完整的 CodePay 协议文档（签名规则、参数表、curl/Python 示例、回调验签示例）、参数拼装（自动补 pid/签名，字符与 JSON 双格式一键复制）与在线调试（支持进程内执行与 HTTP 完整链路两种模式，展示状态码/耗时，结果支持 JSON/字符双视图）。
- **安全**：后台登录失败限速、会话 Cookie HttpOnly/SameSite、常用安全响应头。
- **PushPlus 通知**：支付成功、商城成交通知。

---

## 快速开始

```bash
# 1. 安装依赖（Python 3.9+）
pip install -r requirements.txt

# 2. 启动
python app.py
```

- 商城前台：<http://localhost:5000/>
- 管理后台：<http://localhost:5000/admin/>（默认 `admin / admin123`，登录后请立即修改）
- 开发接口文档：<http://localhost:5000/admin/api-docs>
- 健康检查：<http://localhost:5000/health>

Windows 可直接双击 `start.bat`：首次会自动创建 `.venv` 并安装依赖，之后直接启动；端口被占用时会提示并打开已有服务，不会闪退。停止服务可双击 `stop.bat`。Linux/macOS 执行 `./start.sh`。

首次启动会创建 `data/`：数据库 `alipay.db`、加密密钥 `.secret_key`、经营码图片目录 `qrcode/`、日志 `logs/app.log`。

首次使用请先在「后台 → 支付配置」完成支付宝配置，否则商城下单会提示「支付未就绪」。

---

## 支付配置

到「后台 → 支付配置」填写：

| 配置 | 说明 |
| --- | --- |
| AppId | 支付宝开放平台应用 AppId |
| 应用私钥 | 支持 PKCS#1 / PKCS#8 / 纯 base64，自动归一 |
| 支付宝公钥 | 开放平台「支付宝公钥」 |
| 转账收款账号 userId | 转账链接模式必填（收款支付宝账号的 2088 开头 userId） |
| 商户 ID / 密钥 | 点击「自动生成商户凭据」后保存，供外部站点对接 |
| 经营码二维码 | 经营码模式必填，在配置页上传收款二维码图片 |

保存配置后等待一个监控周期（默认 10 秒）即可开始收款。配置页顶部会显示「支付已就绪 / 未就绪」及缺失项。

**应用需要开通的权限**：账户账单查询（`alipay.data.bill.accountlog.query`）。账单查询为同步接口，查询时间范围支持当天；本项目按「最近 N 分钟」回溯。

### 收款方式说明

- **转账链接模式**（经营码模式关闭）：二维码内容为 `alipays://` 转账链接（含防风控多层跳转），备注自动填写订单号。监控按「备注 = 订单号」+ 金额匹配。
- **经营码模式**：展示固定收款码，每笔订单金额按 `amount_offset` 递增以保持唯一；监控按金额 + 时间容差匹配。同一时间多笔订单也不会混淆。

### 邮件通知配置

到「后台 → 系统设置 → 邮件通知（SMTP）」：

1. 选择邮箱服务商（会自动填充 SMTP 主机、端口与加密方式）；
2. 填写发件邮箱账号与授权码（QQ/163/126 等需在邮箱设置中开启 SMTP 并生成授权码；Gmail 需应用专用密码）；
3. 点击「保存邮件配置」，再填写测试收件邮箱并「发送测试邮件」验证；
4. 勾选「启用邮件通知」后，买家下单（必须填写邮箱）并支付成功，发货时自动发送订单号与卡密到该邮箱。

商城订单列表会显示每笔订单的邮件状态（未发送 / 已发送 / 失败 / 未启用），失败原因可在状态悬停查看。

---

## 目录结构

```
alipay-python/
├── app.py                  # 启动入口（Web + 监控线程）
├── config.py               # 路径/端口/环境变量
├── core/                   # 日志、SQLite、加密、配置、会话密钥
├── alipay_pay/             # 支付宝客户端、转账链接、账单查询、订单、监控
├── shop/                   # 商品/卡密/订单/客服业务
├── web/                    # Flask 蓝图、模板、静态资源
├── tools/ui_smoke.py       # 可选：真实浏览器 UI 冒烟测试（playwright）
├── selftest.py             # 端到端自测（独立数据目录，不影响正式数据）
├── wsgi.py                 # waitress/gunicorn 入口
├── data/                   # 运行时数据（自动创建）
├── requirements.txt
├── start.bat / start.sh
└── README.md
```

---

## 码支付协议（外部站点对接）

端点：`GET/POST /api/codepay`

签名算法：参数过滤空值，按 key 升序拼接 `k=v&k=v`，末尾拼接商户密钥（`merchant_key`），取 MD5 小写。

| act | 参数 | 说明 |
| --- | --- | --- |
| `query` | `pid, key` | 查询商户信息 |
| `order` | `pid, out_trade_no[, key]` | 查询单笔订单 |
| `orders` | `pid, key, limit` | 查询最近订单 |
| `submit`/`create` | `pid, type=alipay, out_trade_no, name, money, sign[, notify_url, return_url, sitename]` | 创建支付，返回 `qr_code`（base64）/`payment_url` |
| `notify` | 订单通知参数 | 接收支付成功通知 |

创建支付示例（Python）：

```python
import hashlib, requests

cfg = {
    "pid": "商户ID", "key": "商户密钥", "base": "http://你的域名:5000",
}
params = {
    "pid": cfg["pid"], "type": "alipay",
    "out_trade_no": "ORDER202601010001",
    "name": "测试商品", "money": "1.00",
    "notify_url": "https://你的站点/pay/notify",
}
sign_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if str(v) != "")
params["sign"] = hashlib.md5((sign_str + cfg["key"]).encode()).hexdigest()
params["act"] = "submit"
r = requests.post(cfg["base"] + "/api/codepay", data=params)
print(r.json())
```

`qr_code` 为二维码 base64，直接 `data:image/png;base64,...` 展示。

兼容路径：`/submit.php`、`/api.php`（GET/POST，参数与签名规则相同）。

### 商户回调

`notify_url` 是**对接方自己的回调接收地址**，不是本系统地址；在调用 `act=submit` 时传入，例如
`notify_url=https://your-site.com/pay/notify`。本系统自带商城的订单不需要该字段（支付成功后内部发货并发送邮件）。

支付成功后系统向 `notify_url` 发送 POST 表单：

| 字段 | 说明 |
| --- | --- |
| pid / trade_no / out_trade_no | 商户 ID、平台订单号、商户订单号 |
| type / name / money | 支付类型、商品名、金额 |
| trade_status | 固定 `TRADE_SUCCESS` |
| sign / sign_type | MD5 签名（排除 sign/sign_type），`sign_type=MD5` |

商户验签后返回小写 `success`。失败会按 1/5/15/30/60 分钟自动重试，最多 5 次；后台「码支付订单」可查看回调状态并手动重发。

---

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ALIPAY_HOST` | `0.0.0.0` | 监听地址 |
| `ALIPAY_PORT` | `5000` | 监听端口 |
| `ALIPAY_DEBUG` | `0` | Flask 调试模式 |
| `ALIPAY_MONITOR` | `1` | 设为 `0` 关闭支付监控线程 |
| `ALIPAY_DATA_DIR` | `./data` | 数据目录 |
| `ALIPAY_ENCRYPT_KEY` | 自动生成 | 敏感字段加密密钥（多机迁移保持一致） |
| `ALIPAY_SECRET_KEY` | 自动生成 | Flask 会话密钥 |

---

## 自测

```bash
# 端到端自测（120 项，独立 data_selftest 目录，不影响正式数据）
python selftest.py

# 可选：真实浏览器 UI 冒烟测试（会创建测试商品与配置）
# 建议用独立数据目录启动服务后再运行：
#   Linux/macOS: ALIPAY_DATA_DIR=./data_uitest ALIPAY_PORT=5000 ALIPAY_MONITOR=0 python app.py
#   Windows CMD: set ALIPAY_DATA_DIR=data_uitest && set ALIPAY_MONITOR=0 && python app.py
python tools/ui_smoke.py http://127.0.0.1:5000
```

---

## 数据与备份
- 数据库：`data/alipay.db`（订单 + 商城 + 配置）
- 加密密钥：`data/.secret_key` —— **必须与数据库一起备份**，否则私钥/商户密钥无法解密
- 经营码图片：`data/qrcode/`
- 日志：`data/logs/app.log`

迁移服务器：同时拷贝 `data/alipay.db` 与 `data/.secret_key`，或设置相同的 `ALIPAY_ENCRYPT_KEY`。

---

## MCP 服务（Agent 接入）

内置 MCP 服务，与 Web 同端口，路径 `/mcp`，使用 Bearer Token 鉴权。后台「系统设置 → MCP 服务」可查看/复制 Token、启停与重置。

- 接入地址：`http://<host>:5000/mcp`
- 鉴权：`Authorization: Bearer <token>`（也支持 `X-MCP-Token`）
- 传输：Streamable HTTP（JSON-RPC 2.0，无状态）
- 资源：`alipay://readme`、`alipay://api-docs`、`alipay://stats`

### 客户端配置

Claud Code：
```bash
Claud mcp add --transport http alipay-pay http://<host>:5000/mcp --header "Authorization: Bearer <token>"
```

Cursor / 通用 MCP JSON：
```json
{
  "mcpServers": {
    "alipay-pay": {
      "type": "http",
      "url": "http://<host>:5000/mcp",
      "headers": {"Authorization": "Bearer <token>"}
    }
  }
}
```

### 工具（40 个）

- 概览：`get_stats`、`get_health`、`get_monitor_status`、`run_monitor`
- 商品/卡密：`list_products`、`get_product`、`create_product`、`update_product`、`delete_products`、`set_products_status`、`list_cards`、`import_cards`、`set_card_status`、`delete_cards`、`upload_image`
- 码支付订单：`list_orders`、`get_order`、`mark_order_paid`（补单）、`expire_orders`、`delete_orders`、`resend_notify`
- 商城订单：`list_shop_orders`、`get_shop_order`、`mark_shop_order_paid`（补单）、`refulfill_shop_order`、`delete_shop_orders`、`export_shop_orders_csv`
- 客服：`list_tickets`、`get_ticket`、`reply_ticket`、`set_ticket_status`、`delete_tickets`
- 配置：`get_pay_config`、`update_pay_config`、`generate_merchant`、`get_email_config`、`update_email_config`、`test_email`、`get_shop_settings`、`update_shop_settings`

敏感字段（私钥、商户密钥、邮件密码、推送 token）读取时掩码；修改时只有传入非空值才覆盖。补单、删除、配置修改等工具会作用于真实数据，请只把 Token 交给可信的 Agent。

---

## 性能与并发

- Web 服务默认使用 **waitress（16 线程）**，非 debug 启动不再使用 Flask 开发服务器；可用 `python app.py` 或 `waitress-serve --threads=16 wsgi:app`。
- **账单查询互斥 + 节流**：支付页/订单轮询只读取本地状态，最多每 3 秒在后台线程触发一次账单检查；无论多少用户同时支付，都不会放大支付宝接口请求。
- **异步回调与邮件**：商户回调、PushPlus、发货邮件在线程池执行，不阻塞请求与监控循环；回调失败自动退避重试。
- **并发安全**：
  - 经营码唯一金额分配与订单落库在同一临界区，避免并发下单金额冲突；
  - 补单/标记支付使用原子状态迁移，重复触发只生效一次；
  - 商城发货使用串行锁 + 幂等判断，避免重复扣库存/重复发货；
  - SQLite WAL + 每线程连接（初始化时设置一次 WAL），读并发、写毫秒级串行。
- 建议单进程运行（waitress 多线程已足够）。多进程部署时，监控、金额分配与发货锁仅在进程内生效，需保证只有一个监控实例。
- 本机压测参考：页面 60 并发平均约 40ms；支付状态轮询 40 并发平均约 30ms；并发创建 10 笔订单全部成功。

---

## 部署建议

- 生产环境使用 Nginx 反向代理 + HTTPS；
- 直接 `python app.py` 运行（自带监控线程），或用 waitress：`waitress-serve --host 0.0.0.0 --port 5000 wsgi:app`（`wsgi.py` 已内置监控启动）；
- 若使用多进程（gunicorn 多 worker），请仅在一个进程开启监控，其余进程设置 `ALIPAY_MONITOR_IN_WSGI=0`（匹配逻辑幂等，但可避免重复查询账单）；
- 后台建议加 IP 白名单、修改默认密码。

---

## 常见问题

- **双击 start.bat 闪退**：脚本已改为出错时暂停并显示原因。若仍异常，请在项目目录打开 PowerShell/CMD 执行 `start.bat` 查看完整输出，或检查 `data\logs\app.log`。
- **端口 5000 被占用**：可能已有服务在运行。`start.bat` 会自动提示并打开浏览器；需要重启时先运行 `stop.bat`。
- **依赖安装失败**：检查网络，或在项目目录执行 `.venv\Scripts\python.exe -m pip install -r requirements.txt`。

---

## 已知限制

- 账单查询依赖支付宝开放平台权限；未开通时监控会报错并写入日志，后台「概览」可见最近一次检查结果。
- 转账链接模式依赖收款账号 userId 与账单备注准确送达，极端情况下备注可能被支付平台改写；对账失败时可在后台手动标记支付。
- 经营码模式需保证收款码为固定收款账号的码，且同一时间不与其他系统共用该收款码。
