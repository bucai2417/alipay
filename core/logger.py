"""统一的日志配置。"""

import logging
import os
import sys

from config import DATA_DIR

_initialized = False


def setup_logging(level: int = logging.INFO) -> None:
    global _initialized
    if _initialized:
        return
    _initialized = True

    # Windows 控制台默认 GBK，尽量切换为 UTF-8 避免中文日志乱码
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    try:
        log_dir = os.path.join(DATA_DIR, "logs")
        os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.FileHandler(
            os.path.join(log_dir, "app.log"), encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except Exception:
        pass


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
