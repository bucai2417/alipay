"""重置后台管理员密码。

用法（在项目根目录或任意目录执行）：
    python tools/reset_admin_password.py

脚本会更新 data/alipay.db 中的管理员密码哈希。建议先停止服务并备份 data/。
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from werkzeug.security import generate_password_hash

from core.settings import ADMIN_KEY, get_admin_auth
from core.storage import db


def main() -> int:
    print(f"数据库：{db.db_path}")
    print(f"当前用户名：{get_admin_auth().get('username', 'admin')}")

    password = getpass.getpass("新密码（至少 6 位）：")
    if len(password) < 6:
        print("新密码至少 6 位。", file=sys.stderr)
        return 1
    if password != getpass.getpass("再次输入新密码："):
        print("两次输入的密码不一致。", file=sys.stderr)
        return 1

    auth = get_admin_auth()
    auth["password_hash"] = generate_password_hash(password)
    db.settings_set(ADMIN_KEY, auth)
    print("管理员密码已更新，重启服务后使用新密码登录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
