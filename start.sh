#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "需要 Python 3.10 或更高版本。"
  exit 1
}

if [ ! -x ".venv/bin/python" ]; then
  echo "[1/3] 创建虚拟环境..."
  python3 -m venv .venv
else
  echo "[1/3] 虚拟环境已存在"
fi

if ! .venv/bin/python -c "import flask, requests, qrcode, PIL, cryptography, rsa, alipay, bleach, waitress" >/dev/null 2>&1; then
  echo "[2/3] 安装依赖..."
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
else
  echo "[2/3] 依赖已安装"
fi

echo "[3/3] 启动服务 http://${ALIPAY_HOST:-0.0.0.0}:${ALIPAY_PORT:-5000}"
exec .venv/bin/python app.py
