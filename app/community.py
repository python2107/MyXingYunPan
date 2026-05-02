# app/community.py
from flask import Blueprint, request, jsonify, session, url_for
from app.models import get_db, update_user_coins
from app.utils import human_readable_size

community_bp = Blueprint('community', __name__)

# ==================== 获取公开文件列表（支持搜索） ====================
@community_bp.route('/community/files')
def community_files():
    search_query = request.args.get('search', '').strip()
    db = get_db()
    
    if search_query:
        # 对文件名和上传者用户名进行模糊搜索
        like_pattern = f'%{search_query}%'
        records = db.execute("""
            SELECT f.id, f.filename, f.size_bytes, f.likes, f.collections, f.created_at, u.username as uploader
            FROM files f
            JOIN users u ON f.user_id = u.id
            WHERE f.is_public = 1 AND (f.filename LIKE ? OR u.username LIKE ?)
            ORDER BY f.likes DESC, f.created_at DESC
            LIMIT 100
        """, (like_pattern, like_pattern)).fetchall()
    else:
        records = db.execute("""
            SELECT f.id, f.filename, f.size_bytes, f.likes, f.collections, f.created_at, u.username as uploader
            FROM files f
            JOIN users u ON f.user_id = u.id
            WHERE f.is_public = 1
            ORDER BY f.likes DESC, f.created_at DESC
            LIMIT 100
        """).fetchall()

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

# ==================== 点赞文件 ====================
@community_bp.route('/community/like/<int:file_id>', methods=['POST'])
def like_file(file_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    uid = session['user_id']
    db = get_db()
    
    file = db.execute('SELECT id, user_id, is_public FROM files WHERE id = ?', (file_id,)).fetchone()
    if not file or not file['is_public']:
        return jsonify({'success': False, 'error': '文件不存在或非公开'}), 404
    
    existing = db.execute('SELECT id FROM file_likes WHERE user_id = ? AND file_id = ?', (uid, file_id)).fetchone()
    if existing:
        return jsonify({'success': False, 'error': '你已经点过赞了'}), 400
    
    db.execute('INSERT INTO file_likes (user_id, file_id) VALUES (?, ?)', (uid, file_id))
    db.execute('UPDATE files SET likes = likes + 1 WHERE id = ?', (file_id,))
    update_user_coins(file['user_id'], 1, f'文件 {file_id} 获得一个点赞')
    db.commit()
    return jsonify({'success': True, 'message': '点赞成功'})

# ==================== 收藏文件 ====================
@community_bp.route('/community/collect/<int:file_id>', methods=['POST'])
def collect_file(file_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    uid = session['user_id']
    db = get_db()
    
    file = db.execute('SELECT id, user_id, is_public FROM files WHERE id = ?', (file_id,)).fetchone()
    if not file or not file['is_public']:
        return jsonify({'success': False, 'error': '文件不存在或非公开'}), 404
    
    existing = db.execute('SELECT id FROM file_collections WHERE user_id = ? AND file_id = ?', (uid, file_id)).fetchone()
    if existing:
        return jsonify({'success': False, 'error': '你已经收藏过了'}), 400
    
    db.execute('INSERT INTO file_collections (user_id, file_id) VALUES (?, ?)', (uid, file_id))
    db.execute('UPDATE files SET collections = collections + 1 WHERE id = ?', (file_id,))
    update_user_coins(file['user_id'], 2, f'文件 {file_id} 获得一个收藏')
    db.commit()
    return jsonify({'success': True, 'message': '收藏成功'})