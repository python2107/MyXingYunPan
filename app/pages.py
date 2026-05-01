# app/pages.py
import mimetypes
from pathlib import Path
from flask import Blueprint, render_template, send_from_directory, send_file, abort, session, redirect, url_for
from datetime import datetime
from config import UPLOAD_FOLDER
from app.models import get_db
from app.utils import human_readable_size

pages_bp = Blueprint('pages', __name__)

# ==================== 页面渲染 ====================
@pages_bp.route('/')
def index():
    return render_template('index.html')

@pages_bp.route('/login')
def login_page():
    if 'user_id' in session:
        return redirect(url_for('pages.index'))
    return render_template('login.html')

@pages_bp.route('/logout')
def logout_page():
    session.clear()
    return redirect(url_for('pages.index'))

@pages_bp.route('/user_center')
def user_center():
    if 'user_id' not in session:
        return redirect(url_for('pages.login_page'))
    return render_template('user_center.html')

@pages_bp.route('/community')
def community_page():
    return render_template('community.html')

@pages_bp.route('/admin')
def admin_panel():
    # 简单检查是否为管理员
    if 'user_id' not in session:
        return redirect(url_for('pages.login_page'))
    db = get_db()
    user = db.execute('SELECT is_admin FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    if not user or not user['is_admin']:
        abort(403)
    return render_template('admin.html')

@pages_bp.route('/privacy')
def privacy_policy():
    now = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    return render_template('privacy_policy.html', now_time=now)

@pages_bp.route('/terms')
def user_agreement():
    now = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    return render_template('user_agreement.html', now_time=now)

# ==================== 用户公开主页 ====================
@pages_bp.route('/user/<string:name>')
def user_profile(name):
    db = get_db()
    user = db.execute('SELECT id, username, created_at FROM users WHERE username = ?', (name,)).fetchone()
    if not user:
        abort(404)
    files = db.execute(
        'SELECT id, filename, size_bytes, likes, collections, created_at FROM files WHERE user_id = ? AND is_public = 1 ORDER BY created_at DESC',
        (user['id'],)
    ).fetchall()
    file_list = [{
        'id': f['id'],
        'name': f['filename'],
        'size_human': human_readable_size(f['size_bytes']),
        'likes': f['likes'],
        'collections': f['collections'],
        'created_at': f['created_at'],
        'download_url': url_for('pages.numfile', num=f['id'])
    } for f in files]
    return render_template('user_profile.html', user=user, files=file_list)

# ==================== 通过文件ID访问 ====================
@pages_bp.route('/numfile/<int:num>')
def numfile(num):
    db = get_db()
    record = db.execute(
        'SELECT user_id, filename, file_path, is_public FROM files WHERE id = ?',
        (num,)
    ).fetchone()
    if not record:
        abort(404)
    # 私密文件权限检查
    if record['is_public'] == 0:
        if 'user_id' not in session or session['user_id'] != record['user_id']:
            abort(404)
    download = request.args.get('download', '0')
    path_obj = Path(record['file_path'])
    if not path_obj.exists():
        abort(404)
    mime_type, _ = mimetypes.guess_type(record['filename'])
    if download == '1':
        return send_from_directory(
            directory=path_obj.parent,
            path=path_obj.name,
            as_attachment=True,
            download_name=record['filename']
        )
    else:
        if mime_type and mime_type.startswith('image/'):
            return send_file(path_obj, mimetype=mime_type)
        else:
            return send_from_directory(
                directory=path_obj.parent,
                path=path_obj.name,
                as_attachment=False,
                download_name=record['filename']
            )

# ==================== 通过路径访问文件 ====================
@pages_bp.route('/pathfile/<string:path>')
def pathfile(path):
    if '/' not in path:
        abort(404)
    parts = path.split('/', 1)
    identifier = parts[0]
    filename = parts[1]
    db = get_db()
    if identifier.isdigit():
        user = db.execute('SELECT id FROM users WHERE id = ?', (int(identifier),)).fetchone()
    else:
        user = db.execute('SELECT id FROM users WHERE username = ?', (identifier,)).fetchone()
    if not user:
        abort(404)
    record = db.execute(
        'SELECT id, user_id, filename, file_path, is_public FROM files WHERE user_id = ? AND filename = ?',
        (user['id'], filename)
    ).fetchone()
    if not record:
        abort(404)
    if record['is_public'] == 0:
        if 'user_id' not in session or session['user_id'] != record['user_id']:
            abort(404)
    download = request.args.get('download', '0')
    path_obj = Path(record['file_path'])
    if not path_obj.exists():
        abort(404)
    mime_type, _ = mimetypes.guess_type(filename)
    if download == '1':
        return send_from_directory(
            directory=path_obj.parent,
            path=path_obj.name,
            as_attachment=True,
            download_name=filename
        )
    else:
        if mime_type and mime_type.startswith('image/'):
            return send_file(path_obj, mimetype=mime_type)
        else:
            return send_from_directory(
                directory=path_obj.parent,
                path=path_obj.name,
                as_attachment=False,
                download_name=filename
            )