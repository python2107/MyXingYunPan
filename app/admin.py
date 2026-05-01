# app/admin.py
from functools import wraps
from pathlib import Path
import shutil
from flask import Blueprint, request, jsonify, session
from werkzeug.security import generate_password_hash
from app.models import get_db, get_user_folder, get_user_capacity, update_user_coins
from app.user import get_file_info_from_record  # 复用文件信息转换函数

admin_bp = Blueprint('admin', __name__)

# ==================== 管理员权限装饰器 ====================
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'success': False, 'error': '未登录'}), 401
        db = get_db()
        user = db.execute('SELECT is_admin FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        if not user or not user['is_admin']:
            return jsonify({'success': False, 'error': '无权限'}), 403
        return f(*args, **kwargs)
    return decorated

# ==================== 获取所有用户列表 ====================
@admin_bp.route('/users')
@admin_required
def admin_users():
    db = get_db()
    users = db.execute("""
        SELECT id, username, password_hash, is_admin, created_at, coins, capacity_mb
        FROM users
        ORDER BY id
    """).fetchall()
    user_list = []
    for u in users:
        used_mb = get_user_capacity(u['id'])
        user_list.append({
            'id': u['id'],
            'username': u['username'],
            'password_hash': u['password_hash'],
            'is_admin': bool(u['is_admin']),
            'created_at': u['created_at'],
            'coins': u['coins'],
            'capacity_total_mb': u['capacity_mb'],
            'capacity_used_mb': used_mb
        })
    return jsonify({'success': True, 'users': user_list})

# ==================== 查看指定用户的文件列表 ====================
@admin_bp.route('/user/<int:user_id>/files')
@admin_required
def admin_user_files(user_id):
    db = get_db()
    user = db.execute('SELECT id FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        return jsonify({'success': False, 'error': '用户不存在'}), 404
    records = db.execute("""
        SELECT id, filename, size_bytes, is_public, likes, collections, created_at
        FROM files
        WHERE user_id = ?
        ORDER BY created_at DESC
    """, (user_id,)).fetchall()
    files = [get_file_info_from_record(r) for r in records]
    return jsonify({'success': True, 'files': files})

# ==================== 删除用户（含所有文件）====================
@admin_bp.route('/delete_user/<int:user_id>', methods=['DELETE'])
@admin_required
def admin_delete_user(user_id):
    db = get_db()
    user = db.execute('SELECT is_admin FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        return jsonify({'success': False, 'error': '用户不存在'}), 404
    if user['is_admin']:
        return jsonify({'success': False, 'error': '不能删除管理员账号'}), 403

    # 删除用户所有文件（物理文件）
    files = db.execute('SELECT file_path FROM files WHERE user_id = ?', (user_id,)).fetchall()
    for f in files:
        path = Path(f['file_path'])
        if path.exists():
            path.unlink()
    db.execute('DELETE FROM files WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM friends WHERE user_id = ? OR friend_id = ?', (user_id, user_id))
    db.execute('DELETE FROM messages WHERE sender_id = ? OR receiver_id = ?', (user_id, user_id))
    db.execute('DELETE FROM file_likes WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM file_collections WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM sign_in_log WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM exchange_log WHERE user_id = ?', (user_id,))

    # 删除用户文件夹
    user_folder = get_user_folder(user_id)
    if user_folder.exists():
        shutil.rmtree(user_folder)

    db.execute('DELETE FROM users WHERE id = ?', (user_id,))
    db.commit()
    return jsonify({'success': True, 'message': '用户已删除'})

# ==================== 重置用户密码 ====================
@admin_bp.route('/reset_password/<int:user_id>', methods=['POST'])
@admin_required
def admin_reset_password(user_id):
    data = request.get_json()
    new_password = data.get('new_password', '123456')
    if len(new_password) < 6:
        return jsonify({'success': False, 'error': '密码至少6位'}), 400
    db = get_db()
    user = db.execute('SELECT id FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        return jsonify({'success': False, 'error': '用户不存在'}), 404
    new_hash = generate_password_hash(new_password)
    db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (new_hash, user_id))
    db.commit()
    return jsonify({'success': True, 'message': f'密码已重置为 {new_password}'})

# ==================== 调整用户星币 ====================
@admin_bp.route('/adjust_coins/<int:user_id>', methods=['POST'])
@admin_required
def admin_adjust_coins(user_id):
    data = request.get_json()
    delta = data.get('delta', 0)
    reason = data.get('reason', '管理员调整')
    if not isinstance(delta, int) or delta == 0:
        return jsonify({'success': False, 'error': '调整量必须为非零整数'}), 400
    db = get_db()
    user = db.execute('SELECT id FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        return jsonify({'success': False, 'error': '用户不存在'}), 404
    success = update_user_coins(user_id, delta, reason)
    if not success:
        return jsonify({'success': False, 'error': '调整后星币不能为负数'}), 400
    new_balance = db.execute('SELECT coins FROM users WHERE id = ?', (user_id,)).fetchone()['coins']
    return jsonify({'success': True, 'new_balance': new_balance})