# app/auth.py
from flask import Blueprint, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
from config import VERIFICATION_CODE_EXPIRE
from app.models import get_db, get_user_folder, update_user_coins
from app.email_utils import send_email, generate_verification_code

auth_bp = Blueprint('auth', __name__)

# ==================== 发送验证码（使用 UTC 时间存储） ====================
@auth_bp.route('/send_code', methods=['POST'])
def send_verification_code():
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    if not email:
        return jsonify({'success': False, 'error': '邮箱地址不能为空'}), 400
    if '@' not in email or '.' not in email:
        return jsonify({'success': False, 'error': '邮箱格式不正确'}), 400

    code = generate_verification_code()
    db = get_db()
    # 使用 UTC 时间存储
    now_utc = datetime.now(timezone.utc).isoformat()
    db.execute(
        'INSERT INTO email_verification_codes (email, code, used, created_at) VALUES (?, ?, 0, ?)',
        (email, code, now_utc)
    )
    db.commit()

    subject = '星云盘 - 邮箱验证码'
    body = f'您的验证码是：{code}，有效期为5分钟。请勿泄露给他人。'
    if send_email(email, subject, body):
        return jsonify({'success': True, 'message': '验证码已发送，请查收邮件'})
    else:
        return jsonify({'success': False, 'error': '邮件发送失败，请稍后重试'}), 500

# ==================== 注册（使用 UTC 比较） ====================
@auth_bp.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    code = data.get('code', '').strip()
    password = data.get('password', '')
    username = data.get('username', '').strip()

    if not email or not code or not password:
        return jsonify({'success': False, 'error': '邮箱、验证码和密码不能为空'}), 400
    if len(password) < 6:
        return jsonify({'success': False, 'error': '密码至少6位'}), 400

    db = get_db()

    # 验证验证码
    record = db.execute(
        'SELECT id, code, created_at FROM email_verification_codes WHERE email = ? AND used = 0 ORDER BY created_at DESC LIMIT 1',
        (email,)
    ).fetchone()
    if not record:
        return jsonify({'success': False, 'error': '请先获取验证码'}), 400

    # 解析时间（兼容新旧两种格式）
    created_at_str = record['created_at']
    # 如果是 SQLite 默认格式 'YYYY-MM-DD HH:MM:SS'，则转为 UTC 时间
    if 'T' not in created_at_str:
        db_time = datetime.strptime(created_at_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    else:
        # ISO 格式
        if created_at_str.endswith('Z'):
            created_at_str = created_at_str.replace('Z', '+00:00')
        db_time = datetime.fromisoformat(created_at_str)

    now_utc = datetime.now(timezone.utc)
    if (now_utc - db_time).total_seconds() > VERIFICATION_CODE_EXPIRE:
        return jsonify({'success': False, 'error': '验证码已过期，请重新获取'}), 400
    if record['code'] != code:
        return jsonify({'success': False, 'error': '验证码错误'}), 400

    db.execute('UPDATE email_verification_codes SET used = 1 WHERE id = ?', (record['id'],))
    db.commit()

    # 检查邮箱是否已被注册
    if db.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone():
        return jsonify({'success': False, 'error': '该邮箱已被注册'}), 400

    # 处理用户名
    if not username:
        username = email.split('@')[0]
        original = username
        counter = 1
        while db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone() or username == 'admin':
            username = f'{original}{counter}'
            counter += 1
    else:
        if db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone():
            return jsonify({'success': False, 'error': '用户名已存在'}), 400
    if username == 'admin':
        return jsonify({'success': False, 'error': '此用户名不可用'}), 400

    # 创建用户
    password_hash = generate_password_hash(password)
    try:
        db.execute(
            'INSERT INTO users (username, password_hash, email, is_admin, coins, capacity_mb) VALUES (?, ?, ?, 0, 100, 100)',
            (username, password_hash, email)
        )
        db.commit()
        user_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        get_user_folder(user_id)
        db.execute(
            'INSERT INTO coin_logs (user_id, change_amount, balance_after, reason) VALUES (?, 100, 100, "注册赠送100星币")',
            (user_id,)
        )
        db.commit()
        return jsonify({'success': True, 'message': '注册成功'})
    except Exception as e:
        return jsonify({'success': False, 'error': '注册失败，请稍后重试'}), 500

# ==================== 登录（验证码登录部分使用 UTC 比较） ====================
@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    account = data.get('account', '').strip()
    password = data.get('password', '')
    code = data.get('code', '')

    use_password = bool(password)
    use_code = bool(code)

    if not account:
        return jsonify({'success': False, 'error': '请输入用户名/邮箱'}), 400
    if not use_password and not use_code:
        return jsonify({'success': False, 'error': '请输入密码或验证码'}), 400
    if use_password and use_code:
        return jsonify({'success': False, 'error': '请选择一种登录方式（密码或验证码）'}), 400

    db = get_db()
    user = db.execute(
        'SELECT id, username, password_hash, is_admin, email FROM users WHERE username = ? OR email = ?',
        (account, account)
    ).fetchone()
    if not user:
        return jsonify({'success': False, 'error': '用户不存在'}), 401

    if use_password:
        if not check_password_hash(user['password_hash'], password):
            return jsonify({'success': False, 'error': '密码错误'}), 401
    else:
        # 验证码登录
        record = db.execute(
            'SELECT id, code, created_at FROM email_verification_codes WHERE email = ? AND used = 0 ORDER BY created_at DESC LIMIT 1',
            (user['email'],)
        ).fetchone()
        if not record:
            return jsonify({'success': False, 'error': '请先获取验证码'}), 400

        # 解析时间（兼容新旧格式）
        created_at_str = record['created_at']
        if 'T' not in created_at_str:
            db_time = datetime.strptime(created_at_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        else:
            if created_at_str.endswith('Z'):
                created_at_str = created_at_str.replace('Z', '+00:00')
            db_time = datetime.fromisoformat(created_at_str)

        now_utc = datetime.now(timezone.utc)
        if (now_utc - db_time).total_seconds() > VERIFICATION_CODE_EXPIRE:
            return jsonify({'success': False, 'error': '验证码已过期，请重新获取'}), 400
        if record['code'] != code:
            return jsonify({'success': False, 'error': '验证码错误'}), 400

        db.execute('UPDATE email_verification_codes SET used = 1 WHERE id = ?', (record['id'],))
        db.commit()

    session.permanent = True
    session['user_id'] = user['id']
    session['username'] = user['username']
    return jsonify({
        'success': True,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'is_admin': bool(user['is_admin'])
        }
    })

# ==================== 检查登录状态 ====================
@auth_bp.route('/check_auth', methods=['GET'])
def check_auth():
    if 'user_id' in session:
        db = get_db()
        user = db.execute('SELECT id, username, is_admin FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        if user:
            return jsonify({
                'success': True,
                'user': {
                    'id': user['id'],
                    'username': user['username'],
                    'is_admin': bool(user['is_admin'])
                }
            })
        session.clear()
    return jsonify({'success': False}), 401

# ==================== 登出 ====================
@auth_bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})