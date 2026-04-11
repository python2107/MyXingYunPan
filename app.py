import os
import sqlite3
from datetime import timedelta, datetime, date
from pathlib import Path
from functools import wraps
import mimetypes
import shutil
import math

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_from_directory,
    abort,
    session,
    g,
    redirect,
    url_for,
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = "change-this-in-production"
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

BASE_UPLOAD_FOLDER = Path("uploads")
BASE_UPLOAD_FOLDER.mkdir(exist_ok=True)

DATABASE = "users.db"

# ==================== 数据库操作 ====================
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

def init_db():
    """初始化数据库：创建所有表，添加缺失的列，创建默认管理员"""
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        
        # 1. 创建 users 表（基础表）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 2. 为 users 表添加缺失的列（安全执行）
        cursor.execute("PRAGMA table_info(users)")
        existing_columns = [col[1] for col in cursor.fetchall()]
        
        if 'is_admin' not in existing_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
            print("已添加 is_admin 列")
        if 'coins' not in existing_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN coins INTEGER DEFAULT 100")
            print("已添加 coins 列")
        if 'capacity_mb' not in existing_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN capacity_mb INTEGER DEFAULT 100")
            print("已添加 capacity_mb 列")
        
        # 3. 创建所有其他表（如果不存在）
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
        # 星币日志表
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
        # 点赞表
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
        # 收藏表
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
        # 签到表
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
        # 兑换表
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
        # 好友表
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
        # 消息表
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
        
        # 4. 创建默认管理员（如果不存在）
        admin = db.execute("SELECT id FROM users WHERE username = ?", ("admin",)).fetchone()
        if not admin:
            db.execute(
                "INSERT INTO users (username, password_hash, is_admin, coins, capacity_mb) VALUES (?, ?, 1, 10000, 10240)",
                ("admin", generate_password_hash("123465"))
            )
            db.commit()
            print("管理员账号已创建: admin / 123465，初始星币10000，容量10GB")
        else:
            # 确保管理员有足够的容量和星币（可选）
            db.execute("UPDATE users SET capacity_mb = 10240 WHERE username = 'admin' AND capacity_mb < 10240")
            db.commit()
init_db()

# ==================== 辅助函数 ====================
def get_user_folder(user_id: int) -> Path:
    user_folder = BASE_UPLOAD_FOLDER / str(user_id)
    user_folder.mkdir(exist_ok=True)
    return user_folder

def safe_filename(original: str) -> str:
    """安全化文件名，只保留字母、数字、点、下划线、横线、空格，其他字符替换为下划线"""
    base = os.path.basename(original)
    safe_chars = []
    for ch in base:
        if ch.isalnum() or ch in ".-_ ":
            safe_chars.append(ch)
        else:
            safe_chars.append("_")
    return "".join(safe_chars).strip()

def unique_filename(directory: Path, filename: str) -> str:
    """避免重名，如果文件已存在则添加序号"""
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1
    new_name = filename
    while (directory / new_name).exists():
        new_name = f"{stem} ({counter}){suffix}"
        counter += 1
    return new_name

def human_readable_size(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"

def get_file_info_from_record(file_record):
    """从files表记录构造返回信息"""
    return {
        "id": file_record["id"],
        "name": file_record["filename"],
        "size": file_record["size_bytes"],
        "size_human": human_readable_size(file_record["size_bytes"]),
        "is_public": bool(file_record["is_public"]),
        "likes": file_record["likes"],
        "collections": file_record["collections"],
        "created_at": file_record["created_at"],
        "download_url": f"/api/download/{file_record['id']}",
        "uploader": None  # 会在需要时填充
    }

def get_user_coins(user_id: int) -> int:
    db = get_db()
    row = db.execute("SELECT coins FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["coins"] if row else 0

def update_user_coins(user_id: int, delta: int, reason: str) -> bool:
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

def get_user_capacity(user_id: int) -> int:
    """返回用户当前已用容量（MB）"""
    db = get_db()
    # 计算所有文件总大小（字节转MB）
    total_bytes = db.execute(
        "SELECT COALESCE(SUM(size_bytes), 0) FROM files WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    return math.ceil(total_bytes / (1024*1024))

def check_capacity(user_id: int, new_file_size_bytes: int) -> bool:
    """检查上传新文件后是否超出用户总容量限制"""
    db = get_db()
    user = db.execute("SELECT capacity_mb FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return False
    total_capacity_mb = user["capacity_mb"]
    used_mb = get_user_capacity(user_id)
    new_file_mb = math.ceil(new_file_size_bytes / (1024*1024))
    return (used_mb + new_file_mb) <= total_capacity_mb

# 签到功能
def can_sign_in(user_id: int) -> bool:
    db = get_db()
    today = date.today().isoformat()
    record = db.execute(
        "SELECT id FROM sign_in_log WHERE user_id = ? AND sign_date = ?",
        (user_id, today)
    ).fetchone()
    return record is None

def do_sign_in(user_id: int) -> int:
    """执行签到，返回获得星币数（固定10）"""
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

# 好友相关
def are_friends(user_id1: int, user_id2: int) -> bool:
    db = get_db()
    row = db.execute(
        "SELECT id FROM friends WHERE ((user_id=? AND friend_id=?) OR (user_id=? AND friend_id=?)) AND status='accepted'",
        (user_id1, user_id2, user_id2, user_id1)
    ).fetchone()
    return row is not None

def send_friend_request(from_user_id: int, to_user_id: int) -> bool:
    if from_user_id == to_user_id:
        return False
    db = get_db()
    # 检查是否已经是好友或已有请求
    existing = db.execute(
        "SELECT id, status FROM friends WHERE (user_id=? AND friend_id=?) OR (user_id=? AND friend_id=?)",
        (from_user_id, to_user_id, to_user_id, from_user_id)
    ).fetchone()
    if existing:
        return False
    db.execute(
        "INSERT INTO friends (user_id, friend_id, status) VALUES (?, ?, 'pending')",
        (from_user_id, to_user_id)
    )
    db.commit()
    return True

def accept_friend_request(request_id: int, user_id: int) -> bool:
    db = get_db()
    req = db.execute(
        "SELECT id, user_id, friend_id FROM friends WHERE id=? AND status='pending'",
        (request_id,)
    ).fetchone()
    if not req or req["friend_id"] != user_id:
        return False
    db.execute("UPDATE friends SET status='accepted' WHERE id=?", (request_id,))
    db.commit()
    return True

# ==================== 权限装饰器 ====================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login_page"))
        db = get_db()
        user = db.execute("SELECT is_admin FROM users WHERE id = ?", (session["user_id"],)).fetchone()
        if not user or not user["is_admin"]:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

# ==================== 页面路由 ====================
@app.route("/")
def index():
    if "user_id" in session:
        db = get_db()
        user = db.execute("SELECT is_admin FROM users WHERE id = ?", (session["user_id"],)).fetchone()
        if user and user["is_admin"]:
            return redirect(url_for("admin_dashboard"))
        else:
            return redirect(url_for("user_center"))
    return redirect(url_for("login_page"))

@app.route("/login")
def login_page():
    if "user_id" in session:
        return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/user_center")
@login_required
def user_center():
    """用户个人中心（新界面）"""
    return render_template("user_center.html")

@app.route("/community")
def community_page():
    """社区公开文件页面，免注册可查看"""
    return render_template("community.html")

@app.route("/admin")
@admin_required
def admin_dashboard():
    return render_template("admin.html")

@app.route("/privacy")
def privacy_policy():
    now = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    return render_template("privacy_policy.html", now_time=now)

@app.route("/terms")
def user_agreement():
    now = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    return render_template("user_agreement.html", now_time=now)

# ==================== API：认证 ====================
@app.route("/api/check_auth")
def check_auth():
    if "user_id" in session:
        db = get_db()
        user = db.execute(
            "SELECT id, username, is_admin FROM users WHERE id = ?", (session["user_id"],)
        ).fetchone()
        if user:
            return jsonify({
                "success": True,
                "user": {
                    "id": user["id"],
                    "username": user["username"],
                    "is_admin": bool(user["is_admin"])
                }
            })
        else:
            session.clear()
    return jsonify({"success": False}), 401

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password:
        return jsonify({"success": False, "error": "用户名和密码不能为空"}), 400
    if len(username) < 3 or len(username) > 20:
        return jsonify({"success": False, "error": "用户名长度需3-20位"}), 400
    if len(password) < 6:
        return jsonify({"success": False, "error": "密码长度至少6位"}), 400
    if username == "admin":
        return jsonify({"success": False, "error": "此用户名不可用"}), 400

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        return jsonify({"success": False, "error": "用户名已存在"}), 400

    password_hash = generate_password_hash(password)
    try:
        db.execute(
            "INSERT INTO users (username, password_hash, is_admin, coins, capacity_mb) VALUES (?, ?, 0, 100, 100)",
            (username, password_hash)
        )
        db.commit()
        user_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        get_user_folder(user_id)
        # 记录赠送星币日志
        db.execute(
            "INSERT INTO coin_logs (user_id, change_amount, balance_after, reason) VALUES (?, ?, ?, ?)",
            (user_id, 100, 100, "注册赠送100星币")
        )
        db.commit()
        return jsonify({"success": True, "message": "注册成功"})
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "error": "用户名已存在"}), 400

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password:
        return jsonify({"success": False, "error": "用户名和密码不能为空"}), 400
    db = get_db()
    user = db.execute(
        "SELECT id, username, password_hash, is_admin FROM users WHERE username = ?", (username,)
    ).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"success": False, "error": "用户名或密码错误"}), 401
    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return jsonify({
        "success": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "is_admin": bool(user["is_admin"])
        }
    })

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})

# ==================== API：用户个人中心 ====================
@app.route("/api/user/info")
@login_required
def user_info():
    user_id = session["user_id"]
    db = get_db()
    user = db.execute(
        "SELECT id, username, coins, capacity_mb, created_at FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()
    used_mb = get_user_capacity(user_id)
    return jsonify({
        "success": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "coins": user["coins"],
            "capacity_total_mb": user["capacity_mb"],
            "capacity_used_mb": used_mb,
            "joined_at": user["created_at"]
        }
    })

@app.route("/api/user/change_password", methods=["POST"])
@login_required
def change_password():
    data = request.get_json()
    old_password = data.get("old_password", "")
    new_password = data.get("new_password", "")
    if not old_password or not new_password:
        return jsonify({"success": False, "error": "请填写完整信息"}), 400
    if len(new_password) < 6:
        return jsonify({"success": False, "error": "新密码至少6位"}), 400
    db = get_db()
    user = db.execute("SELECT password_hash FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    if not check_password_hash(user["password_hash"], old_password):
        return jsonify({"success": False, "error": "原密码错误"}), 401
    new_hash = generate_password_hash(new_password)
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, session["user_id"]))
    db.commit()
    return jsonify({"success": True, "message": "密码修改成功"})

@app.route("/api/user/delete_account", methods=["POST"])
@login_required
def delete_account():
    user_id = session["user_id"]
    db = get_db()
    user = db.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    if user and user["is_admin"]:
        return jsonify({"success": False, "error": "管理员账号不可注销"}), 403
    # 删除用户所有文件记录和物理文件
    files = db.execute("SELECT file_path FROM files WHERE user_id = ?", (user_id,)).fetchall()
    for f in files:
        path = Path(f["file_path"])
        if path.exists():
            path.unlink()
    db.execute("DELETE FROM files WHERE user_id = ?", (user_id,))
    # 删除好友关系
    db.execute("DELETE FROM friends WHERE user_id=? OR friend_id=?", (user_id, user_id))
    # 删除聊天消息
    db.execute("DELETE FROM messages WHERE sender_id=? OR receiver_id=?", (user_id, user_id))
    # 删除点赞收藏记录
    db.execute("DELETE FROM file_likes WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM file_collections WHERE user_id=?", (user_id,))
    # 删除签到和兑换记录
    db.execute("DELETE FROM sign_in_log WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM exchange_log WHERE user_id=?", (user_id,))
    # 删除用户目录
    user_folder = get_user_folder(user_id)
    if user_folder.exists():
        shutil.rmtree(user_folder)
    # 删除用户记录
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    session.clear()
    return jsonify({"success": True, "message": "账号已注销"})

# ==================== API：星币与签到 ====================
@app.route("/api/user/coins")
@login_required
def get_coins():
    coins = get_user_coins(session["user_id"])
    return jsonify({"success": True, "coins": coins})

@app.route("/api/user/coin_logs")
@login_required
def get_coin_logs():
    user_id = session["user_id"]
    db = get_db()
    logs = db.execute(
        "SELECT change_amount, balance_after, reason, created_at FROM coin_logs WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
        (user_id,)
    ).fetchall()
    log_list = [{
        "change": log["change_amount"],
        "balance": log["balance_after"],
        "reason": log["reason"],
        "time": log["created_at"]
    } for log in logs]
    return jsonify({"success": True, "logs": log_list})

@app.route("/api/user/sign_in", methods=["POST"])
@login_required
def sign_in():
    user_id = session["user_id"]
    if not can_sign_in(user_id):
        return jsonify({"success": False, "error": "今日已签到"}), 400
    gained = do_sign_in(user_id)
    return jsonify({"success": True, "gained": gained, "new_balance": get_user_coins(user_id)})

@app.route("/api/user/exchange_capacity", methods=["POST"])
@login_required
def exchange_capacity():
    """星币兑换容量：1星币 = 0.5 MB，需要整数星币"""
    data = request.get_json()
    coins_to_spend = data.get("coins", 0)
    if not isinstance(coins_to_spend, int) or coins_to_spend <= 0:
        return jsonify({"success": False, "error": "请输入正整数的星币数量"}), 400
    user_id = session["user_id"]
    current_coins = get_user_coins(user_id)
    if current_coins < coins_to_spend:
        return jsonify({"success": False, "error": "星币不足"}), 400
    mb_gain = coins_to_spend // 2  # 因为0.5MB per coin，整数除法
    if mb_gain == 0 and coins_to_spend > 0:
        mb_gain = 1  # 至少1MB
    # 扣除星币，增加容量
    db = get_db()
    if not update_user_coins(user_id, -coins_to_spend, f"兑换{mb_gain}MB容量消耗{coins_to_spend}星币"):
        return jsonify({"success": False, "error": "扣币失败"}), 500
    db.execute(
        "UPDATE users SET capacity_mb = capacity_mb + ? WHERE id = ?",
        (mb_gain, user_id)
    )
    db.execute(
        "INSERT INTO exchange_log (user_id, coins_spent, mb_gained, total_capacity) VALUES (?, ?, ?, (SELECT capacity_mb FROM users WHERE id=?))",
        (user_id, coins_to_spend, mb_gain, user_id)
    )
    db.commit()
    new_capacity = db.execute("SELECT capacity_mb FROM users WHERE id=?", (user_id,)).fetchone()["capacity_mb"]
    return jsonify({"success": True, "mb_gained": mb_gain, "new_capacity_mb": new_capacity, "new_balance": get_user_coins(user_id)})

# ==================== API：文件上传与管理（含公开/私密、星币奖励）====================
@app.route("/api/files")
@login_required
def list_my_files():
    user_id = session["user_id"]
    db = get_db()
    records = db.execute(
        "SELECT id, filename, size_bytes, is_public, likes, collections, created_at FROM files WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,)
    ).fetchall()
    files = [get_file_info_from_record(r) for r in records]
    return jsonify({"success": True, "files": files})

@app.route("/api/upload", methods=["POST"])
@login_required
def upload_file():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "没有文件部分"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"success": False, "error": "未选择文件"}), 400

    # 获取额外参数：是否公开
    is_public = request.form.get("is_public", "0") == "1"

    original_filename = safe_filename(file.filename)
    if not original_filename:
        return jsonify({"success": False, "error": "无效的文件名"}), 400

    user_id = session["user_id"]
    # 检查容量
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    if not check_capacity(user_id, file_size):
        return jsonify({"success": False, "error": "存储容量不足，请兑换容量或删除旧文件"}), 403

    # 检查星币（上传需要消耗星币？按新规则：上传文件奖励星币（文件大小MB向下取整），但之前的设计是消耗，现改为奖励。此处实现奖励规则）
    # 规则：上传文件 + 文件大小(MB)向下取整 星币（至少1）
    size_mb = max(1, file_size // (1024*1024))
    # 先保存文件，成功后增加星币
    user_folder = get_user_folder(user_id)
    final_name = unique_filename(user_folder, original_filename)
    try:
        file.save(user_folder / final_name)
        # 记录到 files 表
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO files (user_id, filename, file_path, size_bytes, is_public) VALUES (?, ?, ?, ?, ?)",
            (user_id, final_name, str(user_folder / final_name), file_size, 1 if is_public else 0)
        )
        file_id = cursor.lastrowid
        db.commit()
        # 奖励星币
        reward = size_mb
        update_user_coins(user_id, reward, f"上传文件 {final_name} 获得 {reward} 星币")
        return jsonify({
            "success": True,
            "message": f"上传成功，获得{reward}星币",
            "file": get_file_info_from_record(
                db.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
            )
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"保存失败: {str(e)}"}), 500

@app.route("/api/download/<int:file_id>")
def download_file(file_id):
    """公开文件可免注册下载，私密文件需登录且为文件主人或好友"""
    db = get_db()
    file_record = db.execute(
        "SELECT user_id, filename, file_path, is_public FROM files WHERE id = ?", (file_id,)
    ).fetchone()
    if not file_record:
        abort(404)
    # 如果是公开文件，允许所有人下载
    if file_record["is_public"]:
        path = Path(file_record["file_path"])
        if path.exists():
            return send_from_directory(
                directory=path.parent,
                path=path.name,
                as_attachment=True,
                download_name=file_record["filename"],
            )
        else:
            abort(404)
    # 私密文件：需要登录且为文件主人
    if "user_id" not in session:
        return redirect(url_for("login_page"))
    if session["user_id"] != file_record["user_id"]:
        # 可以扩展好友分享权限，这里简单拒绝
        abort(403)
    path = Path(file_record["file_path"])
    if not path.exists():
        abort(404)
    return send_from_directory(
        directory=path.parent,
        path=path.name,
        as_attachment=True,
        download_name=file_record["filename"],
    )

@app.route("/api/delete/<int:file_id>", methods=["DELETE"])
@login_required
def delete_file(file_id):
    db = get_db()
    file_record = db.execute(
        "SELECT user_id, file_path FROM files WHERE id = ?", (file_id,)
    ).fetchone()
    if not file_record or file_record["user_id"] != session["user_id"]:
        return jsonify({"success": False, "error": "无权限或文件不存在"}), 404
    path = Path(file_record["file_path"])
    if path.exists():
        path.unlink()
    db.execute("DELETE FROM files WHERE id = ?", (file_id,))
    db.execute("DELETE FROM file_likes WHERE file_id = ?", (file_id,))
    db.execute("DELETE FROM file_collections WHERE file_id = ?", (file_id,))
    db.commit()
    return jsonify({"success": True, "message": "删除成功"})

@app.route("/api/file/toggle_public/<int:file_id>", methods=["POST"])
@login_required
def toggle_public(file_id):
    db = get_db()
    file_record = db.execute(
        "SELECT user_id, is_public FROM files WHERE id = ?", (file_id,)
    ).fetchone()
    if not file_record or file_record["user_id"] != session["user_id"]:
        return jsonify({"success": False, "error": "无权限"}), 403
    new_status = 1 - file_record["is_public"]
    db.execute("UPDATE files SET is_public = ? WHERE id = ?", (new_status, file_id))
    db.commit()
    return jsonify({"success": True, "is_public": bool(new_status)})

# ==================== API：社区公开文件 ====================
@app.route("/api/community/files")
def community_files():
    """公开文件列表，按点赞数排序，可选分页"""
    db = get_db()
    # 联查上传者用户名
    records = db.execute("""
        SELECT f.id, f.filename, f.size_bytes, f.likes, f.collections, f.created_at, u.username as uploader
        FROM files f
        JOIN users u ON f.user_id = u.id
        WHERE f.is_public = 1
        ORDER BY f.likes DESC, f.created_at DESC
        LIMIT 100
    """).fetchall()
    files = []
    for r in records:
        files.append({
            "id": r["id"],
            "name": r["filename"],
            "size_human": human_readable_size(r["size_bytes"]),
            "uploader": r["uploader"],
            "likes": r["likes"],
            "collections": r["collections"],
            "created_at": r["created_at"],
            "download_url": f"/api/download/{r['id']}"
        })
    return jsonify({"success": True, "files": files})

@app.route("/api/community/like/<int:file_id>", methods=["POST"])
@login_required
def like_file(file_id):
    user_id = session["user_id"]
    db = get_db()
    # 检查文件是否存在且公开
    file = db.execute("SELECT id, user_id, is_public FROM files WHERE id = ?", (file_id,)).fetchone()
    if not file or not file["is_public"]:
        return jsonify({"success": False, "error": "文件不存在或非公开"}), 404
    # 检查是否已经点赞
    existing = db.execute(
        "SELECT id FROM file_likes WHERE user_id = ? AND file_id = ?",
        (user_id, file_id)
    ).fetchone()
    if existing:
        return jsonify({"success": False, "error": "你已经点过赞了"}), 400
    db.execute(
        "INSERT INTO file_likes (user_id, file_id) VALUES (?, ?)",
        (user_id, file_id)
    )
    db.execute("UPDATE files SET likes = likes + 1 WHERE id = ?", (file_id,))
    # 奖励点赞者？规则：点赞不奖励星币，但作者获得星币（点赞数量个星币）
    # 这里实现：点赞时，文件作者获得1星币（每个点赞1星币）
    update_user_coins(file["user_id"], 1, f"文件 {file_id} 获得一个点赞")
    db.commit()
    return jsonify({"success": True, "message": "点赞成功"})

@app.route("/api/community/collect/<int:file_id>", methods=["POST"])
@login_required
def collect_file(file_id):
    user_id = session["user_id"]
    db = get_db()
    file = db.execute("SELECT id, user_id, is_public FROM files WHERE id = ?", (file_id,)).fetchone()
    if not file or not file["is_public"]:
        return jsonify({"success": False, "error": "文件不存在或非公开"}), 404
    existing = db.execute(
        "SELECT id FROM file_collections WHERE user_id = ? AND file_id = ?",
        (user_id, file_id)
    ).fetchone()
    if existing:
        return jsonify({"success": False, "error": "你已经收藏过了"}), 400
    db.execute(
        "INSERT INTO file_collections (user_id, file_id) VALUES (?, ?)",
        (user_id, file_id)
    )
    db.execute("UPDATE files SET collections = collections + 1 WHERE id = ?", (file_id,))
    # 收藏奖励：作者获得2星币（按规则：收藏数量*2个星币，此处每个收藏给2星币）
    update_user_coins(file["user_id"], 2, f"文件 {file_id} 获得一个收藏")
    db.commit()
    return jsonify({"success": True, "message": "收藏成功"})

@app.route("/api/user/my_collections")
@login_required
def my_collections():
    user_id = session["user_id"]
    db = get_db()
    records = db.execute("""
        SELECT f.id, f.filename, f.size_bytes, f.likes, f.collections, f.created_at, u.username as uploader
        FROM files f
        JOIN file_collections c ON f.id = c.file_id
        JOIN users u ON f.user_id = u.id
        WHERE c.user_id = ?
        ORDER BY c.created_at DESC
    """, (user_id,)).fetchall()
    files = [{
        "id": r["id"],
        "name": r["filename"],
        "size_human": human_readable_size(r["size_bytes"]),
        "uploader": r["uploader"],
        "likes": r["likes"],
        "collections": r["collections"],
        "created_at": r["created_at"],
        "download_url": f"/api/download/{r['id']}"
    } for r in records]
    return jsonify({"success": True, "files": files})

# ==================== API：好友与聊天 ====================
@app.route("/api/friends")
@login_required
def list_friends():
    user_id = session["user_id"]
    db = get_db()
    # 获取好友列表（status='accepted'）
    rows = db.execute("""
        SELECT u.id, u.username
        FROM friends f
        JOIN users u ON (f.user_id = u.id OR f.friend_id = u.id)
        WHERE (f.user_id = ? OR f.friend_id = ?) AND f.status = 'accepted' AND u.id != ?
    """, (user_id, user_id, user_id)).fetchall()
    friends = [{"id": r["id"], "username": r["username"]} for r in rows]
    # 获取待处理的好友请求（别人发给我的）
    pending = db.execute("""
        SELECT f.id, u.id as from_id, u.username
        FROM friends f
        JOIN users u ON f.user_id = u.id
        WHERE f.friend_id = ? AND f.status = 'pending'
    """, (user_id,)).fetchall()
    pending_requests = [{"request_id": r["id"], "from_id": r["from_id"], "username": r["username"]} for r in pending]
    return jsonify({"success": True, "friends": friends, "pending_requests": pending_requests})

@app.route("/api/friends/search", methods=["GET"])
@login_required
def search_users():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify({"success": True, "users": []})
    db = get_db()
    users = db.execute(
        "SELECT id, username FROM users WHERE username LIKE ? AND id != ? LIMIT 10",
        (f"%{query}%", session["user_id"])
    ).fetchall()
    return jsonify({"success": True, "users": [{"id": u["id"], "username": u["username"]} for u in users]})

@app.route("/api/friends/request", methods=["POST"])
@login_required
def send_friend_request_api():
    data = request.get_json()
    to_user_id = data.get("to_user_id")
    if not to_user_id or to_user_id == session["user_id"]:
        return jsonify({"success": False, "error": "无效的用户"}), 400
    if send_friend_request(session["user_id"], int(to_user_id)):
        return jsonify({"success": True, "message": "好友请求已发送"})
    else:
        return jsonify({"success": False, "error": "无法发送请求（已是好友或已有待处理请求）"}), 400

@app.route("/api/friends/accept", methods=["POST"])
@login_required
def accept_friend_request_api():
    data = request.get_json()
    request_id = data.get("request_id")
    if not request_id:
        return jsonify({"success": False, "error": "缺少请求ID"}), 400
    if accept_friend_request(int(request_id), session["user_id"]):
        return jsonify({"success": True, "message": "已添加好友"})
    else:
        return jsonify({"success": False, "error": "无效的请求"}), 400

@app.route("/api/messages", methods=["GET"])
@login_required
def get_messages():
    friend_id = request.args.get("friend_id", type=int)
    if not friend_id:
        return jsonify({"success": False, "error": "缺少好友ID"}), 400
    user_id = session["user_id"]
    db = get_db()
    # 获取与指定好友的聊天记录
    rows = db.execute("""
        SELECT sender_id, receiver_id, content, created_at, is_read
        FROM messages
        WHERE (sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?)
        ORDER BY created_at ASC
    """, (user_id, friend_id, friend_id, user_id)).fetchall()
    messages = [{
        "sender_id": r["sender_id"],
        "receiver_id": r["receiver_id"],
        "content": r["content"],
        "time": r["created_at"],
        "is_read": bool(r["is_read"])
    } for r in rows]
    # 标记已读
    db.execute(
        "UPDATE messages SET is_read = 1 WHERE sender_id = ? AND receiver_id = ? AND is_read = 0",
        (friend_id, user_id)
    )
    db.commit()
    return jsonify({"success": True, "messages": messages})

@app.route("/api/messages/send", methods=["POST"])
@login_required
def send_message():
    data = request.get_json()
    receiver_id = data.get("receiver_id")
    content = data.get("content", "").strip()
    if not receiver_id or not content:
        return jsonify({"success": False, "error": "缺少参数"}), 400
    # 检查是否是好友
    if not are_friends(session["user_id"], int(receiver_id)):
        return jsonify({"success": False, "error": "不是好友关系"}), 403
    db = get_db()
    db.execute(
        "INSERT INTO messages (sender_id, receiver_id, content) VALUES (?, ?, ?)",
        (session["user_id"], receiver_id, content)
    )
    db.commit()
    return jsonify({"success": True, "message": "发送成功"})

# ==================== API：管理员功能（保留原样，新增用户列表显示星币容量）====================
@app.route("/api/admin/users")
@admin_required
def admin_list_users():
    db = get_db()
    users = db.execute(
        "SELECT id, username, password_hash, is_admin, created_at, coins, capacity_mb FROM users ORDER BY id"
    ).fetchall()
    user_list = []
    for u in users:
        used_mb = get_user_capacity(u["id"])
        user_list.append({
            "id": u["id"],
            "username": u["username"],
            "password_hash": u["password_hash"],
            "is_admin": bool(u["is_admin"]),
            "created_at": u["created_at"],
            "coins": u["coins"],
            "capacity_total_mb": u["capacity_mb"],
            "capacity_used_mb": used_mb
        })
    return jsonify({"success": True, "users": user_list})

@app.route("/api/admin/user/<int:user_id>/files")
@admin_required
def admin_list_user_files(user_id):
    db = get_db()
    user = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return jsonify({"success": False, "error": "用户不存在"}), 404
    records = db.execute(
        "SELECT id, filename, size_bytes, is_public, likes, collections, created_at FROM files WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,)
    ).fetchall()
    files = [get_file_info_from_record(r) for r in records]
    return jsonify({"success": True, "files": files})

@app.route("/api/admin/delete_user/<int:user_id>", methods=["DELETE"])
@admin_required
def admin_delete_user(user_id):
    db = get_db()
    user = db.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return jsonify({"success": False, "error": "用户不存在"}), 404
    if user["is_admin"]:
        return jsonify({"success": False, "error": "不能删除管理员账号"}), 403
    # 调用与用户自助注销相同的逻辑（复用）
    # 为避免重复代码，直接调用删除函数？但需要 session 上下文，简单复制逻辑
    # 这里简化：删除文件、记录、目录
    files = db.execute("SELECT file_path FROM files WHERE user_id = ?", (user_id,)).fetchall()
    for f in files:
        path = Path(f["file_path"])
        if path.exists():
            path.unlink()
    db.execute("DELETE FROM files WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM friends WHERE user_id=? OR friend_id=?", (user_id, user_id))
    db.execute("DELETE FROM messages WHERE sender_id=? OR receiver_id=?", (user_id, user_id))
    db.execute("DELETE FROM file_likes WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM file_collections WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM sign_in_log WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM exchange_log WHERE user_id=?", (user_id,))
    user_folder = get_user_folder(user_id)
    if user_folder.exists():
        shutil.rmtree(user_folder)
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    return jsonify({"success": True, "message": "用户已删除"})

@app.route("/api/admin/reset_password/<int:user_id>", methods=["POST"])
@admin_required
def admin_reset_password(user_id):
    data = request.get_json()
    new_password = data.get("new_password", "123456")
    if len(new_password) < 6:
        return jsonify({"success": False, "error": "密码至少6位"}), 400
    db = get_db()
    user = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return jsonify({"success": False, "error": "用户不存在"}), 404
    new_hash = generate_password_hash(new_password)
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
    db.commit()
    return jsonify({"success": True, "message": f"密码已重置为 {new_password}"})

@app.route("/api/admin/adjust_coins/<int:user_id>", methods=["POST"])
@admin_required
def admin_adjust_coins(user_id):
    data = request.get_json()
    delta = data.get("delta", 0)
    reason = data.get("reason", "管理员调整")
    if not isinstance(delta, int) or delta == 0:
        return jsonify({"success": False, "error": "调整量必须为非零整数"}), 400
    db = get_db()
    user = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return jsonify({"success": False, "error": "用户不存在"}), 404
    success = update_user_coins(user_id, delta, reason)
    if not success:
        return jsonify({"success": False, "error": "调整后星币不能为负数"}), 400
    new_balance = get_user_coins(user_id)
    return jsonify({"success": True, "new_balance": new_balance})

# ==================== 错误处理 ====================
@app.errorhandler(403)
def forbidden(e):
    return jsonify({"success": False, "error": "无权限访问"}), 403

@app.errorhandler(404)
def not_found(e):
    return jsonify({"success": False, "error": "资源不存在"}), 404

@app.errorhandler(413)
def too_large(e):
    return jsonify({"success": False, "error": "文件超过大小限制 (256MB)"}), 413

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8000)
