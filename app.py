import os
import sqlite3
import math
import shutil
import mimetypes
import random
import string
import smtplib
from datetime import timedelta, datetime, date
from pathlib import Path
from functools import wraps
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import (
    Flask, render_template, request, jsonify, send_from_directory,
    send_file, abort, session, g, redirect, url_for
)
from werkzeug.security import generate_password_hash, check_password_hash

# ==================== 配置 ====================
app = Flask(__name__)
app.config["SECRET_KEY"] = "change-this-in-production"
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

BASE_UPLOAD_FOLDER = Path("uploads")
BASE_UPLOAD_FOLDER.mkdir(exist_ok=True)
DATABASE = "users.db"

# 邮箱配置（请修改为自己的163邮箱信息）
EMAIL_HOST = "smtp.163.com"
EMAIL_PORT = 465
EMAIL_USER = ""          # 你的163邮箱地址
EMAIL_PASSWORD = ""  # 163邮箱授权码（不是登录密码）
VERIFICATION_CODE_EXPIRE = 300              # 验证码有效期（秒）

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
            # 创建唯一索引（允许 NULL 值重复）
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email IS NOT NULL")
            print("已添加 email 列并创建唯一索引")

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
        # 其他表（文件、星币日志、点赞、收藏、签到、兑换、好友、消息）
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

        # 创建默认管理员
        admin = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()
        if not admin:
            db.execute(
                "INSERT INTO users (username, password_hash, is_admin, coins, capacity_mb, email) VALUES (?, ?, 1, 10000, 10240, ?)",
                ("admin", generate_password_hash("123465"), "admin@163.com")
            )
            db.commit()
            print("管理员账号已创建: admin / 123465")
        else:
            # 确保管理员有容量
            db.execute("UPDATE users SET capacity_mb = 10240 WHERE username='admin' AND capacity_mb < 10240")
            db.commit()
init_db()

# ==================== 辅助函数 ====================
def get_user_folder(user_id: int) -> Path:
    path = BASE_UPLOAD_FOLDER / str(user_id)
    path.mkdir(exist_ok=True)
    return path

def human_readable_size(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"

def safe_filename(original: str) -> str:
    base = os.path.basename(original)
    safe = []
    for ch in base:
        if ch.isalnum() or ch in ".-_ ":
            safe.append(ch)
        else:
            safe.append("_")
    return "".join(safe).strip()

def unique_filename(directory: Path, filename: str) -> str:
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1
    new = filename
    while (directory / new).exists():
        new = f"{stem} ({counter}){suffix}"
        counter += 1
    return new

def get_user_coins(user_id: int) -> int:
    row = get_db().execute("SELECT coins FROM users WHERE id=?", (user_id,)).fetchone()
    return row["coins"] if row else 0

def update_user_coins(user_id: int, delta: int, reason: str) -> bool:
    db = get_db()
    cur = get_user_coins(user_id)
    new = cur + delta
    if new < 0:
        return False
    db.execute("UPDATE users SET coins=? WHERE id=?", (new, user_id))
    db.execute("INSERT INTO coin_logs (user_id, change_amount, balance_after, reason) VALUES (?,?,?,?)",
               (user_id, delta, new, reason))
    db.commit()
    return True

def get_user_capacity(user_id: int) -> int:
    total = get_db().execute("SELECT COALESCE(SUM(size_bytes),0) FROM files WHERE user_id=?", (user_id,)).fetchone()[0]
    return math.ceil(total / (1024*1024))

def check_capacity(user_id: int, new_size: int) -> bool:
    db = get_db()
    user = db.execute("SELECT capacity_mb FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        return False
    used = get_user_capacity(user_id)
    new_mb = math.ceil(new_size / (1024*1024))
    return used + new_mb <= user["capacity_mb"]

def can_sign_in(user_id: int) -> bool:
    today = date.today().isoformat()
    row = get_db().execute("SELECT id FROM sign_in_log WHERE user_id=? AND sign_date=?", (user_id, today)).fetchone()
    return row is None

def do_sign_in(user_id: int) -> int:
    if not can_sign_in(user_id):
        return 0
    gain = 10
    db = get_db()
    today = date.today().isoformat()
    db.execute("INSERT INTO sign_in_log (user_id, sign_date, coins_gained) VALUES (?,?,?)", (user_id, today, gain))
    update_user_coins(user_id, gain, f"签到获得{gain}星币")
    db.commit()
    return gain

def are_friends(uid1: int, uid2: int) -> bool:
    row = get_db().execute(
        "SELECT id FROM friends WHERE ((user_id=? AND friend_id=?) OR (user_id=? AND friend_id=?)) AND status='accepted'",
        (uid1, uid2, uid2, uid1)).fetchone()
    return row is not None

def send_friend_request(from_id: int, to_id: int) -> bool:
    if from_id == to_id:
        return False
    db = get_db()
    existing = db.execute(
        "SELECT id FROM friends WHERE (user_id=? AND friend_id=?) OR (user_id=? AND friend_id=?)",
        (from_id, to_id, to_id, from_id)).fetchone()
    if existing:
        return False
    db.execute("INSERT INTO friends (user_id, friend_id, status) VALUES (?,?,'pending')", (from_id, to_id))
    db.commit()
    return True

def accept_friend_request(req_id: int, user_id: int) -> bool:
    db = get_db()
    req = db.execute("SELECT id, user_id, friend_id FROM friends WHERE id=? AND status='pending'", (req_id,)).fetchone()
    if not req or req["friend_id"] != user_id:
        return False
    db.execute("UPDATE friends SET status='accepted' WHERE id=?", (req_id,))
    db.commit()
    return True

def get_file_info_from_record(record):
    return {
        "id": record["id"],
        "name": record["filename"],
        "size": record["size_bytes"],
        "size_human": human_readable_size(record["size_bytes"]),
        "is_public": bool(record["is_public"]),
        "likes": record["likes"],
        "collections": record["collections"],
        "created_at": record["created_at"],
        "download_url": url_for("numfile", num=record["id"])
    }

# 邮件发送函数
def send_email(to_email, subject, body):
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        server = smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT)
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_USER, to_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"邮件发送失败: {e}")
        return False

def generate_verification_code(length=6):
    return ''.join(random.choices(string.digits, k=length))

# ==================== 权限装饰器 ====================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login_page"))
        db = get_db()
        user = db.execute("SELECT is_admin FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if not user or not user["is_admin"]:
            abort(403)
        return f(*args, **kwargs)
    return decorated

# ==================== 页面路由 ====================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login")
def login_page():
    if "user_id" in session:
        return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/logout")
def logout_page():
    session.clear()
    return redirect(url_for("index"))

@app.route("/user_center")
@login_required
def user_center():
    return render_template("user_center.html")

@app.route("/community")
def community_page():
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

@app.route("/user/<string:name>")
def user_profile(name):
    db = get_db()
    user = db.execute("SELECT id, username, created_at FROM users WHERE username=?", (name,)).fetchone()
    if not user:
        abort(404)
    files = db.execute(
        "SELECT id, filename, size_bytes, likes, collections, created_at FROM files WHERE user_id=? AND is_public=1 ORDER BY created_at DESC",
        (user["id"],)).fetchall()
    file_list = [{
        "id": f["id"],
        "name": f["filename"],
        "size_human": human_readable_size(f["size_bytes"]),
        "likes": f["likes"],
        "collections": f["collections"],
        "created_at": f["created_at"],
        "download_url": url_for("numfile", num=f["id"])
    } for f in files]
    return render_template("user_profile.html", user=user, files=file_list)

@app.route("/pathfile/<string:path>")
def pathfile(path):
    if "/" not in path:
        abort(404)
    parts = path.split("/", 1)
    identifier = parts[0]
    filename = parts[1]
    db = get_db()
    if identifier.isdigit():
        user = db.execute("SELECT id FROM users WHERE id=?", (int(identifier),)).fetchone()
    else:
        user = db.execute("SELECT id FROM users WHERE username=?", (identifier,)).fetchone()
    if not user:
        abort(404)
    record = db.execute(
        "SELECT id, user_id, filename, file_path, is_public FROM files WHERE user_id=? AND filename=?",
        (user["id"], filename)).fetchone()
    if not record:
        abort(404)
    if record["is_public"] == 0:
        if "user_id" not in session or session["user_id"] != record["user_id"]:
            abort(404)
    download = request.args.get("download", "0")
    path_obj = Path(record["file_path"])
    if not path_obj.exists():
        abort(404)
    mime, _ = mimetypes.guess_type(filename)
    if download == "1":
        return send_from_directory(path_obj.parent, path_obj.name, as_attachment=True, download_name=filename)
    else:
        if mime and mime.startswith("image/"):
            return send_file(path_obj, mimetype=mime)
        else:
            return send_from_directory(path_obj.parent, path_obj.name, as_attachment=False, download_name=filename)

@app.route("/numfile/<int:num>")
def numfile(num):
    db = get_db()
    record = db.execute(
        "SELECT user_id, filename, file_path, is_public FROM files WHERE id=?",
        (num,)).fetchone()
    if not record:
        abort(404)
    if record["is_public"] == 0:
        if "user_id" not in session or session["user_id"] != record["user_id"]:
            abort(404)
    download = request.args.get("download", "0")
    path_obj = Path(record["file_path"])
    if not path_obj.exists():
        abort(404)
    mime, _ = mimetypes.guess_type(record["filename"])
    if download == "1":
        return send_from_directory(path_obj.parent, path_obj.name, as_attachment=True, download_name=record["filename"])
    else:
        if mime and mime.startswith("image/"):
            return send_file(path_obj, mimetype=mime)
        else:
            return send_from_directory(path_obj.parent, path_obj.name, as_attachment=False, download_name=record["filename"])

# ==================== API：认证 ====================
@app.route("/api/send_code", methods=["POST"])
def send_verification_code():
    data = request.get_json()
    email = data.get("email", "").strip().lower()
    if not email:
        return jsonify({"success": False, "error": "邮箱地址不能为空"}), 400
    if "@" not in email or "." not in email:
        return jsonify({"success": False, "error": "邮箱格式不正确"}), 400
    code = generate_verification_code()
    db = get_db()
    db.execute(
        "INSERT INTO email_verification_codes (email, code, used) VALUES (?, ?, 0)",
        (email, code)
    )
    db.commit()
    subject = "星云盘 - 邮箱验证码"
    body = f"您的验证码是：{code}，有效期为5分钟。请勿泄露给他人。"
    if send_email(email, subject, body):
        return jsonify({"success": True, "message": "验证码已发送，请查收邮件"})
    else:
        return jsonify({"success": False, "error": "邮件发送失败，请稍后重试"}), 500

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json()
    email = data.get("email", "").strip().lower()
    code = data.get("code", "").strip()
    password = data.get("password", "")
    username = data.get("username", "").strip()
    if not email or not code or not password:
        return jsonify({"success": False, "error": "邮箱、验证码和密码不能为空"}), 400
    if len(password) < 6:
        return jsonify({"success": False, "error": "密码至少6位"}), 400
    db = get_db()
    record = db.execute(
        "SELECT id, code, created_at FROM email_verification_codes WHERE email = ? AND used = 0 ORDER BY created_at DESC LIMIT 1",
        (email,)
    ).fetchone()
    if not record:
        return jsonify({"success": False, "error": "请先获取验证码"}), 400
    created_at = datetime.fromisoformat(record["created_at"])
    if (datetime.now() - created_at).seconds > VERIFICATION_CODE_EXPIRE:
        return jsonify({"success": False, "error": "验证码已过期，请重新获取"}), 400
    if record["code"] != code:
        return jsonify({"success": False, "error": "验证码错误"}), 400
    db.execute("UPDATE email_verification_codes SET used = 1 WHERE id = ?", (record["id"],))
    db.commit()
    if db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
        return jsonify({"success": False, "error": "该邮箱已被注册"}), 400
    if not username:
        username = email.split('@')[0]
        original = username
        counter = 1
        while db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone() or username == "admin":
            username = f"{original}{counter}"
            counter += 1
    else:
        if db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            return jsonify({"success": False, "error": "用户名已存在"}), 400
    if username == "admin":
        return jsonify({"success": False, "error": "此用户名不可用"}), 400
    password_hash = generate_password_hash(password)
    try:
        db.execute(
            "INSERT INTO users (username, password_hash, email, is_admin, coins, capacity_mb) VALUES (?, ?, ?, 0, 100, 100)",
            (username, password_hash, email)
        )
        db.commit()
        user_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        get_user_folder(user_id)
        db.execute(
            "INSERT INTO coin_logs (user_id, change_amount, balance_after, reason) VALUES (?, 100, 100, '注册赠送100星币')",
            (user_id,)
        )
        db.commit()
        return jsonify({"success": True, "message": "注册成功"})
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "error": "用户名或邮箱已存在"}), 400

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    account = data.get("account", "").strip()
    password = data.get("password", "")
    code = data.get("code", "")
    use_password = bool(password)
    use_code = bool(code)
    if not account:
        return jsonify({"success": False, "error": "请输入用户名/邮箱"}), 400
    if not use_password and not use_code:
        return jsonify({"success": False, "error": "请输入密码或验证码"}), 400
    if use_password and use_code:
        return jsonify({"success": False, "error": "请选择一种登录方式（密码或验证码）"}), 400

    db = get_db()
    # 查找用户：可通过邮箱或用户名
    user = db.execute(
        "SELECT id, username, password_hash, is_admin, email FROM users WHERE username = ? OR email = ?",
        (account, account)
    ).fetchone()
    if not user:
        return jsonify({"success": False, "error": "用户不存在"}), 401

    if use_password:
        if not check_password_hash(user["password_hash"], password):
            return jsonify({"success": False, "error": "密码错误"}), 401
    else:  # 使用验证码登录
        record = db.execute(
            "SELECT id, code, created_at FROM email_verification_codes WHERE email = ? AND used = 0 ORDER BY created_at DESC LIMIT 1",
            (user["email"],)
        ).fetchone()
        if not record:
            return jsonify({"success": False, "error": "请先获取验证码"}), 400
        created_at = datetime.fromisoformat(record["created_at"])
        if (datetime.now() - created_at).seconds > VERIFICATION_CODE_EXPIRE:
            return jsonify({"success": False, "error": "验证码已过期，请重新获取"}), 400
        if record["code"] != code:
            return jsonify({"success": False, "error": "验证码错误"}), 400
        db.execute("UPDATE email_verification_codes SET used = 1 WHERE id = ?", (record["id"],))
        db.commit()

    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return jsonify({"success": True, "user": {"id": user["id"], "username": user["username"], "is_admin": bool(user["is_admin"])}})

@app.route("/api/check_auth")
def check_auth():
    if "user_id" in session:
        db = get_db()
        user = db.execute("SELECT id, username, is_admin FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if user:
            return jsonify({"success": True, "user": {"id": user["id"], "username": user["username"], "is_admin": bool(user["is_admin"])}})
        session.clear()
    return jsonify({"success": False}), 401

@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"success": True})

# ==================== API：用户信息 ====================
@app.route("/api/user/info")
@login_required
def user_info():
    uid = session["user_id"]
    db = get_db()
    user = db.execute("SELECT id, username, coins, capacity_mb, created_at FROM users WHERE id=?", (uid,)).fetchone()
    used = get_user_capacity(uid)
    return jsonify({"success": True, "user": {
        "id": user["id"], "username": user["username"], "coins": user["coins"],
        "capacity_total_mb": user["capacity_mb"], "capacity_used_mb": used, "joined_at": user["created_at"]
    }})

@app.route("/api/user/change_password", methods=["POST"])
@login_required
def change_password():
    data = request.get_json()
    old = data.get("old_password", "")
    new = data.get("new_password", "")
    if not old or not new:
        return jsonify({"success": False, "error": "请填写完整"}), 400
    if len(new) < 6:
        return jsonify({"success": False, "error": "新密码至少6位"}), 400
    db = get_db()
    user = db.execute("SELECT password_hash FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if not check_password_hash(user["password_hash"], old):
        return jsonify({"success": False, "error": "原密码错误"}), 401
    db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), session["user_id"]))
    db.commit()
    return jsonify({"success": True, "message": "密码修改成功"})

@app.route("/api/user/delete_account", methods=["POST"])
@login_required
def delete_account():
    uid = session["user_id"]
    db = get_db()
    if db.execute("SELECT is_admin FROM users WHERE id=?", (uid,)).fetchone()["is_admin"]:
        return jsonify({"success": False, "error": "管理员不可注销"}), 403
    files = db.execute("SELECT file_path FROM files WHERE user_id=?", (uid,)).fetchall()
    for f in files:
        Path(f["file_path"]).unlink(missing_ok=True)
    db.execute("DELETE FROM files WHERE user_id=?", (uid,))
    db.execute("DELETE FROM friends WHERE user_id=? OR friend_id=?", (uid, uid))
    db.execute("DELETE FROM messages WHERE sender_id=? OR receiver_id=?", (uid, uid))
    db.execute("DELETE FROM file_likes WHERE user_id=?", (uid,))
    db.execute("DELETE FROM file_collections WHERE user_id=?", (uid,))
    db.execute("DELETE FROM sign_in_log WHERE user_id=?", (uid,))
    db.execute("DELETE FROM exchange_log WHERE user_id=?", (uid,))
    shutil.rmtree(get_user_folder(uid), ignore_errors=True)
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.commit()
    session.clear()
    return jsonify({"success": True, "message": "账号已注销"})

# ==================== API：星币与签到 ====================
@app.route("/api/user/coins")
@login_required
def coins_api():
    return jsonify({"success": True, "coins": get_user_coins(session["user_id"])})

@app.route("/api/user/coin_logs")
@login_required
def coin_logs():
    logs = get_db().execute(
        "SELECT change_amount, balance_after, reason, created_at FROM coin_logs WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
        (session["user_id"],)).fetchall()
    return jsonify({"success": True, "logs": [{"change": l["change_amount"], "balance": l["balance_after"], "reason": l["reason"], "time": l["created_at"]} for l in logs]})

@app.route("/api/user/sign_in", methods=["POST"])
@login_required
def sign_in_api():
    uid = session["user_id"]
    if not can_sign_in(uid):
        return jsonify({"success": False, "error": "今日已签到"}), 400
    gained = do_sign_in(uid)
    return jsonify({"success": True, "gained": gained, "new_balance": get_user_coins(uid)})

@app.route("/api/user/exchange_capacity", methods=["POST"])
@login_required
def exchange_capacity():
    data = request.get_json()
    coins = data.get("coins", 0)
    if not isinstance(coins, int) or coins <= 0:
        return jsonify({"success": False, "error": "请输入正整数的星币数量"}), 400
    uid = session["user_id"]
    cur_coins = get_user_coins(uid)
    if cur_coins < coins:
        return jsonify({"success": False, "error": "星币不足"}), 400
    mb = max(1, coins // 2)
    db = get_db()
    if not update_user_coins(uid, -coins, f"兑换{mb}MB容量消耗{coins}星币"):
        return jsonify({"success": False, "error": "扣币失败"}), 500
    db.execute("UPDATE users SET capacity_mb = capacity_mb + ? WHERE id=?", (mb, uid))
    db.execute("INSERT INTO exchange_log (user_id, coins_spent, mb_gained, total_capacity) VALUES (?,?,?,(SELECT capacity_mb FROM users WHERE id=?))",
               (uid, coins, mb, uid))
    db.commit()
    return jsonify({"success": True, "mb_gained": mb, "new_capacity_mb": db.execute("SELECT capacity_mb FROM users WHERE id=?", (uid,)).fetchone()[0], "new_balance": get_user_coins(uid)})

# ==================== API：文件操作 ====================
@app.route("/api/files")
@login_required
def list_my_files():
    uid = session["user_id"]
    records = get_db().execute(
        "SELECT id, filename, size_bytes, is_public, likes, collections, created_at FROM files WHERE user_id=? ORDER BY created_at DESC",
        (uid,)).fetchall()
    return jsonify({"success": True, "files": [get_file_info_from_record(r) for r in records]})

@app.route("/api/upload", methods=["POST"])
@login_required
def upload_file():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "没有文件部分"}), 400
    f = request.files["file"]
    if f.filename == "":
        return jsonify({"success": False, "error": "未选择文件"}), 400
    is_public = request.form.get("is_public", "0") == "1"
    safe_name = safe_filename(f.filename)
    if not safe_name:
        return jsonify({"success": False, "error": "无效的文件名"}), 400
    uid = session["user_id"]
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(0)
    if not check_capacity(uid, size):
        return jsonify({"success": False, "error": "存储容量不足，请兑换容量或删除旧文件"}), 403
    user_dir = get_user_folder(uid)
    final_name = unique_filename(user_dir, safe_name)
    try:
        f.save(user_dir / final_name)
        db = get_db()
        db.execute(
            "INSERT INTO files (user_id, filename, file_path, size_bytes, is_public) VALUES (?,?,?,?,?)",
            (uid, final_name, str(user_dir / final_name), size, 1 if is_public else 0)
        )
        fid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()
        reward = max(1, size // (1024*1024))
        update_user_coins(uid, reward, f"上传文件 {final_name} 获得 {reward} 星币")
        return jsonify({"success": True, "message": f"上传成功，获得{reward}星币", "file": get_file_info_from_record(db.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone())})
    except Exception as e:
        return jsonify({"success": False, "error": f"保存失败: {str(e)}"}), 500

@app.route("/api/delete/<int:file_id>", methods=["DELETE"])
@login_required
def delete_file_api(file_id):
    db = get_db()
    rec = db.execute("SELECT user_id, file_path FROM files WHERE id=?", (file_id,)).fetchone()
    if not rec or rec["user_id"] != session["user_id"]:
        return jsonify({"success": False, "error": "无权限或文件不存在"}), 404
    Path(rec["file_path"]).unlink(missing_ok=True)
    db.execute("DELETE FROM files WHERE id=?", (file_id,))
    db.execute("DELETE FROM file_likes WHERE file_id=?", (file_id,))
    db.execute("DELETE FROM file_collections WHERE file_id=?", (file_id,))
    db.commit()
    return jsonify({"success": True, "message": "删除成功"})

@app.route("/api/file/toggle_public/<int:file_id>", methods=["POST"])
@login_required
def toggle_public(file_id):
    db = get_db()
    rec = db.execute("SELECT user_id, is_public FROM files WHERE id=?", (file_id,)).fetchone()
    if not rec or rec["user_id"] != session["user_id"]:
        return jsonify({"success": False, "error": "无权限"}), 403
    new = 1 - rec["is_public"]
    db.execute("UPDATE files SET is_public=? WHERE id=?", (new, file_id))
    db.commit()
    return jsonify({"success": True, "is_public": bool(new)})

# ==================== API：社区 ====================
@app.route("/api/community/files")
def community_files():
    recs = get_db().execute("""
        SELECT f.id, f.filename, f.size_bytes, f.likes, f.collections, f.created_at, u.username as uploader
        FROM files f JOIN users u ON f.user_id = u.id
        WHERE f.is_public=1 ORDER BY f.likes DESC, f.created_at DESC LIMIT 100
    """).fetchall()
    return jsonify({"success": True, "files": [{
        "id": r["id"], "name": r["filename"], "size_human": human_readable_size(r["size_bytes"]),
        "uploader": r["uploader"], "likes": r["likes"], "collections": r["collections"],
        "created_at": r["created_at"], "download_url": url_for("numfile", num=r["id"])
    } for r in recs]})

@app.route("/api/community/like/<int:file_id>", methods=["POST"])
@login_required
def like_file(file_id):
    uid = session["user_id"]
    db = get_db()
    file = db.execute("SELECT id, user_id, is_public FROM files WHERE id=?", (file_id,)).fetchone()
    if not file or not file["is_public"]:
        return jsonify({"success": False, "error": "文件不存在或非公开"}), 404
    if db.execute("SELECT id FROM file_likes WHERE user_id=? AND file_id=?", (uid, file_id)).fetchone():
        return jsonify({"success": False, "error": "你已经点过赞了"}), 400
    db.execute("INSERT INTO file_likes (user_id, file_id) VALUES (?,?)", (uid, file_id))
    db.execute("UPDATE files SET likes = likes + 1 WHERE id=?", (file_id,))
    update_user_coins(file["user_id"], 1, f"文件 {file_id} 获得一个点赞")
    db.commit()
    return jsonify({"success": True, "message": "点赞成功"})

@app.route("/api/community/collect/<int:file_id>", methods=["POST"])
@login_required
def collect_file(file_id):
    uid = session["user_id"]
    db = get_db()
    file = db.execute("SELECT id, user_id, is_public FROM files WHERE id=?", (file_id,)).fetchone()
    if not file or not file["is_public"]:
        return jsonify({"success": False, "error": "文件不存在或非公开"}), 404
    if db.execute("SELECT id FROM file_collections WHERE user_id=? AND file_id=?", (uid, file_id)).fetchone():
        return jsonify({"success": False, "error": "你已经收藏过了"}), 400
    db.execute("INSERT INTO file_collections (user_id, file_id) VALUES (?,?)", (uid, file_id))
    db.execute("UPDATE files SET collections = collections + 1 WHERE id=?", (file_id,))
    update_user_coins(file["user_id"], 2, f"文件 {file_id} 获得一个收藏")
    db.commit()
    return jsonify({"success": True, "message": "收藏成功"})

@app.route("/api/user/my_collections")
@login_required
def my_collections():
    uid = session["user_id"]
    recs = get_db().execute("""
        SELECT f.id, f.filename, f.size_bytes, f.likes, f.collections, f.created_at, u.username as uploader
        FROM files f JOIN file_collections c ON f.id=c.file_id JOIN users u ON f.user_id=u.id
        WHERE c.user_id=? ORDER BY c.created_at DESC
    """, (uid,)).fetchall()
    return jsonify({"success": True, "files": [{
        "id": r["id"], "name": r["filename"], "size_human": human_readable_size(r["size_bytes"]),
        "uploader": r["uploader"], "likes": r["likes"], "collections": r["collections"],
        "created_at": r["created_at"], "download_url": url_for("numfile", num=r["id"])
    } for r in recs]})

# ==================== API：好友与聊天 ====================
@app.route("/api/friends")
@login_required
def list_friends():
    uid = session["user_id"]
    db = get_db()
    friends = db.execute(
        "SELECT u.id, u.username FROM friends f JOIN users u ON (f.user_id=u.id OR f.friend_id=u.id) WHERE (f.user_id=? OR f.friend_id=?) AND f.status='accepted' AND u.id != ?",
        (uid, uid, uid)).fetchall()
    pending = db.execute(
        "SELECT f.id, u.id as from_id, u.username FROM friends f JOIN users u ON f.user_id=u.id WHERE f.friend_id=? AND f.status='pending'",
        (uid,)).fetchall()
    return jsonify({"success": True, "friends": [{"id": f["id"], "username": f["username"]} for f in friends],
                    "pending_requests": [{"request_id": p["id"], "from_id": p["from_id"], "username": p["username"]} for p in pending]})

@app.route("/api/friends/search", methods=["GET"])
@login_required
def search_users():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"success": True, "users": []})
    db = get_db()
    users = db.execute(
        "SELECT id, username FROM users WHERE username LIKE ? AND id != ? AND username != 'admin' LIMIT 10",
        (f"%{q}%", session["user_id"])).fetchall()
    return jsonify({"success": True, "users": [{"id": u["id"], "username": u["username"]} for u in users]})

@app.route("/api/friends/request", methods=["POST"])
@login_required
def send_friend_request_api():
    data = request.get_json()
    to_id = data.get("to_user_id")
    if not to_id or to_id == session["user_id"]:
        return jsonify({"success": False, "error": "无效的用户"}), 400
    db = get_db()
    target = db.execute("SELECT is_admin FROM users WHERE id=?", (to_id,)).fetchone()
    if target and target["is_admin"]:
        return jsonify({"success": False, "error": "不能添加管理员为好友"}), 400
    if send_friend_request(session["user_id"], int(to_id)):
        return jsonify({"success": True, "message": "好友请求已发送"})
    return jsonify({"success": False, "error": "无法发送请求（已是好友或已有待处理请求）"}), 400

@app.route("/api/friends/accept", methods=["POST"])
@login_required
def accept_friend_request_api():
    data = request.get_json()
    req_id = data.get("request_id")
    if not req_id:
        return jsonify({"success": False, "error": "缺少请求ID"}), 400
    if accept_friend_request(int(req_id), session["user_id"]):
        return jsonify({"success": True, "message": "已添加好友"})
    return jsonify({"success": False, "error": "无效的请求"}), 400

@app.route("/api/messages", methods=["GET"])
@login_required
def get_messages():
    friend_id = request.args.get("friend_id", type=int)
    if not friend_id:
        return jsonify({"success": False, "error": "缺少好友ID"}), 400
    uid = session["user_id"]
    db = get_db()
    rows = db.execute(
        "SELECT sender_id, receiver_id, content, created_at, is_read FROM messages WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?) ORDER BY created_at ASC",
        (uid, friend_id, friend_id, uid)).fetchall()
    db.execute("UPDATE messages SET is_read=1 WHERE sender_id=? AND receiver_id=? AND is_read=0", (friend_id, uid))
    db.commit()
    return jsonify({"success": True, "messages": [{
        "sender_id": r["sender_id"], "receiver_id": r["receiver_id"], "content": r["content"],
        "time": r["created_at"], "is_read": bool(r["is_read"])
    } for r in rows]})

@app.route("/api/messages/send", methods=["POST"])
@login_required
def send_message():
    data = request.get_json()
    to_id = data.get("receiver_id")
    content = data.get("content", "").strip()
    if not to_id or not content:
        return jsonify({"success": False, "error": "缺少参数"}), 400
    if not are_friends(session["user_id"], int(to_id)):
        return jsonify({"success": False, "error": "不是好友关系"}), 403
    get_db().execute("INSERT INTO messages (sender_id, receiver_id, content) VALUES (?,?,?)", (session["user_id"], to_id, content))
    get_db().commit()
    return jsonify({"success": True, "message": "发送成功"})

# ==================== API：管理员 ====================
@app.route("/api/admin/users")
@admin_required
def admin_users():
    db = get_db()
    users = db.execute("SELECT id, username, password_hash, is_admin, created_at, coins, capacity_mb FROM users ORDER BY id").fetchall()
    return jsonify({"success": True, "users": [{
        "id": u["id"], "username": u["username"], "password_hash": u["password_hash"],
        "is_admin": bool(u["is_admin"]), "created_at": u["created_at"], "coins": u["coins"],
        "capacity_total_mb": u["capacity_mb"], "capacity_used_mb": get_user_capacity(u["id"])
    } for u in users]})

@app.route("/api/admin/user/<int:user_id>/files")
@admin_required
def admin_user_files(user_id):
    recs = get_db().execute(
        "SELECT id, filename, size_bytes, is_public, likes, collections, created_at FROM files WHERE user_id=? ORDER BY created_at DESC",
        (user_id,)).fetchall()
    return jsonify({"success": True, "files": [get_file_info_from_record(r) for r in recs]})

@app.route("/api/admin/delete_user/<int:user_id>", methods=["DELETE"])
@admin_required
def admin_delete_user(user_id):
    db = get_db()
    user = db.execute("SELECT is_admin FROM users WHERE id=?", (user_id,)).fetchone()
    if not user or user["is_admin"]:
        return jsonify({"success": False, "error": "不能删除管理员账号"}), 403
    files = db.execute("SELECT file_path FROM files WHERE user_id=?", (user_id,)).fetchall()
    for f in files:
        Path(f["file_path"]).unlink(missing_ok=True)
    db.execute("DELETE FROM files WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM friends WHERE user_id=? OR friend_id=?", (user_id, user_id))
    db.execute("DELETE FROM messages WHERE sender_id=? OR receiver_id=?", (user_id, user_id))
    db.execute("DELETE FROM file_likes WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM file_collections WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM sign_in_log WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM exchange_log WHERE user_id=?", (user_id,))
    shutil.rmtree(get_user_folder(user_id), ignore_errors=True)
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    return jsonify({"success": True, "message": "用户已删除"})

@app.route("/api/admin/reset_password/<int:user_id>", methods=["POST"])
@admin_required
def admin_reset_password(user_id):
    data = request.get_json()
    new_pwd = data.get("new_password", "123456")
    if len(new_pwd) < 6:
        return jsonify({"success": False, "error": "密码至少6位"}), 400
    get_db().execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_pwd), user_id))
    get_db().commit()
    return jsonify({"success": True, "message": f"密码已重置为 {new_pwd}"})

@app.route("/api/admin/adjust_coins/<int:user_id>", methods=["POST"])
@admin_required
def admin_adjust_coins(user_id):
    data = request.get_json()
    delta = data.get("delta", 0)
    reason = data.get("reason", "管理员调整")
    if not isinstance(delta, int) or delta == 0:
        return jsonify({"success": False, "error": "调整量必须为非零整数"}), 400
    if not update_user_coins(user_id, delta, reason):
        return jsonify({"success": False, "error": "调整后星币不能为负数"}), 400
    return jsonify({"success": True, "new_balance": get_user_coins(user_id)})

# ==================== 错误处理 ====================
@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(403)
def forbidden(e):
    return render_template("404.html"), 403

@app.errorhandler(413)
def too_large(e):
    return jsonify({"success": False, "error": "文件超过大小限制 (256MB)"}), 413

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8000)
