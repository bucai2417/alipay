"""全局运行配置：路径、服务端口、监控开关。"""

import os

APP_VERSION = "1.0.0"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("ALIPAY_DATA_DIR") or os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "alipay.db")

HOST = os.getenv("ALIPAY_HOST", "0.0.0.0")
PORT = int(os.getenv("ALIPAY_PORT", "5000"))
DEBUG = os.getenv("ALIPAY_DEBUG", "0") == "1"

# 是否随 Web 服务启动账单监控线程（测试时可以 ALIPAY_MONITOR=0 关闭）
MONITOR_ENABLED = os.getenv("ALIPAY_MONITOR", "1") != "0"

# 会话密钥：优先环境变量，否则使用 data/.flask_secret 持久化
SECRET_KEY = os.getenv("ALIPAY_SECRET_KEY", "")

os.makedirs(DATA_DIR, exist_ok=True)
