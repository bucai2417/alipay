"""WSGI 入口（waitress / gunicorn）。

生产环境示例：
    waitress-serve --host 0.0.0.0 --port 5000 wsgi:app

注意：监控线程默认随 WSGI 进程启动。使用多进程部署时，请只在一个进程
保留监控（其余进程设置 ALIPAY_MONITOR_IN_WSGI=0），或直接使用 `python app.py`。
"""

import os

from app import app
from config import MONITOR_ENABLED

_started = False
if MONITOR_ENABLED and os.getenv("ALIPAY_MONITOR_IN_WSGI", "1") != "0":
    from alipay_pay.monitor import MonitorThread

    MonitorThread().start()
    _started = True

__all__ = ["app"]
