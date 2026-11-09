#!/bin/sh
# 容器入口：以 root 修复数据卷权限后，降权到 app 用户运行主进程。
set -e

DATA_DIR="${ALIPAY_DATA_DIR:-/app/data}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    chown -R app:app "$DATA_DIR"
    exec gosu app "$@"
fi

exec "$@"
