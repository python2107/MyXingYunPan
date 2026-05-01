# app/__init__.py
from flask import Flask
import os
from config import SECRET_KEY, MAX_CONTENT_LENGTH, PERMANENT_SESSION_LIFETIME, UPLOAD_FOLDER
from app.models import init_db, close_connection

def create_app():
    app = Flask(__name__,
                template_folder='templates',
                static_folder='static')
    app.config['SECRET_KEY'] = SECRET_KEY
    app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
    app.config['PERMANENT_SESSION_LIFETIME'] = PERMANENT_SESSION_LIFETIME

    # 确保上传目录存在
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    # 注册蓝图
    from app.pages import pages_bp
    from app.auth import auth_bp
    from app.user import user_bp
    from app.community import community_bp
    from app.social import social_bp
    from app.admin import admin_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(auth_bp, url_prefix='/api')
    app.register_blueprint(user_bp, url_prefix='/api')
    app.register_blueprint(community_bp, url_prefix='/api')
    app.register_blueprint(social_bp, url_prefix='/api')
    app.register_blueprint(admin_bp, url_prefix='/api/admin')

    # 初始化数据库（传入app）
    init_db(app)

    # 注册数据库连接关闭钩子
    app.teardown_appcontext(close_connection)

    return app