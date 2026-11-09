#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "[1/3] 创建虚拟环境..."
  python3 -m venv .venv
  echo "[2/3] 安装依赖..."
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi

echo "[3/3] 启动服务 http://0.0.0.0:5000"
exec .venv/bin/python app.py
