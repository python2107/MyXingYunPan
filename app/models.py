# app/models.py
import sqlite3
import os
import math
import shutil
from datetime import date, datetime
from pathlib import Path
from flask import g
from werkzeug.security import generate_password_hash
from config import DATABASE, UPLOAD_FOLDER

# ---------- 数据库连接 ----------
def get_db():
    """获取当前请求的数据库连接"""
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

def close_connection(exception):
    """请求结束后关闭数据库连接"""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# ---------- 初始化数据库（创建所有表及默认管理员）----------
def init_db(app):
    with app.app_context():
        db = get_db()
        cursor = db.cursor()

        # 用户表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("PRAGMA table_info(users)")
        cols = [c[1] for c in cursor.fetchall()]
        if 'is_admin' not in cols:
            cursor.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
        if 'coins' not in cols:
            cursor.execute("ALTER TABLE users ADD COLUMN coins INTEGER DEFAULT 100")
        if 'capacity_mb' not in cols:
            cursor.execute("ALTER TABLE users ADD COLUMN capacity_mb INTEGER DEFAULT 100")
        if 'email' not in cols:
            cursor.execute("ALTER TABLE users ADD COLUMN email TEXT")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email IS NOT NULL")

        # 验证码表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS email_verification_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                code TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                used INTEGER DEFAULT 0
            )
        """)

        # 文件表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                is_public INTEGER DEFAULT 0,
                likes INTEGER DEFAULT 0,
                collections INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        # 星币变动日志表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS coin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                change_amount INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        # 点赞记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS file_likes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                file_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, file_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (file_id) REFERENCES files(id)
            )
        """)

        # 收藏记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS file_collections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                file_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, file_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (file_id) REFERENCES files(id)
            )
        """)

        # 签到记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sign_in_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                sign_date DATE NOT NULL,
                coins_gained INTEGER NOT NULL,
                UNIQUE(user_id, sign_date),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        # 容量兑换记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS exchange_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                coins_spent INTEGER NOT NULL,
                mb_gained INTEGER NOT NULL,
                total_capacity INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        # 好友关系表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS friends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                friend_id INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (friend_id) REFERENCES users(id),
                UNIQUE(user_id, friend_id)
            )
        """)

        # 聊天消息表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sender_id) REFERENCES users(id),
                FOREIGN KEY (receiver_id) REFERENCES users(id)
            )
        """)

        db.commit()

        # 创建默认管理员账号
        admin = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()
        if not admin:
            db.execute(
                "INSERT INTO users (username, password_hash, is_admin, coins, capacity_mb, email) VALUES (?, ?, 1, 10000, 10240, ?)",
                ("admin", generate_password_hash("123465"), "admin@163.com")
            )
            db.commit()
            print("管理员账号已创建: admin / 123465")
        else:
            # 确保管理员容量足够大
            db.execute("UPDATE users SET capacity_mb = 10240 WHERE username='admin' AND capacity_mb < 10240")
            db.commit()

# ---------- 用户文件夹辅助函数 ----------
def get_user_folder(user_id: int) -> Path:
    """获取用户专属文件夹路径，不存在则创建"""
    folder = Path(UPLOAD_FOLDER) / str(user_id)
    folder.mkdir(exist_ok=True)
    return folder

# ---------- 星币相关 ----------
def get_user_coins(user_id: int) -> int:
    db = get_db()
    row = db.execute("SELECT coins FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["coins"] if row else 0

def update_user_coins(user_id: int, delta: int, reason: str) -> bool:
    """调整用户星币，返回是否成功（余额不能为负）"""
    db = get_db()
    current = get_user_coins(user_id)
    new_balance = current + delta
    if new_balance < 0:
        return False
    db.execute("UPDATE users SET coins = ? WHERE id = ?", (new_balance, user_id))
    db.execute(
        "INSERT INTO coin_logs (user_id, change_amount, balance_after, reason) VALUES (?, ?, ?, ?)",
        (user_id, delta, new_balance, reason)
    )
    db.commit()
    return True

# ---------- 容量相关 ----------
def get_user_capacity(user_id: int) -> int:
    """返回用户当前已用容量（MB）"""
    db = get_db()
    total_bytes = db.execute(
        "SELECT COALESCE(SUM(size_bytes), 0) FROM files WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    return math.ceil(total_bytes / (1024 * 1024))

def check_capacity(user_id: int, new_file_size_bytes: int) -> bool:
    """检查上传后是否会超出容量限制"""
    db = get_db()
    user = db.execute("SELECT capacity_mb FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return False
    used_mb = get_user_capacity(user_id)
    new_file_mb = math.ceil(new_file_size_bytes / (1024 * 1024))
    return used_mb + new_file_mb <= user["capacity_mb"]

# ---------- 签到相关 ----------
def can_sign_in(user_id: int) -> bool:
    db = get_db()
    today = date.today().isoformat()
    record = db.execute(
        "SELECT id FROM sign_in_log WHERE user_id = ? AND sign_date = ?",
        (user_id, today)
    ).fetchone()
    return record is None

def do_sign_in(user_id: int) -> int:
    """执行签到，返回获得的星币数（固定10）"""
    if not can_sign_in(user_id):
        return 0
    gain = 10
    db = get_db()
    today = date.today().isoformat()
    db.execute(
        "INSERT INTO sign_in_log (user_id, sign_date, coins_gained) VALUES (?, ?, ?)",
        (user_id, today, gain)
    )
    update_user_coins(user_id, gain, f"签到获得{gain}星币")
    db.commit()
    return gain

# ---------- 好友相关 ----------
def are_friends(uid1: int, uid2: int) -> bool:
    db = get_db()
    row = db.execute(
        "SELECT id FROM friends WHERE ((user_id=? AND friend_id=?) OR (user_id=? AND friend_id=?)) AND status='accepted'",
        (uid1, uid2, uid2, uid1)
    ).fetchone()
    return row is not None

def send_friend_request(from_id: int, to_id: int) -> bool:
    if from_id == to_id:
        return False
    db = get_db()
    existing = db.execute(
        "SELECT id FROM friends WHERE (user_id=? AND friend_id=?) OR (user_id=? AND friend_id=?)",
        (from_id, to_id, to_id, from_id)
    ).fetchone()
    if existing:
        return False
    db.execute(
        "INSERT INTO friends (user_id, friend_id, status) VALUES (?, ?, 'pending')",
        (from_id, to_id)
    )
    db.commit()
    return True

def accept_friend_request(req_id: int, user_id: int) -> bool:
    db = get_db()
    req = db.execute(
        "SELECT id, user_id, friend_id FROM friends WHERE id=? AND status='pending'",
        (req_id,)
    ).fetchone()
    if not req or req["friend_id"] != user_id:
        return False
    db.execute("UPDATE friends SET status='accepted' WHERE id=?", (req_id,))
    db.commit()
    return True

# ---------- 文件信息辅助（可选，便于蓝图使用）----------
def get_file_info_from_record(record):
    """将数据库记录转换为前端需要的文件信息字典"""
    from app.utils import human_readable_size  # 避免循环导入，延迟导入
    return {
        "id": record["id"],
        "name": record["filename"],
        "size": record["size_bytes"],
        "size_human": human_readable_size(record["size_bytes"]),
        "is_public": bool(record["is_public"]),
        "likes": record["likes"],
        "collections": record["collections"],
        "created_at": record["created_at"],
        "download_url": f"/numfile/{record['id']}"
    }