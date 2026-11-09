"""邮件发送（SMTP SSL / STARTTLS），用于商城订单通知。"""

from __future__ import annotations

import html
import re
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from core.logger import get_logger

logger = get_logger("mailer")

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def is_email(value) -> bool:
    return bool(_EMAIL_RE.match(str(value or "").strip()))


def _strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value or "")


def send_email(config: dict, to_addr: str, subject: str, html_body: str,
               text_body: str = "") -> tuple[bool, str]:
    """发送 HTML 邮件，返回 (成功, 错误信息)。"""
    if not config.get("enabled"):
        return False, "邮件通知未启用"
    host = (config.get("host") or "").strip()
    username = (config.get("username") or "").strip()
    password = config.get("password") or ""
    if not host or not username or not password:
        return False, "SMTP 配置不完整（主机 / 账号 / 密码）"
    if not is_email(to_addr):
        return False, "收件邮箱格式不正确"

    port = int(config.get("port") or 465)
    security = config.get("security") or "ssl"
    from_email = (config.get("from_email") or username).strip()
    from_name = (config.get("from_name") or "卡密商城").strip()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, from_email))
    msg["To"] = to_addr.strip()
    msg.set_content(text_body or html.unescape(_strip_tags(html_body)))
    msg.add_alternative(html_body, subtype="html")

    server = None
    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            if security == "starttls":
                server.starttls()
        server.login(username, password)
        server.send_message(msg)
        server.quit()
        logger.info("邮件已发送: %s -> %s", subject, to_addr)
        return True, ""
    except Exception as e:
        try:
            if server is not None:
                server.quit()
        except Exception:
            pass
        logger.warning("邮件发送失败 %s: %s", to_addr, e)
        return False, str(e)[:200]


def test_email_content(shop_title: str = "") -> tuple[str, str, str]:
    title = shop_title or "卡密商城"
    subject = f"【{title}】邮件配置测试"
    html_body = (
        f"<html><body style=\"font-family:Arial,sans-serif;color:#333;\">"
        f"<h3>{html.escape(title)} · 邮件配置测试</h3>"
        "<p>如果你收到这封邮件，说明后台 SMTP 配置已生效。</p>"
        "<p style=\"color:#888;font-size:12px;\">此邮件由系统自动发送，请勿回复。</p>"
        "</body></html>"
    )
    text_body = f"【{title}】邮件配置测试\n\n如果你收到这封邮件，说明后台 SMTP 配置已生效。"
    return subject, html_body, text_body


def feedback_notify_content(kind: str, ticket: dict, message: str = "",
                            base_url: str = ""):
    """构建客服新消息通知邮件（kind: admin 通知管理员 / user 通知用户）。"""
    contact = ticket.get("contact") or ""
    tid = ticket.get("id")
    preview = str(message or "").strip().replace("\n", " ")
    if not preview:
        preview = "（发送了一个附件）"
    if len(preview) > 160:
        preview = preview[:160] + "..."
    base = (base_url or "").rstrip("/")

    if kind == "admin":
        subject = f"【客服】新咨询 - 会话 #{tid}"
        heading = "有新的客服咨询"
        detail_label = "用户邮箱"
        detail = contact
        link = f"{base}/admin/feedback" if base else ""
    else:
        subject = "【客服】您有一条新回复"
        heading = "客服已回复您的咨询"
        detail_label = "会话编号"
        detail = f"#{tid}"
        link = f"{base}/support" if base else ""

    link_html = (f'<p><a href="{html.escape(link)}" style="color:#2563eb;">'
                 f'点击查看会话</a></p>' if link else "")
    html_body = f"""<html><body style="font-family:Arial,'Microsoft YaHei',sans-serif;color:#333;line-height:1.7;">
<h2 style="color:#2563eb;margin-bottom:10px;">{html.escape(heading)}</h2>
<table style="border-collapse:collapse;margin:10px 0;">
  <tr><td style="padding:5px 12px;color:#888;">{detail_label}</td>
      <td style="padding:5px 12px;">{html.escape(str(detail))}</td></tr>
  <tr><td style="padding:5px 12px;color:#888;">消息摘要</td>
      <td style="padding:5px 12px;">{html.escape(preview)}</td></tr>
</table>
{link_html}
<p style="color:#888;font-size:12px;margin-top:16px;">此邮件由系统自动发送，请勿直接回复。</p>
</body></html>"""

    text_body = (
        f"{heading}\n"
        f"{detail_label}: {detail}\n"
        f"消息摘要: {preview}\n"
        + (f"查看: {link}\n" if link else "")
        + "\n此邮件由系统自动发送，请勿直接回复。"
    )
    return subject, html_body, text_body


def order_email_content(shop_title: str, order: dict, cards: str):
    """构建购买成功邮件（主题 / HTML / 纯文本）。"""
    title = shop_title or "卡密商城"
    order_no = order.get("order_no") or ""
    product = order.get("product_name_snapshot") or "商品"
    qty = int(order.get("qty") or 1)
    price = float(order.get("price") or 0)
    money = f"{price * qty:.2f}"
    created = order.get("created_at") or ""
    card_lines = [c.strip() for c in str(cards or "").splitlines() if c.strip()]
    cards_html = "".join(
        f"<li style=\"margin:4px 0;\"><code style=\"background:#f2f5fa;padding:2px 6px;"
        f"border-radius:4px;font-size:13px;\">{html.escape(c)}</code></li>"
        for c in card_lines
    )
    subject = f"【{title}】购买成功 - {order_no}"

    html_body = f"""<html><body style="font-family:Arial,'Microsoft YaHei',sans-serif;color:#333;line-height:1.7;">
<h2 style="color:#2563eb;margin-bottom:12px;">{html.escape(title)} · 购买成功</h2>
<p>感谢你的购买，以下是订单与卡密信息：</p>
<table style="border-collapse:collapse;margin:12px 0;">
  <tr><td style="padding:6px 12px;color:#888;">订单号</td><td style="padding:6px 12px;">{html.escape(order_no)}</td></tr>
  <tr><td style="padding:6px 12px;color:#888;">商品</td><td style="padding:6px 12px;">{html.escape(product)}</td></tr>
  <tr><td style="padding:6px 12px;color:#888;">数量</td><td style="padding:6px 12px;">{qty}</td></tr>
  <tr><td style="padding:6px 12px;color:#888;">金额</td><td style="padding:6px 12px;">￥{money}</td></tr>
  <tr><td style="padding:6px 12px;color:#888;">下单时间</td><td style="padding:6px 12px;">{html.escape(created)}</td></tr>
</table>
<h3 style="margin:16px 0 8px;">卡密 / 发货内容</h3>
<ul style="padding-left:20px;margin:0;">{cards_html or '<li>无</li>'}</ul>
<p style="color:#888;font-size:12px;margin-top:20px;">请妥善保存本邮件。如订单有问题，请凭订单号联系客服。</p>
</body></html>"""

    text_body = (
        f"【{title}】购买成功\n"
        f"订单号: {order_no}\n"
        f"商品: {product}\n"
        f"数量: {qty}\n"
        f"金额: {money}\n"
        f"时间: {created}\n\n"
        f"卡密/发货内容:\n{cards}\n"
    )
    return subject, html_body, text_body
