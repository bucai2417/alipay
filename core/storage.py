"""SQLite 存储层。

单库 ``data/alipay.db``，WAL 模式；每个线程独立连接，写入用进程内锁串行化。
包含码支付订单、商城（商品/卡密/订单/客服）与键值配置表。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime

from config import DB_PATH
from core.logger import get_logger

logger = get_logger("storage")

SCHEMA = """
CREATE TABLE IF NOT EXISTS codepay_orders (
    id VARCHAR(32) PRIMARY KEY,
    out_trade_no VARCHAR(64) NOT NULL,
    type VARCHAR(10) NOT NULL,
    pid VARCHAR(32) NOT NULL DEFAULT '',
    name VARCHAR(255) NOT NULL,
    price DECIMAL(10, 2) NOT NULL,
    payment_amount DECIMAL(10, 2) DEFAULT 0,
    status TINYINT(1) DEFAULT 0,
    add_time DATETIME NOT NULL,
    pay_time DATETIME,
    notify_url VARCHAR(255) DEFAULT '',
    return_url VARCHAR(255) DEFAULT '',
    sitename VARCHAR(255) DEFAULT '',
    source VARCHAR(16) DEFAULT 'api',
    notify_status TINYINT(1) DEFAULT 0,
    notify_attempts INTEGER DEFAULT 0,
    notify_last_error VARCHAR(255) DEFAULT '',
    notify_next_at DATETIME
);
CREATE INDEX IF NOT EXISTS idx_cp_out_trade ON codepay_orders(out_trade_no);
CREATE INDEX IF NOT EXISTS idx_cp_status_amount ON codepay_orders(status, payment_amount);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(128) NOT NULL,
    description TEXT DEFAULT '',
    price DECIMAL(10, 2) NOT NULL,
    status TINYINT(1) DEFAULT 1,
    fulfill_mode VARCHAR(16) NOT NULL DEFAULT 'stock',
    generate_config TEXT DEFAULT '{}',
    stock_count INTEGER DEFAULT 0,
    sold_count INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    icon_path VARCHAR(255) DEFAULT '',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'available',
    sold_order_id INTEGER,
    sold_at DATETIME,
    created_at DATETIME NOT NULL,
    UNIQUE(product_id, code),
    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cards_product_status ON cards(product_id, status);

CREATE TABLE IF NOT EXISTS shop_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no VARCHAR(48) NOT NULL UNIQUE,
    product_id INTEGER NOT NULL,
    product_name_snapshot VARCHAR(128) NOT NULL,
    price DECIMAL(10, 2) NOT NULL,
    qty INTEGER NOT NULL DEFAULT 1,
    buyer_platform VARCHAR(32) DEFAULT '',
    buyer_user_id VARCHAR(128) DEFAULT '',
    contact VARCHAR(255) DEFAULT '',
    payment_out_trade_no VARCHAR(64) NOT NULL UNIQUE,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    card_payload TEXT DEFAULT '',
    fail_reason TEXT DEFAULT '',
    email_status TINYINT(1) DEFAULT 0,
    email_error VARCHAR(255) DEFAULT '',
    email_sent_at DATETIME,
    created_at DATETIME NOT NULL,
    paid_at DATETIME,
    fulfilled_at DATETIME
);
CREATE INDEX IF NOT EXISTS idx_shop_orders_status ON shop_orders(status);
CREATE INDEX IF NOT EXISTS idx_shop_orders_contact ON shop_orders(contact);

CREATE TABLE IF NOT EXISTS feedback_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact VARCHAR(200) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'open',
    last_message TEXT DEFAULT '',
    last_sender VARCHAR(16) DEFAULT 'user',
    admin_read_at DATETIME,
    notify_email VARCHAR(255) DEFAULT '',
    notify_enabled TINYINT(1) DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_contact ON feedback_tickets(contact);
CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback_tickets(status);

CREATE TABLE IF NOT EXISTS feedback_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL,
    sender VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    attachment_url VARCHAR(255) DEFAULT '',
    attachment_name VARCHAR(255) DEFAULT '',
    attachment_type VARCHAR(64) DEFAULT '',
    attachment_size INTEGER DEFAULT 0,
    created_at DATETIME NOT NULL,
    FOREIGN KEY(ticket_id) REFERENCES feedback_tickets(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_feedback_msg_ticket ON feedback_messages(ticket_id);

CREATE TABLE IF NOT EXISTS settings (
    key VARCHAR(64) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at DATETIME NOT NULL
);
"""


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Database:
    _instance: "Database | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                obj = super().__new__(cls)
                obj._initialized = False
                cls._instance = obj
            return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.db_path = DB_PATH
        self._local = threading.local()
        self.write_lock = threading.Lock()
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_schema()

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, timeout=30)
            c.row_factory = sqlite3.Row
            # journal_mode=WAL 已在初始化时设置（数据库级持久生效），
            # 新连接不再重复设置，避免多线程首次建连时争抢排他锁。
            c.execute("PRAGMA busy_timeout=30000")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.conn = c
        return c

    def _init_schema(self):
        """建表；旧库缺列时做增量兼容。"""
        c = self.conn()
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript(SCHEMA)
        # 旧库兼容：codepay_orders 缺列
        cols = {r[1] for r in c.execute("PRAGMA table_info(codepay_orders)").fetchall()}
        if "payment_amount" not in cols:
            c.execute("ALTER TABLE codepay_orders ADD COLUMN payment_amount DECIMAL(10, 2) DEFAULT 0")
        if "source" not in cols:
            c.execute("ALTER TABLE codepay_orders ADD COLUMN source VARCHAR(16) DEFAULT 'api'")
        # 回调状态（旧库增量升级）
        for col, ddl in (
            ("notify_status", "ALTER TABLE codepay_orders ADD COLUMN notify_status TINYINT(1) DEFAULT 0"),
            ("notify_attempts", "ALTER TABLE codepay_orders ADD COLUMN notify_attempts INTEGER DEFAULT 0"),
            ("notify_last_error", "ALTER TABLE codepay_orders ADD COLUMN notify_last_error VARCHAR(255) DEFAULT ''"),
            ("notify_next_at", "ALTER TABLE codepay_orders ADD COLUMN notify_next_at DATETIME"),
        ):
            if col not in cols:
                c.execute(ddl)
        cols = {r[1] for r in c.execute("PRAGMA table_info(feedback_tickets)").fetchall()}
        if "admin_read_at" not in cols:
            c.execute("ALTER TABLE feedback_tickets ADD COLUMN admin_read_at DATETIME")
        for col, ddl in (
            ("notify_email", "ALTER TABLE feedback_tickets ADD COLUMN notify_email VARCHAR(255) DEFAULT ''"),
            ("notify_enabled", "ALTER TABLE feedback_tickets ADD COLUMN notify_enabled TINYINT(1) DEFAULT 1"),
        ):
            if col not in cols:
                c.execute(ddl)
        msg_cols = {r[1] for r in c.execute("PRAGMA table_info(feedback_messages)").fetchall()}
        for col, ddl in (
            ("attachment_url", "ALTER TABLE feedback_messages ADD COLUMN attachment_url VARCHAR(255) DEFAULT ''"),
            ("attachment_name", "ALTER TABLE feedback_messages ADD COLUMN attachment_name VARCHAR(255) DEFAULT ''"),
            ("attachment_type", "ALTER TABLE feedback_messages ADD COLUMN attachment_type VARCHAR(64) DEFAULT ''"),
            ("attachment_size", "ALTER TABLE feedback_messages ADD COLUMN attachment_size INTEGER DEFAULT 0"),
        ):
            if col not in msg_cols:
                c.execute(ddl)
        # 商品图标与邮件通知（旧库增量升级）
        prod_cols = {r[1] for r in c.execute("PRAGMA table_info(products)").fetchall()}
        if "icon_path" not in prod_cols:
            c.execute("ALTER TABLE products ADD COLUMN icon_path VARCHAR(255) DEFAULT ''")
        order_cols = {r[1] for r in c.execute("PRAGMA table_info(shop_orders)").fetchall()}
        for col, ddl in (
            ("email_status", "ALTER TABLE shop_orders ADD COLUMN email_status TINYINT(1) DEFAULT 0"),
            ("email_error", "ALTER TABLE shop_orders ADD COLUMN email_error VARCHAR(255) DEFAULT ''"),
            ("email_sent_at", "ALTER TABLE shop_orders ADD COLUMN email_sent_at DATETIME"),
        ):
            if col not in order_cols:
                c.execute(ddl)
        c.commit()
        logger.info("数据库已就绪: %s", self.db_path)

    # ---------- settings ----------

    def settings_get(self, key: str, default=None):
        row = self.conn().execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    def settings_set(self, key: str, value) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        c = self.conn()
        with self.write_lock:
            c.execute(
                """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                                  updated_at = excluded.updated_at""",
                (key, payload, now_str()),
            )
            c.commit()

    def settings_all(self) -> dict:
        rows = self.conn().execute("SELECT key, value FROM settings").fetchall()
        out = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except Exception:
                out[r["key"]] = r["value"]
        return out


db = Database()
