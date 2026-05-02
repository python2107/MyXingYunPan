# app/social.py
from flask import Blueprint, request, jsonify, session
from app.models import get_db, are_friends, send_friend_request, accept_friend_request
from app.utils import human_readable_size

social_bp = Blueprint('social', __name__)

# ==================== 获取好友列表和好友请求 ====================
@social_bp.route('/friends')
def list_friends():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    uid = session['user_id']
    db = get_db()

    # 已接受的好友
    friends = db.execute("""
        SELECT u.id, u.username
        FROM friends f
        JOIN users u ON (f.user_id = u.id OR f.friend_id = u.id)
        WHERE (f.user_id = ? OR f.friend_id = ?) AND f.status = 'accepted' AND u.id != ?
    """, (uid, uid, uid)).fetchall()
    friends_list = [{'id': f['id'], 'username': f['username']} for f in friends]

    # 待处理的好友请求（别人发给我的）
    pending = db.execute("""
        SELECT f.id, u.id as from_id, u.username
        FROM friends f
        JOIN users u ON f.user_id = u.id
        WHERE f.friend_id = ? AND f.status = 'pending'
    """, (uid,)).fetchall()
    pending_requests = [
        {'request_id': p['id'], 'from_id': p['from_id'], 'username': p['username']}
        for p in pending
    ]

    return jsonify({
        'success': True,
        'friends': friends_list,
        'pending_requests': pending_requests
    })

# ==================== 搜索用户（排除自己和管理员）====================
@social_bp.route('/friends/search')
def search_users():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify({'success': True, 'users': []})
    db = get_db()
    users = db.execute(
        "SELECT id, username FROM users WHERE username LIKE ? AND id != ? AND username != 'admin' LIMIT 10",
        (f'%{q}%', session['user_id'])
    ).fetchall()
    return jsonify({'success': True, 'users': [{'id': u['id'], 'username': u['username']} for u in users]})

# ==================== 发送好友请求 ====================
@social_bp.route('/friends/request', methods=['POST'])
def send_friend_request_api():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    data = request.get_json()
    to_id = data.get('to_user_id')
    if not to_id or to_id == session['user_id']:
        return jsonify({'success': False, 'error': '无效的用户'}), 400
    db = get_db()
    target = db.execute('SELECT is_admin FROM users WHERE id = ?', (to_id,)).fetchone()
    if target and target['is_admin']:
        return jsonify({'success': False, 'error': '不能添加管理员为好友'}), 400
    if send_friend_request(session['user_id'], int(to_id)):
        return jsonify({'success': True, 'message': '好友请求已发送'})
    else:
        return jsonify({'success': False, 'error': '无法发送请求（已是好友或已有待处理请求）'}), 400

# ==================== 接受好友请求 ====================
@social_bp.route('/friends/accept', methods=['POST'])
def accept_friend_request_api():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    data = request.get_json()
    request_id = data.get('request_id')
    if not request_id:
        return jsonify({'success': False, 'error': '缺少请求ID'}), 400
    if accept_friend_request(int(request_id), session['user_id']):
        return jsonify({'success': True, 'message': '已添加好友'})
    else:
        return jsonify({'success': False, 'error': '无效的请求'}), 400

# ==================== 获取与指定好友的聊天记录 ====================
@social_bp.route('/messages')
def get_messages():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    friend_id = request.args.get('friend_id', type=int)
    if not friend_id:
        return jsonify({'success': False, 'error': '缺少好友ID'}), 400
    uid = session['user_id']
    db = get_db()

    rows = db.execute("""
        SELECT id, sender_id, receiver_id, content, type, file_id, image_url, created_at, is_read
        FROM messages
        WHERE (sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?)
        ORDER BY created_at ASC
    """, (uid, friend_id, friend_id, uid)).fetchall()

    # 构建消息列表，附带文件信息
    messages = []
    for r in rows:
        msg = {
            'id': r['id'],
            'sender_id': r['sender_id'],
            'receiver_id': r['receiver_id'],
            'content': r['content'],
            'type': r['type'],
            'time': r['created_at'],
            'is_read': bool(r['is_read'])
        }
        if r['type'] in ['file', 'image'] and r['file_id']:
            # 获取文件详情
            file_rec = db.execute('SELECT id, filename, size_bytes FROM files WHERE id = ?', (r['file_id'],)).fetchone()
            if file_rec:
                msg['file'] = {
                    'id': file_rec['id'],
                    'name': file_rec['filename'],
                    'size_human': human_readable_size(file_rec['size_bytes']),
                    'download_url': f'/numfile/{file_rec["id"]}?download=1'
                }
        elif r['type'] == 'image' and r['image_url']:
            msg['image_url'] = r['image_url']
        messages.append(msg)

    # 标记已读
    db.execute(
        'UPDATE messages SET is_read = 1 WHERE sender_id = ? AND receiver_id = ? AND is_read = 0',
        (friend_id, uid)
    )
    db.commit()
    return jsonify({'success': True, 'messages': messages})

# ==================== 发送文本消息 ====================
@social_bp.route('/messages/send', methods=['POST'])
def send_message():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    data = request.get_json()
    receiver_id = data.get('receiver_id')
    content = data.get('content', '').strip()
    if not receiver_id or not content:
        return jsonify({'success': False, 'error': '缺少参数'}), 400
    if not are_friends(session['user_id'], int(receiver_id)):
        return jsonify({'success': False, 'error': '不是好友关系'}), 403
    db = get_db()
    db.execute(
        'INSERT INTO messages (sender_id, receiver_id, type, content) VALUES (?, ?, "text", ?)',
        (session['user_id'], receiver_id, content)
    )
    db.commit()
    return jsonify({'success': True, 'message': '发送成功'})

# ==================== 发送文件消息（通过云盘文件ID）====================
@social_bp.route('/messages/send_file', methods=['POST'])
def send_file_message():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    data = request.get_json()
    receiver_id = data.get('receiver_id')
    file_id = data.get('file_id')
    msg_type = data.get('type', 'file')  # 'file' 或 'image'
    content = data.get('content', '').strip()

    if not receiver_id or not file_id:
        return jsonify({'success': False, 'error': '缺少接收者或文件ID'}), 400
    if not are_friends(session['user_id'], int(receiver_id)):
        return jsonify({'success': False, 'error': '不是好友关系'}), 403

    db = get_db()
    # 检查文件是否存在，且属于当前用户（只能发送自己的文件）
    file_rec = db.execute('SELECT id, user_id, filename, is_public FROM files WHERE id = ?', (file_id,)).fetchone()
    if not file_rec or file_rec['user_id'] != session['user_id']:
        return jsonify({'success': False, 'error': '文件不存在或无权发送'}), 404

    # 确保消息类型正确（如果是图片且文件是图片格式，可以标记为image）
    import mimetypes
    mime, _ = mimetypes.guess_type(file_rec['filename'])
    if mime and mime.startswith('image/') and msg_type == 'image':
        pass  # 保持 image 类型
    else:
        msg_type = 'file'

    db.execute(
        'INSERT INTO messages (sender_id, receiver_id, type, file_id, content) VALUES (?, ?, ?, ?, ?)',
        (session['user_id'], receiver_id, msg_type, file_id, content)
    )
    db.commit()
    return jsonify({'success': True, 'message': '文件已发送'})