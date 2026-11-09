"""二维码生成（qrcode + Pillow）。"""

import base64
import io


def generate_qr_base64(content, size=300, margin=10):
    """生成二维码并返回 base64 PNG；依赖缺失时返回 None。"""
    try:
        import qrcode

        qr = qrcode.QRCode(version=1, box_size=10, border=max(1, margin // 10))
        qr.add_data(content)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        if size and size != 300:
            img = img.resize((size, size))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
    except Exception:
        return None
