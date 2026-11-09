"""客服附件保存与校验（单文件最大 10MB）。"""

from __future__ import annotations

import io
import mimetypes
import os
import uuid

from config import DATA_DIR

MAX_FEEDBACK_FILE_BYTES = 10 * 1024 * 1024
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
ALLOWED_EXTS = IMAGE_EXTS | {
    ".pdf", ".txt", ".log", ".csv", ".zip", ".rar", ".7z",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
}


def save_feedback_file(raw: bytes, original_name: str):
    """保存客服附件，返回 (附件信息, "") 或 (None, 错误原因)。"""
    if not raw:
        return None, "文件为空"
    if len(raw) > MAX_FEEDBACK_FILE_BYTES:
        return None, "附件不能超过 10MB"

    name = os.path.basename(str(original_name or "")).strip() or "attachment"
    name = name[:120]
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXTS:
        return None, "不支持的文件类型"
    if ext in IMAGE_EXTS and ext != ".svg":
        try:
            from PIL import Image

            img = Image.open(io.BytesIO(raw))
            img.verify()
        except Exception:
            return None, "图片文件损坏或格式不支持"

    file_dir = os.path.join(DATA_DIR, "feedback_files")
    os.makedirs(file_dir, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(file_dir, stored), "wb") as f:
        f.write(raw)

    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return {
        "url": f"/feedback-files/{stored}",
        "name": name,
        "type": mime,
        "size": len(raw),
        "is_image": ext in IMAGE_EXTS,
    }, ""
