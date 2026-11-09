# 码支付 · 卡密商城（独立版）

---声明：本项目改自PHP版的码支付项目（https://github.com/dedemao/alipay），请勿用于频繁收款场景和灰产，避免被封禁，后果自负---

**当前版本：v1.0.0**（版本号同时显示在商城页脚、后台侧边栏与 `/health` 响应中）

基于支付宝账单查询的**码支付系统**，内置卡密商城、管理后台、客服工单与 MCP 服务。独立进程即可运行，不需要额外的消息队列、缓存或外部数据库。

> **支付侦测原理**：用户扫码转账（备注 = 订单号）或扫描经营码支付唯一金额后，后台定时查询支付宝账户流水（`alipay.data.bill.accountlog.query`），命中后自动标记支付、商城自动发货，并回调商户的 `notify_url`。
>
> 本项目不包含演示支付或模拟到账功能：未配置支付宝时，后台与商城页面可以正常浏览，但下单会提示「支付未就绪」。这是生产设计，避免误把测试订单当成真实收款。

---

## 功能特性

- **码支付协议**：兼容 CodePay 协议（`/api/codepay`），支持 `query / order / orders / submit / notify`，MD5 签名。
- **两种收款方式**
  - **转账链接模式**：生成带备注的支付宝转账二维码，按「备注 = 订单号 + 金额」匹配；
  - **经营码模式**：固定收款码 + 每单唯一金额（默认按 0.01 递增），按金额 + 时间容差匹配，适合个人收款码。
- **自动支付监控**：后台线程按配置间隔（默认 10 秒）查询账单，修改配置即时生效。
- **卡密商城**：商品管理、库存卡密批量导入、下单自动生成卡密、支付后自动发货、订单查询、客服工单。
- **商品展示**：商品可配置图标（可选）；商品描述支持富文本与内嵌图片（服务端白名单清洗后展示）。
- **邮件通知**：后台可配置主流邮箱 SMTP（QQ / 163 / 126 / 阿里云 / 腾讯企业邮 / Gmail / Outlook / 自定义）；买家下单必须填写邮箱，启用后发货自动发送订单与卡密邮件，后台可查看发送状态并测试发送。
- **客服系统**：用户端 `/support` 使用下单邮箱验证身份；支持 10MB 以内附件；后台可查看、回复、批量关闭/删除，支持新消息邮件提醒。
- **批量操作**：订单、商城订单、商品、客服工单均支持批量选择，可批量补单、补发、上下架、关闭、删除、导出 CSV。
- **补单**：支付成功但未自动匹配/发货时，可在后台对码支付订单或商城订单手动补单，补单会触发商城发货与商户回调。
- **商户回调**：支付成功自动回调 `notify_url`（POST 表单 + MD5 签名，返回 `success`），失败按 1/5/15/30/60 分钟重试，最多 5 次；后台可查看回调状态并手动重发。
- **管理后台**：概览（含 7 日收入趋势图）、支付配置、订单、商品/卡密、商城订单、客服、系统设置；敏感字段加密存储；登录页支持记住账号密码（仅保存在本机浏览器）。
- **开发接口页**：`/admin/api-docs` 提供完整的 CodePay 协议文档（签名规则、参数表、curl/Python 示例、回调验签示例）、参数拼装与在线调试。
- **MCP 服务**：内置 40 个 MCP 工具，可让 AI Agent 查询/管理商品、订单、客服与配置。
- **安全**：后台登录失败限速、会话 Cookie HttpOnly/SameSite、常用安全响应头；私钥、商户密钥、邮箱密码、MCP Token 加密存储。
- **PushPlus 通知**：支付成功、商城成交通知（可选）。

---

## 系统要求

| 项目 | 要求 |
| --- | --- |
| Python | **3.10 或更高版本**（代码使用 Python 3.10 的联合类型语法） |
| 操作系统 | Windows 10/11、Linux、macOS |
| 支付宝 | 开放平台应用（AppId、应用私钥、支付宝公钥） |
| 接口权限 | 账户账单查询 `alipay.data.bill.accountlog.query`（通常需要企业/签约应用，能否开通以支付宝开放平台审核为准） |
| 网络 | 本系统主动查询账单，默认**不需要公网回调**；仅当外部商户填写 `notify_url` 时，该地址需由商户自己保证可达 |
| 可选 | SMTP 邮箱（发货/客服通知）、PushPlus Token、HTTPS 域名（建议生产使用） |
| 可选 | Docker Engine 24+ 与 Docker Compose v2（容器化部署） |

> **重要**：没有「账户账单查询」权限时，支付监控无法确认到账。请先在支付宝开放平台确认应用具备该权限，再部署本项目。

---

## 快速开始

### 方式一：Windows 一键启动（推荐）

1. 安装 [Python 3.10+](https://www.python.org/downloads/)，安装时勾选 **Add Python to PATH**；
2. 下载或克隆本项目到本地目录；
3. 双击 `start.bat`。首次运行会自动创建 `.venv`、安装依赖并启动；
4. 浏览器打开 <http://localhost:5000/>。

停止服务：双击 `stop.bat`。端口被占用时，`start.bat` 会提示并打开已有服务，不会闪退。`start.bat` / `stop.bat` 会读取 `ALIPAY_PORT`（默认 5000）。

### 方式二：手动启动（Windows / macOS / Linux）

```bash
git clone 你的仓库地址 alipay-python
cd alipay-python

# 创建虚拟环境
python -m venv .venv

# Windows CMD
.venv\Scripts\activate.bat
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 启动（非 debug 模式自动使用 waitress，16 线程）
python app.py
```

Linux/macOS 也可以直接执行：

```bash
chmod +x start.sh
./start.sh
```

### 方式三：Docker Compose（推荐服务器部署）

前置：安装 Docker Engine 24+ 与 Docker Compose v2（Windows / macOS 使用 Docker Desktop 即可）。

```bash
cp .env.example .env          # 按需修改端口、时区、密钥
docker compose up -d --build  # 构建镜像并后台启动
docker compose logs -f        # 查看启动日志
```

首次启动会自动创建宿主机 `./data` 目录，并生成数据库与加密密钥。容器入口脚本会自动把数据目录权限调整为运行用户（uid 1000），一般无需手动处理；如需指定其他所有者，可在宿主机执行：

```bash
mkdir -p data && sudo chown -R 1000:1000 data
```

常用运维命令：

```bash
docker compose ps             # 查看状态（含健康检查）
docker compose restart        # 重启服务
docker compose down           # 停止并删除容器（保留 ./data）
docker compose up -d --build  # 更新代码后重新构建
```

容器内固定监听 5000，宿主机端口由 `.env` 中的 `ALIPAY_PORT` 决定（默认 5000）。生产环境建议只绑定本机，由 Nginx 反向代理并配置 HTTPS：

```yaml
ports:
  - "127.0.0.1:${ALIPAY_PORT:-5000}:5000"
```

数据卷为 `./data:/app/data`，备份该目录即可（见「数据与备份」）。健康检查调用 `/health`，`docker compose ps` 显示 `healthy` 即表示服务正常。

### 首次访问

| 入口 | 地址 | 说明 |
| --- | --- | --- |
| 商城前台 | <http://localhost:5000/> | 买家购买、订单查询、客服 |
| 管理后台 | <http://localhost:5000/admin/> | 默认账号 `admin`，密码 `admin123` |
| 接口文档 | <http://localhost:5000/admin/api-docs> | 登录后查看 CodePay 协议与在线调试 |
| 健康检查 | <http://localhost:5000/health> | 返回服务状态与版本号，可用于探活 |

> **首次登录后请立即修改默认密码**：后台 → 系统设置 → 管理员密码。

首次启动会自动创建 `data/`：

```
data/
├── alipay.db          # SQLite 数据库（订单、商品、卡密、配置等）
├── .secret_key        # 敏感字段加密密钥（必须备份）
├── .flask_secret      # 会话密钥（未设置 ALIPAY_SECRET_KEY 时生成）
├── qrcode/            # 经营码图片
├── uploads/           # 商品图片等上传文件
├── feedback_files/    # 客服附件
└── logs/app.log       # 运行日志
```

---

## 从零配置教程

### 第 1 步：修改管理员密码

登录后台 → **系统设置 → 管理员密码**，输入原密码与新密码（至少 6 位）保存。

### 第 2 步：准备支付宝开放平台

1. 登录 [支付宝开放平台](https://open.alipay.com/)，创建应用（网页/移动应用，按你的使用场景选择）；
2. 生成 **RSA2** 密钥对：保留应用私钥，上传应用公钥；
3. 在开放平台获取 **支付宝公钥**；
4. 为应用申请 **账户账单查询**（`alipay.data.bill.accountlog.query`）权限（开放平台应用详情 → 产品绑定/能力列表）；
5. 记录以下三项：**AppId**、**应用私钥**、**支付宝公钥**。

> 应用私钥支持 PKCS#1、PKCS#8 或纯 base64，系统会自动归一化。不要把私钥提交到 Git 或发给他人。

### 第 3 步：填写支付配置

后台 → **支付配置**，按下表填写并保存：

| 配置 | 是否必填 | 说明 |
| --- | --- | --- |
| AppId | 是 | 支付宝开放平台应用 AppId |
| 应用私钥 | 是 | 应用私钥（PKCS#1 / PKCS#8 / 纯 base64 均可） |
| 支付宝公钥 | 是 | 开放平台「支付宝公钥」 |
| 转账收款账号 userId | 转账链接模式必填 | 收款支付宝账号的 `2088` 开头 userId（可通过开放平台用户信息接口或商户平台查询） |
| 商户 ID / 密钥 | 否 | 点击「自动生成商户凭据」生成，供外部站点对接 |
| 经营码二维码 | 经营码模式必填 | 上传固定收款码图片 |

保存后页面顶部会显示「支付已就绪 / 未就绪」及缺失项。配置生效后，等待一个监控周期（默认 10 秒）即可开始收款。

### 第 4 步：选择收款方式

- **转账链接模式**（关闭经营码模式）：二维码内容为 `alipays://` 转账链接，备注自动填写订单号；监控按「备注 = 订单号」+ 金额匹配。
- **经营码模式**：展示上传的固定收款码；每笔订单使用唯一金额（`amount_offset` 默认 0.01）避免并发混淆；监控按金额 + 时间容差匹配。

> 同一时间只建议一个系统使用该收款码/收款账号，避免与其他业务对账冲突。

### 第 5 步：生成商户凭据（外部站点对接时需要）

后台 → 支付配置 → **自动生成商户凭据**，保存后得到 `pid`（商户 ID）与 `merchant_key`（商户密钥）。外部站点使用这两个值调用 `/api/codepay`。商城的内部订单不需要商户凭据。

### 第 6 步：创建商品与卡密

1. 后台 → **商品管理** → 新建商品：填写名称、价格、描述（支持富文本与图片）、图标（可选）；
2. 进入商品卡密管理，批量导入卡密（一行一条）；
3. 设置商品上架。买家下单并支付成功后，系统自动分配卡密并标记售出；
4. 没有库存卡密的商品无法完成自动发货，请提前导入。

### 第 7 步：配置邮件通知（可选，推荐）

后台 → **系统设置 → 邮件通知（SMTP）**：

1. 选择邮箱服务商（自动填充 SMTP 主机、端口与加密方式）；
2. 填写发件邮箱与授权码。QQ/163/126 需在邮箱设置中开启 SMTP 并生成授权码；Gmail 需应用专用密码；
3. 保存后填写测试收件邮箱，点击「发送测试邮件」；
4. 勾选「启用邮件通知」。买家下单（必须填写邮箱）并支付成功后，系统自动发送订单号与卡密。

商城订单列表会显示邮件状态（未发送 / 已发送 / 失败 / 未启用），失败原因可悬停查看。

### 第 8 步：配置客服通知（可选）

- 用户端 `/support`：使用下单邮箱验证身份进入会话，支持 10MB 以内附件（图片直接显示，其他文件可下载）。
- 后台 `/admin/feedback`：工单列表、批量关闭/删除、会话每 8 秒自动刷新、附件查看与回复。
- 用户可在客服页设置「新消息邮件通知」；管理员可在后台客服页设置接收用户新消息的邮箱与开关。
- 通知使用「系统设置 → 邮件通知」中的 SMTP；未配置/未启用时不发送。管理员通知同一工单 60 秒最多一次。

### 第 9 步：小额实测

上线前务必用真实支付宝做一次小额测试：

1. 打开商城，选择一个低价商品下单，填写真实邮箱；
2. 扫码支付（转账模式请确认备注为订单号；经营码模式请支付页面显示的准确金额）；
3. 等待监控周期（默认 10 秒），观察订单是否变为已支付、卡密是否发放、邮件是否送达；
4. 若未自动命中：后台 → 支付配置/概览查看最近一次账单检查结果；必要时在订单页**手动补单**（补单会触发发货与回调）；
5. 外部站点接入时，再检查回调状态与重试记录。

### 第 10 步：部署上线

生产环境建议：

- 使用 **HTTPS**（后台登录、商城、客服附件都需要）；
- 使用 Nginx 反向代理到本机 `127.0.0.1:5000`；
- 使用 systemd/supervisor 守护进程，或使用 Docker Compose（`docker compose up -d`，已内置健康检查与自动重启）；
- 定期备份 `data/`（见「数据与备份」）；
- 修改默认密码、限制后台访问来源。

Nginx 示例：

```nginx
server {
    listen 80;
    server_name pay.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name pay.example.com;

    ssl_certificate     /etc/letsencrypt/live/pay.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pay.example.com/privkey.pem;

    # 客服附件上限 10MB，留出表单开销
    client_max_body_size 12m;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 90s;
    }
}
```

systemd 示例（`/etc/systemd/system/alipay-pay.service`）：

```ini
[Unit]
Description=Alipay CodePay Shop
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/alipay-python
Environment=ALIPAY_HOST=127.0.0.1
Environment=ALIPAY_PORT=5000
Environment=ALIPAY_DATA_DIR=/opt/alipay-python/data
ExecStart=/opt/alipay-python/.venv/bin/python app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now alipay-pay
sudo systemctl status alipay-pay
```

也可以直接使用 waitress（`wsgi.py` 会自动启动监控线程）：

```bash
waitress-serve --host 127.0.0.1 --port 5000 --threads=16 wsgi:app
```

> 建议单进程运行（waitress 多线程已足够）。使用 gunicorn 多进程时，监控、金额分配与发货锁仅在进程内生效，请只在一个进程开启监控（其余进程设置 `ALIPAY_MONITOR_IN_WSGI=0`）。

---

## 使用教程

### 买家：购买与查单

1. 打开商城首页，选择商品，填写邮箱后下单；
2. 页面展示支付宝二维码：转账模式会自动填写订单号备注；经营码模式请支付页面显示的**准确金额**；
3. 支付完成后停留在支付页，系统会自动轮询订单状态；
4. 支付成功自动发放卡密，邮件通知启用时同时发送到下单邮箱；
5. 买家可通过「订单查询」使用订单号 + 邮箱查看订单与卡密。

### 买家：客服

打开 `/support`，使用下单邮箱验证身份后进入会话，可发送文字与 10MB 以内附件。会话每 8 秒自动刷新。

### 管理员：订单与补单

- **码支付订单**：查看外部站点创建的订单、支付状态、回调状态；支持 CSV 导出、批量删除、批量补单、手动重发回调。
- **商城订单**：查看商品、金额、买家邮箱、卡密、邮件状态；支持补单、补发、批量删除、导出。
- **补单**：支付成功但未自动匹配时使用。补单会按正常支付流程触发商城发货与商户回调；重复补单不会重复发货。

### 管理员：商品与卡密

- 商品支持上下架、批量上下架、删除、图标与富文本描述；
- 卡密支持批量导入、启用/停用、删除；
- 商城发货按可用卡密顺序分配，库存不足时订单会标记异常，需补充库存后补发。

### 管理员：浏览与导出

- 概览页展示订单量、收入、7 日趋势与监控状态；
- 订单与商城订单均支持筛选、分页与 CSV 导出；
- 客服工单支持批量关闭/删除。

---

## 外部站点接入（CodePay 协议）

端点：`GET/POST /api/codepay`

签名算法：参数过滤空值，按 key 升序拼接 `k=v&k=v`，末尾拼接商户密钥（`merchant_key`），取 MD5 小写。

| act | 参数 | 说明 |
| --- | --- | --- |
| `query` | `pid, key` | 查询商户信息 |
| `order` | `pid, out_trade_no[, key]` | 查询单笔订单 |
| `orders` | `pid, key, limit` | 查询最近订单 |
| `submit` / `create` | `pid, type=alipay, out_trade_no, name, money, sign[, notify_url, return_url, sitename]` | 创建支付，返回 `qr_code`（base64）/`payment_url` |
| `notify` | 订单通知参数 | 接收支付成功通知 |

创建支付示例（Python）：

```python
import hashlib
import requests

cfg = {
    "pid": "商户ID",
    "key": "商户密钥",
    "base": "https://你的域名",
}
params = {
    "pid": cfg["pid"],
    "type": "alipay",
    "out_trade_no": "ORDER202601010001",
    "name": "测试商品",
    "money": "1.00",
    "notify_url": "https://your-site.com/pay/notify",
}
sign_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if str(v) != "")
params["sign"] = hashlib.md5((sign_str + cfg["key"]).encode()).hexdigest()
params["act"] = "submit"

r = requests.post(cfg["base"] + "/api/codepay", data=params, timeout=10)
print(r.json())
```

`qr_code` 为二维码 base64，可直接用 `data:image/png;base64,...` 展示。

兼容路径：`/submit.php`、`/api.php`（GET/POST，参数与签名规则相同）。

### 商户回调

`notify_url` 是**对接方自己的回调接收地址**，不是本系统地址；在调用 `act=submit` 时传入，例如 `notify_url=https://your-site.com/pay/notify`。本系统自带商城的订单不需要该字段（支付成功后内部发货并发送邮件）。

支付成功后系统向 `notify_url` 发送 POST 表单：

| 字段 | 说明 |
| --- | --- |
| pid / trade_no / out_trade_no | 商户 ID、平台订单号、商户订单号 |
| type / name / money | 支付类型、商品名、金额 |
| trade_status | 固定 `TRADE_SUCCESS` |
| sign / sign_type | MD5 签名（排除 sign/sign_type），`sign_type=MD5` |

商户验签后返回小写 `success`。失败会按 1/5/15/30/60 分钟自动重试，最多 5 次；后台「码支付订单」可查看回调状态并手动重发。

---

## MCP 服务（Agent 接入）

内置 MCP 服务与 Web 同端口，路径 `/mcp`，使用 Bearer Token 鉴权。后台「系统设置 → MCP 服务」可查看/复制 Token、启停与重置。

- 接入地址：`http://127.0.0.1:5000/mcp`（远程部署时替换为实际域名或 IP）
- 鉴权：`Authorization: Bearer <token>`（也支持 `X-MCP-Token`）
- 传输：Streamable HTTP（JSON-RPC 2.0，无状态）
- 资源：`alipay://readme`、`alipay://api-docs`、`alipay://stats`

### 客户端配置

Claud Code：

```bash
Claud mcp add --transport http alipay-pay http://127.0.0.1:5000/mcp --header "Authorization: Bearer 你的Token"
```

Cursor / 通用 MCP JSON：

```json
{
  "mcpServers": {
    "alipay-pay": {
      "type": "http",
      "url": "http://127.0.0.1:5000/mcp",
      "headers": {"Authorization": "Bearer 你的Token"}
    }
  }
}
```

### 工具（40 个）

- **概览**：`get_stats`、`get_health`、`get_monitor_status`、`run_monitor`
- **商品/卡密**：`list_products`、`get_product`、`create_product`、`update_product`、`delete_products`、`set_products_status`、`list_cards`、`import_cards`、`set_card_status`、`delete_cards`、`upload_image`
- **码支付订单**：`list_orders`、`get_order`、`mark_order_paid`（补单）、`expire_orders`、`delete_orders`、`resend_notify`
- **商城订单**：`list_shop_orders`、`get_shop_order`、`mark_shop_order_paid`（补单）、`refulfill_shop_order`、`delete_shop_orders`、`export_shop_orders_csv`
- **客服**：`list_tickets`、`get_ticket`、`reply_ticket`、`set_ticket_status`、`delete_tickets`
- **配置**：`get_pay_config`、`update_pay_config`、`generate_merchant`、`get_email_config`、`update_email_config`、`test_email`、`get_shop_settings`、`update_shop_settings`

敏感字段（私钥、商户密钥、邮件密码、推送 Token）读取时掩码；修改时只有传入非空值才覆盖。补单、删除、配置修改等工具会作用于真实数据，请只把 Token 交给可信的 Agent。

---

## 项目结构

```text
alipay-python/
├── app.py                       # 启动入口（Web + 支付监控线程）
├── wsgi.py                      # waitress / gunicorn WSGI 入口
├── config.py                    # 路径、端口、环境变量
├── core/                        # 日志、SQLite、加密、配置、邮件、任务
├── alipay_pay/                  # 支付宝客户端、账单查询、码支付协议、订单、监控
├── shop/                        # 商品、卡密、商城订单、客服业务
├── web/                         # Flask 蓝图、模板、静态资源、MCP 服务
├── tools/
│   ├── ui_smoke.py              # 可选：真实浏览器 UI 冒烟测试
│   └── reset_admin_password.py  # 管理员密码重置
├── selftest.py                  # 端到端自测
├── requirements.txt
├── start.bat / start.sh         # 一键启动
├── stop.bat
├── Dockerfile                   # Docker 镜像构建
├── docker-entrypoint.sh         # 容器入口：修复数据卷权限后降权运行
├── compose.yaml                 # Docker Compose 部署（数据卷 / 健康检查 / 日志轮转）
├── .dockerignore
├── .env.example                 # Compose 环境变量模板（复制为 .env）
├── LICENSE
└── README.md
```

---

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ALIPAY_HOST` | `0.0.0.0` | 监听地址；配合 Nginx 时建议 `127.0.0.1` |
| `ALIPAY_PORT` | `5000` | 监听端口 |
| `ALIPAY_DEBUG` | `0` | 设为 `1` 启用 Flask 调试模式（仅开发） |
| `ALIPAY_MONITOR` | `1` | 设为 `0` 关闭支付监控线程（测试/多进程从节点） |
| `ALIPAY_MONITOR_IN_WSGI` | `1` | 使用 `wsgi.py` 多进程部署时，非监控进程设为 `0` |
| `ALIPAY_DATA_DIR` | `./data` | 数据目录 |
| `ALIPAY_ENCRYPT_KEY` | 自动生成 | 敏感字段加密密钥；多机迁移时保持一致 |
| `ALIPAY_SECRET_KEY` | 自动生成 | Flask 会话密钥 |
| `TZ` | `Asia/Shanghai` | 仅 Docker Compose 部署使用，控制容器时区与日志时间 |

示例：

```bash
# Linux/macOS
ALIPAY_HOST=127.0.0.1 ALIPAY_PORT=5000 ALIPAY_DATA_DIR=/opt/alipay-python/data python app.py

# Windows PowerShell
$env:ALIPAY_HOST="127.0.0.1"; $env:ALIPAY_PORT="5000"; python app.py
```

---

## 数据与备份

运行时数据全部位于 `data/`：

| 路径 | 内容 | 备份重要性 |
| --- | --- | --- |
| `data/alipay.db` | 订单、商品、卡密、客服、配置 | **必须** |
| `data/.secret_key` | 敏感字段加密密钥 | **必须**，丢失后私钥/商户密钥/邮箱密码无法解密 |
| `data/.flask_secret` | 会话密钥 | 建议 |
| `data/qrcode/` | 经营码图片 | 建议 |
| `data/uploads/` | 商品图片等 | 建议 |
| `data/feedback_files/` | 客服附件 | 建议 |
| `data/logs/app.log` | 运行日志 | 可选 |

迁移服务器：同时拷贝整个 `data/`（至少数据库与 `.secret_key`），或在新环境设置相同的 `ALIPAY_ENCRYPT_KEY` 并导入数据库。

Docker Compose 部署时 `./data` 会挂载到容器的 `/app/data`，备份宿主机上的 `./data` 目录即可；重建容器不会丢失数据。

> 不要把 `data/` 提交到 Git。项目已在 `.gitignore` 中排除。

---

## 测试

```bash
# 端到端自测（161 项，使用独立 data_selftest 目录，不影响正式数据）
python selftest.py
```

可选的真实浏览器 UI 冒烟测试（需要 playwright）：

```bash
pip install playwright
playwright install chromium

# 建议使用独立数据目录与关闭监控的实例
# Linux/macOS:
ALIPAY_DATA_DIR=./data_uitest ALIPAY_PORT=5000 ALIPAY_MONITOR=0 python app.py
# Windows CMD:
set ALIPAY_DATA_DIR=data_uitest && set ALIPAY_MONITOR=0 && python app.py

python tools/ui_smoke.py http://127.0.0.1:5000
```

---

## 性能与并发

- Web 服务默认使用 **waitress（16 线程）**，非 debug 启动不再使用 Flask 开发服务器。
- **账单查询互斥 + 节流**：支付页/订单轮询只读取本地状态，最多每 3 秒在后台线程触发一次账单检查；无论多少用户同时支付，都不会放大支付宝接口请求。
- **异步回调与邮件**：商户回调、PushPlus、发货邮件在线程池执行，不阻塞请求与监控循环；回调失败自动退避重试。
- **并发安全**：
  - 经营码唯一金额分配与订单落库在同一临界区，避免并发下单金额冲突；
  - 补单/标记支付使用原子状态迁移，重复触发只生效一次；
  - 商城发货使用串行锁 + 幂等判断，避免重复扣库存/重复发货；
  - SQLite WAL + 每线程连接，读并发、写毫秒级串行。
- 建议单进程运行。多进程部署时，监控、金额分配与发货锁仅在进程内生效，需保证只有一个监控实例。

---

## 已知限制

- 账单查询依赖支付宝开放平台「账户账单查询」权限；未开通时监控会报错并写入日志，后台「概览」可查看最近一次检查结果。
- 转账链接模式依赖收款账号 userId 与账单备注准确送达，极端情况下备注可能被支付平台改写；对账失败时可在后台手动标记支付/补单。
- 经营码模式需保证收款码为固定收款账号的码，且同一时间不与其他系统共用该收款码，否则可能金额/订单混淆。
- 商城默认订单超时 300 秒；监控与金额分配按单进程设计，多进程部署需保证只有一个监控实例。
- 项目不提供模拟支付/演示到账；没有支付宝应用与账单权限时只能浏览界面，不能完成真实下单收款。

---

## 常见问题（FAQ）

**Q：双击 `start.bat` 闪退怎么办？**
脚本在出错时会暂停并显示原因。若仍异常，请在项目目录打开 CMD/PowerShell 执行 `start.bat` 查看完整输出，并检查 `data/logs/app.log`。

**Q：端口 5000 被占用？**
可能已有服务在运行。`start.bat` 会提示并打开已有服务；需要重启时先运行 `stop.bat`，或设置 `ALIPAY_PORT` 换端口。

**Q：依赖安装失败？**
检查网络与 Python 版本（必须 3.10+），然后手动执行：

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt   # Windows
.venv/bin/python -m pip install -r requirements.txt           # Linux/macOS
```

**Q：商城提示「支付未就绪」？**
后台 → 支付配置检查 AppId、应用私钥、支付宝公钥是否填写并保存；转账模式还需收款账号 userId，经营码模式还需上传收款码。页面顶部会列出缺失项。

**Q：支付后订单一直未支付？**
依次检查：
1. 后台概览/支付配置的最近一次账单检查结果与错误日志；
2. 应用是否具备账户账单查询权限；
3. 转账模式备注是否准确等于订单号；经营码模式金额是否与页面完全一致；
4. 查询回溯时间（`query_minutes_back`）是否覆盖支付时间；
5. 确认真实到账后，可在订单页手动补单。

**Q：回调没有到达商户？**
`notify_url` 必须是对接方自己的公网可达地址；检查商户验签逻辑是否返回小写 `success`。后台订单页可查看重试状态并手动重发。

**Q：邮件发送失败？**
确认已开启 SMTP、使用授权码/应用专用密码而非登录密码；检查端口与加密方式（SSL 465 / STARTTLS 587）；先用「发送测试邮件」验证。

**Q：客服附件上传失败？**
单个附件上限 10MB；Nginx 需设置 `client_max_body_size`（示例为 12m）。

**Q：MCP 返回 401？**
确认请求头为 `Authorization: Bearer <token>`，Token 与后台「系统设置 → MCP 服务」一致；重置 Token 后需更新客户端配置。

**Q：忘记管理员密码？**
在项目根目录执行 `python tools/reset_admin_password.py`，按提示输入新密码。建议先停止服务并备份 `data/`，更新后重启服务即可使用新密码登录。

**Q：如何迁移服务器？**
拷贝整个 `data/` 目录（至少 `alipay.db` 与 `.secret_key`），安装相同 Python 版本与依赖后启动；或设置相同的 `ALIPAY_ENCRYPT_KEY`。

---

## 安全建议

- 首次登录立即修改默认密码 `admin123`；
- 生产环境使用 HTTPS，后台尽量限制 IP 或加一层访问控制；
- 不要提交/泄露 `data/`、应用私钥、商户密钥、邮箱授权码、MCP Token；
- 定期备份 `data/`，并验证 `.secret_key` 与数据库匹配；
- 只把 MCP Token 交给可信 Agent；
- 关注依赖安全更新：`pip install -U -r requirements.txt` 前先在测试环境验证；
- 遵守支付宝开放平台协议、商户规则与当地税务/经营法规。

---

## 贡献

欢迎提交 Issue 或 Pull Request。提交问题时请附上系统版本、Python 版本、复现步骤与相关日志；**不要**粘贴应用私钥、商户密钥、邮箱授权码、MCP Token 或真实订单/买家信息。

---

## 开源许可证（禁止商业使用）

本项目采用 **PolyForm Noncommercial License 1.0.0**（非商业许可证），全文见 [LICENSE](LICENSE)。

- **允许**：个人学习、研究、实验、业余项目、慈善/教育/公共研究/政府机构等非商业用途；可修改并分发，但必须保留许可证与版权声明。
- **禁止**：任何商业用途，包括但不限于对外收费服务、商业产品集成、企业内部商业运营、以盈利为目的的部署与分发。
- **商业授权**：如需商业使用，请在项目仓库提交标题包含「商业授权」的 Issue，或通过仓库主页提供的作者联系方式获取单独授权。
- **第三方依赖**：Flask、waitress、requests、qrcode、Pillow、cryptography、rsa、bleach、alipay-sdk-python 等遵循各自的开源许可证，不受本项目许可证影响。

> 严格来说，禁止商业使用的许可证不属于 OSI 定义的「开源许可证」，本项目属于**源码开放（source-available）**。请在使用前阅读 LICENSE 全文。

---

## 免责声明

本项目按「现状」提供，不提供任何明示或暗示的担保。支付确认依赖支付宝账单查询接口及网络环境，可能存在延迟、漏单或接口变更风险。使用者应自行评估并承担使用风险，确保用途合法合规，并遵守支付宝平台规则及当地法律法规。作者不对因使用本项目造成的资金损失、数据丢失、业务中断或法律责任负责。
