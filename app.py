"""启动入口：Web 服务 + 支付监控线程。

用法：
    python app.py

环境变量：
    ALIPAY_HOST / ALIPAY_PORT      监听地址与端口（默认 0.0.0.0:5000）
    ALIPAY_MONITOR=0               关闭支付监控线程
    ALIPAY_DATA_DIR                数据目录（默认 ./data）
    ALIPAY_ENCRYPT_KEY             敏感配置加密密钥（多机迁移时保持一致）
    ALIPAY_SECRET_KEY              Flask 会话密钥
"""

import os

from config import DEBUG, HOST, MONITOR_ENABLED, PORT
from core.logger import get_logger, setup_logging
from web.factory import create_app

logger = get_logger("app")

app = create_app()


def _should_start_monitor() -> bool:
    if not MONITOR_ENABLED:
        return False
    # Flask debug reloader 会启动两个进程，只在真正的工作进程中启动监控
    if DEBUG and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return False
    return True


if __name__ == "__main__":
    setup_logging()
    if _should_start_monitor():
        from alipay_pay.monitor import MonitorThread

        MonitorThread().start()
    logger.info("服务已启动: http://%s:%s  （后台: /admin/）", HOST, PORT)
    app.run(host=HOST, port=PORT, debug=DEBUG, threaded=True, use_reloader=DEBUG)
