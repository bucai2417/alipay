# syntax=docker/dockerfile:1
# 码支付 · 卡密商城 运行镜像
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    ALIPAY_HOST=0.0.0.0 \
    ALIPAY_PORT=5000 \
    ALIPAY_DATA_DIR=/app/data

WORKDIR /app

# tzdata 让容器内 TZ=Asia/Shanghai 生效，日志与订单时间保持一致；gosu 用于降权启动
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# 默认以 root 进入入口脚本，脚本修复挂载卷权限后降权到 uid 1000 的 app 用户
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /app/data \
    && chown -R app:app /app

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:5000/health', timeout=3)"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "app.py"]
