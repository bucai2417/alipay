"""上传图片保存：校验真实图片格式并写入 data/uploads。"""

from __future__ import annotations

import io
import os
import uuid

from config import DATA_DIR

MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_FORMATS = {
    "PNG": ".png", "JPEG": ".jpg", "JPG": ".jpg",
    "GIF": ".gif", "WEBP": ".webp", "BMP": ".bmp",
}


def save_image_bytes(raw: bytes) -> tuple[dict | None, str]:
    """保存图片字节，返回 ({url, filename}, "") 或 (None, 错误原因)。"""
    if not raw:
        return None, "空文件"
    if len(raw) > MAX_IMAGE_BYTES:
        return None, "图片不能超过 8MB"
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        img.verify()
        fmt = (img.format or "").upper()
    except Exception:
        return None, "文件不是有效的图片"
    ext = ALLOWED_FORMATS.get(fmt)
    if not ext:
        return None, "不支持的图片格式"

    upload_dir = os.path.join(DATA_DIR, "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(upload_dir, filename), "wb") as f:
        f.write(raw)
    return {"url": f"/uploads/{filename}", "filename": filename}, ""
