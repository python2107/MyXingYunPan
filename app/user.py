# app/user.py
import os
import math
import shutil
from pathlib import Path
from flask import Blueprint, request, jsonify, session, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from config import UPLOAD_FOLDER
from app.models import (
    get_db, get_user_folder, get_user_coins, update_user_coins,
    get_user_capacity, check_capacity, can_sign_in, do_sign_in,
    follow_user, unfollow_user, is_following, get_followers, get_following,
    create_folder, delete_folder, move_file_to_folder, get_folder_tree
)
from app.utils import safe_filename, unique_filename, human_readable_size

user_bp = Blueprint('user', __name__)

# ==================== 辅助函数（文件信息转换，支持文件夹）====================
def get_file_info_from_record(record, with_folder=True):
    """将数据库记录转换为前端需要的文件信息格式"""
    info = {
        "id": record["id"],
        "name": record["filename"],
        "size": record["size_bytes"],
        "size_human": human_readable_size(record["size_bytes"]),
        "is_public": bool(record["is_public"]),
        "likes": record["likes"],
        "collections": record["collections"],
        "created_at": record["created_at"],
        "download_url": url_for('pages.numfile', num=record["id"]),
        "share_link": url_for('pages.numfile', num=record["id"])  # 分享链接（即下载链接）
    }
    if with_folder:
        info["folder_id"] = record.get("folder_id")
    return info

def get_folder_info_from_record(record):
    """文件夹信息转换"""
    return {
        "id": record["id"],
        "name": record["name"],
        "parent_id": record["parent_id"],
        "created_at": record["created_at"]
    }

# ==================== 用户信息 ====================
@user_bp.route('/user/info')
def user_info():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    db = get_db()
    user = db.execute(
        'SELECT id, username, coins, capacity_mb, created_at, followers_count, following_count FROM users WHERE id = ?',
        (uid,)
    ).fetchone()
    if not user:
        session.clear()
        return jsonify({'success': False, 'error': '用户不存在'}), 401
    used_mb = get_user_capacity(uid)
    return jsonify({
        'success': True,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'coins': user['coins'],
            'capacity_total_mb': user['capacity_mb'],
            'capacity_used_mb': used_mb,
            'joined_at': user['created_at'],
            'followers_count': user['followers_count'],
            'following_count': user['following_count']
        }
    })
# ==================== 修改用户资料（用户名/邮箱） ====================
@user_bp.route('/user/update_profile', methods=['POST'])
def update_profile():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401

    data = request.get_json()
    new_username = data.get('username', '').strip()
    new_email = data.get('email', '').strip().lower()

    if not new_username and not new_email:
        return jsonify({'success': False, 'error': '至少需要提供用户名或邮箱'}), 400

    db = get_db()
    updates = []
    params = []

    # 处理用户名修改
    if new_username:
        if len(new_username) < 3 or len(new_username) > 20:
            return jsonify({'success': False, 'error': '用户名长度需在3-20位之间'}), 400
        if new_username == 'admin':
            return jsonify({'success': False, 'error': '用户名不可用'}), 400
        # 检查用户名是否已被其他用户使用
        existing = db.execute('SELECT id FROM users WHERE username = ? AND id != ?', (new_username, uid)).fetchone()
        if existing:
            return jsonify({'success': False, 'error': '用户名已存在'}), 400
        updates.append('username = ?')
        params.append(new_username)

    # 处理邮箱修改
    if new_email:
        if '@' not in new_email or '.' not in new_email:
            return jsonify({'success': False, 'error': '邮箱格式不正确'}), 400
        # 检查邮箱是否已被其他用户使用
        existing = db.execute('SELECT id FROM users WHERE email = ? AND id != ?', (new_email, uid)).fetchone()
        if existing:
            return jsonify({'success': False, 'error': '邮箱已被其他账号使用'}), 400
        updates.append('email = ?')
        params.append(new_email)

    if not updates:
        return jsonify({'success': False, 'error': '没有要修改的内容'}), 400

    # 执行更新
    query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"
    params.append(uid)
    db.execute(query, params)
    db.commit()

    # 如果修改了用户名，同步更新 session 中的 username
    if new_username:
        session['username'] = new_username

    return jsonify({'success': True, 'message': '资料修改成功'})

# ==================== 关注/取消关注 ====================
@user_bp.route('/user/follow/<int:target_id>', methods=['POST'])
def follow(target_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if uid == target_id:
        return jsonify({'success': False, 'error': '不能关注自己'}), 400
    db = get_db()
    target = db.execute('SELECT id FROM users WHERE id = ?', (target_id,)).fetchone()
    if not target:
        return jsonify({'success': False, 'error': '用户不存在'}), 404
    if follow_user(uid, target_id):
        return jsonify({'success': True, 'message': '关注成功'})
    else:
        return jsonify({'success': False, 'error': '已经关注过'}), 400

@user_bp.route('/user/unfollow/<int:target_id>', methods=['POST'])
def unfollow(target_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if uid == target_id:
        return jsonify({'success': False, 'error': '不能取消关注自己'}), 400
    db = get_db()
    target = db.execute('SELECT id FROM users WHERE id = ?', (target_id,)).fetchone()
    if not target:
        return jsonify({'success': False, 'error': '用户不存在'}), 404
    unfollow_user(uid, target_id)
    return jsonify({'success': True, 'message': '已取消关注'})

@user_bp.route('/user/followers')
def followers():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    limit = request.args.get('limit', 20, type=int)
    followers_list = get_followers(uid, limit)
    return jsonify({'success': True, 'followers': followers_list})

@user_bp.route('/user/following')
def following():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    limit = request.args.get('limit', 20, type=int)
    following_list = get_following(uid, limit)
    return jsonify({'success': True, 'following': following_list})

# ==================== 文件夹管理 ====================
@user_bp.route('/folders', methods=['GET'])
def get_folders():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    tree = get_folder_tree(uid)
    return jsonify({'success': True, 'folders': tree})

@user_bp.route('/folders', methods=['POST'])
def create_folder_api():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    data = request.get_json()
    name = data.get('name', '').strip()
    parent_id = data.get('parent_id')
    if not name:
        return jsonify({'success': False, 'error': '文件夹名称不能为空'}), 400
    # 检查同一目录下重名
    db = get_db()
    existing = db.execute(
        'SELECT id FROM folders WHERE user_id = ? AND parent_id IS ? AND name = ?',
        (uid, parent_id, name)
    ).fetchone()
    if existing:
        return jsonify({'success': False, 'error': '同名文件夹已存在'}), 400
    folder_id = create_folder(uid, name, parent_id)
    return jsonify({'success': True, 'folder_id': folder_id, 'message': '创建成功'})

@user_bp.route('/folders/<int:folder_id>', methods=['DELETE'])
def delete_folder_api(folder_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    db = get_db()
    folder = db.execute('SELECT user_id FROM folders WHERE id = ?', (folder_id,)).fetchone()
    if not folder or folder['user_id'] != uid:
        return jsonify({'success': False, 'error': '文件夹不存在或无权限'}), 404
    delete_folder(folder_id, uid)
    return jsonify({'success': True, 'message': '删除成功'})

@user_bp.route('/files/move', methods=['POST'])
def move_file():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    data = request.get_json()
    file_id = data.get('file_id')
    folder_id = data.get('folder_id')
    if not file_id:
        return jsonify({'success': False, 'error': '缺少文件ID'}), 400
    db = get_db()
    file_rec = db.execute('SELECT user_id FROM files WHERE id = ?', (file_id,)).fetchone()
    if not file_rec or file_rec['user_id'] != uid:
        return jsonify({'success': False, 'error': '文件不存在或无权限'}), 404
    if folder_id is not None:
        # 验证目标文件夹属于当前用户
        folder = db.execute('SELECT user_id FROM folders WHERE id = ?', (folder_id,)).fetchone()
        if not folder or folder['user_id'] != uid:
            return jsonify({'success': False, 'error': '目标文件夹无效'}), 404
    move_file_to_folder(file_id, folder_id, uid)
    return jsonify({'success': True, 'message': '移动成功'})

# ==================== 文件管理（修改支持文件夹）====================
@user_bp.route('/files')
def list_my_files():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    folder_id = request.args.get('folder_id', type=int)
    db = get_db()
    if folder_id:
        # 检查文件夹属于当前用户
        folder = db.execute('SELECT user_id FROM folders WHERE id = ?', (folder_id,)).fetchone()
        if not folder or folder['user_id'] != uid:
            return jsonify({'success': False, 'error': '文件夹不存在'}), 404
        query = """
            SELECT id, filename, size_bytes, is_public, likes, collections, created_at, folder_id
            FROM files WHERE user_id = ? AND folder_id = ? ORDER BY created_at DESC
        """
        records = db.execute(query, (uid, folder_id)).fetchall()
    else:
        # 根目录（folder_id IS NULL）
        records = db.execute(
            "SELECT id, filename, size_bytes, is_public, likes, collections, created_at, folder_id FROM files WHERE user_id = ? AND folder_id IS NULL ORDER BY created_at DESC",
            (uid,)
        ).fetchall()
    files = [get_file_info_from_record(r) for r in records]
    return jsonify({'success': True, 'files': files})

@user_bp.route('/upload', methods=['POST'])
def upload_file():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': '没有文件部分'}), 400
    f = request.files['file']
    if f.filename == '':
        return jsonify({'success': False, 'error': '未选择文件'}), 400

    is_public = request.form.get('is_public', '0') == '1'
    folder_id = request.form.get('folder_id', type=int)  # 可选，指定上传到哪个文件夹

    original_filename = safe_filename(f.filename)
    if not original_filename:
        return jsonify({'success': False, 'error': '无效的文件名'}), 400

    # 检查容量
    f.seek(0, os.SEEK_END)
    file_size = f.tell()
    f.seek(0)
    if not check_capacity(uid, file_size):
        return jsonify({'success': False, 'error': '存储容量不足，请兑换容量或删除旧文件'}), 403

    # 验证文件夹（如果指定）
    db = get_db()
    if folder_id:
        folder = db.execute('SELECT user_id FROM folders WHERE id = ?', (folder_id,)).fetchone()
        if not folder or folder['user_id'] != uid:
            return jsonify({'success': False, 'error': '目标文件夹不存在'}), 404

    user_dir = get_user_folder(uid)
    final_name = unique_filename(user_dir, original_filename)
    try:
        f.save(user_dir / final_name)
        db.execute(
            'INSERT INTO files (user_id, filename, file_path, size_bytes, is_public, folder_id) VALUES (?, ?, ?, ?, ?, ?)',
            (uid, final_name, str(user_dir / final_name), file_size, 1 if is_public else 0, folder_id)
        )
        file_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        db.commit()
        # 奖励星币
        size_mb = max(1, file_size // (1024 * 1024))
        update_user_coins(uid, size_mb, f'上传文件 {final_name} 获得 {size_mb} 星币')
        return jsonify({
            'success': True,
            'message': f'上传成功，获得{size_mb}星币',
            'file': get_file_info_from_record(db.execute('SELECT * FROM files WHERE id = ?', (file_id,)).fetchone())
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f'保存失败: {str(e)}'}), 500

@user_bp.route('/delete/<int:file_id>', methods=['DELETE'])
def delete_file(file_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    db = get_db()
    rec = db.execute('SELECT user_id, file_path FROM files WHERE id = ?', (file_id,)).fetchone()
    if not rec or rec['user_id'] != uid:
        return jsonify({'success': False, 'error': '无权限或文件不存在'}), 404
    path = Path(rec['file_path'])
    if path.exists():
        path.unlink()
    db.execute('DELETE FROM files WHERE id = ?', (file_id,))
    db.execute('DELETE FROM file_likes WHERE file_id = ?', (file_id,))
    db.execute('DELETE FROM file_collections WHERE file_id = ?', (file_id,))
    db.commit()
    return jsonify({'success': True, 'message': '删除成功'})

@user_bp.route('/file/toggle_public/<int:file_id>', methods=['POST'])
def toggle_public(file_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    db = get_db()
    rec = db.execute('SELECT user_id, is_public FROM files WHERE id = ?', (file_id,)).fetchone()
    if not rec or rec['user_id'] != uid:
        return jsonify({'success': False, 'error': '无权限'}), 403
    new_status = 1 - rec['is_public']
    db.execute('UPDATE files SET is_public = ? WHERE id = ?', (new_status, file_id))
    db.commit()
    return jsonify({'success': True, 'is_public': bool(new_status)})

# ==================== 我的收藏（未变）====================
@user_bp.route('/user/my_collections')
def my_collections():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    db = get_db()
    records = db.execute("""
        SELECT f.id, f.filename, f.size_bytes, f.likes, f.collections, f.created_at, u.username as uploader
        FROM files f
        JOIN file_collections c ON f.id = c.file_id
        JOIN users u ON f.user_id = u.id
        WHERE c.user_id = ?
        ORDER BY c.created_at DESC
    """, (uid,)).fetchall()
    files = [{
        'id': r['id'],
        'name': r['filename'],
        'size_human': human_readable_size(r['size_bytes']),
        'uploader': r['uploader'],
        'likes': r['likes'],
        'collections': r['collections'],
        'created_at': r['created_at'],
        'download_url': url_for('pages.numfile', num=r['id'])
    } for r in records]
    return jsonify({'success': True, 'files': files})

# ==================== 星币与签到、密码、注销等（保持不变）====================
@user_bp.route('/user/change_password', methods=['POST'])
def change_password():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    data = request.get_json()
    old = data.get('old_password', '')
    new = data.get('new_password', '')
    if not old or not new:
        return jsonify({'success': False, 'error': '请填写完整'}), 400
    if len(new) < 6:
        return jsonify({'success': False, 'error': '新密码至少6位'}), 400
    db = get_db()
    user = db.execute('SELECT password_hash FROM users WHERE id = ?', (uid,)).fetchone()
    if not check_password_hash(user['password_hash'], old):
        return jsonify({'success': False, 'error': '原密码错误'}), 401
    db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (generate_password_hash(new), uid))
    db.commit()
    return jsonify({'success': True, 'message': '密码修改成功'})

@user_bp.route('/user/delete_account', methods=['POST'])
def delete_account():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    db = get_db()
    user = db.execute('SELECT is_admin FROM users WHERE id = ?', (uid,)).fetchone()
    if user and user['is_admin']:
        return jsonify({'success': False, 'error': '管理员不可注销'}), 403
    # 删除所有文件
    files = db.execute('SELECT file_path FROM files WHERE user_id = ?', (uid,)).fetchall()
    for f in files:
        Path(f['file_path']).unlink(missing_ok=True)
    db.execute('DELETE FROM files WHERE user_id = ?', (uid,))
    db.execute('DELETE FROM folders WHERE user_id = ?', (uid,))
    db.execute('DELETE FROM friends WHERE user_id = ? OR friend_id = ?', (uid, uid))
    db.execute('DELETE FROM messages WHERE sender_id = ? OR receiver_id = ?', (uid, uid))
    db.execute('DELETE FROM file_likes WHERE user_id = ?', (uid,))
    db.execute('DELETE FROM file_collections WHERE user_id = ?', (uid,))
    db.execute('DELETE FROM sign_in_log WHERE user_id = ?', (uid,))
    db.execute('DELETE FROM exchange_log WHERE user_id = ?', (uid,))
    db.execute('DELETE FROM followers WHERE user_id = ? OR follower_id = ?', (uid, uid))
    user_folder = get_user_folder(uid)
    if user_folder.exists():
        shutil.rmtree(user_folder)
    db.execute('DELETE FROM users WHERE id = ?', (uid,))
    db.commit()
    session.clear()
    return jsonify({'success': True, 'message': '账号已注销'})

@user_bp.route('/user/coins')
def get_coins():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    return jsonify({'success': True, 'coins': get_user_coins(uid)})

@user_bp.route('/user/coin_logs')
def get_coin_logs():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    db = get_db()
    logs = db.execute(
        'SELECT change_amount, balance_after, reason, created_at FROM coin_logs WHERE user_id = ? ORDER BY created_at DESC LIMIT 50',
        (uid,)
    ).fetchall()
    return jsonify({
        'success': True,
        'logs': [{
            'change': l['change_amount'],
            'balance': l['balance_after'],
            'reason': l['reason'],
            'time': l['created_at']
        } for l in logs]
    })

@user_bp.route('/user/sign_in', methods=['POST'])
def sign_in():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not can_sign_in(uid):
        return jsonify({'success': False, 'error': '今日已签到'}), 400
    gained = do_sign_in(uid)
    return jsonify({'success': True, 'gained': gained, 'new_balance': get_user_coins(uid)})

@user_bp.route('/user/exchange_capacity', methods=['POST'])
def exchange_capacity():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': '未登录'}), 401
    data = request.get_json()
    coins_to_spend = data.get('coins', 0)
    if not isinstance(coins_to_spend, int) or coins_to_spend <= 0:
        return jsonify({'success': False, 'error': '请输入正整数的星币数量'}), 400
    current_coins = get_user_coins(uid)
    if current_coins < coins_to_spend:
        return jsonify({'success': False, 'error': '星币不足'}), 400
    mb_gain = max(1, coins_to_spend // 2)
    db = get_db()
    if not update_user_coins(uid, -coins_to_spend, f'兑换{mb_gain}MB容量消耗{coins_to_spend}星币'):
        return jsonify({'success': False, 'error': '扣币失败'}), 500
    db.execute('UPDATE users SET capacity_mb = capacity_mb + ? WHERE id = ?', (mb_gain, uid))
    db.execute(
        'INSERT INTO exchange_log (user_id, coins_spent, mb_gained, total_capacity) VALUES (?, ?, ?, (SELECT capacity_mb FROM users WHERE id = ?))',
        (uid, coins_to_spend, mb_gain, uid)
    )
    db.commit()
    new_capacity = db.execute('SELECT capacity_mb FROM users WHERE id = ?', (uid,)).fetchone()['capacity_mb']
    return jsonify({
        'success': True,
        'mb_gained': mb_gain,
        'new_capacity_mb': new_capacity,
        'new_balance': get_user_coins(uid)
    })