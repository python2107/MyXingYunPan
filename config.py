import os
from datetime import timedelta

# 基础路径
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Flask 配置
SECRET_KEY = "change-this-in-production"
MAX_CONTENT_LENGTH = 256 * 1024 * 1024
PERMANENT_SESSION_LIFETIME = timedelta(days=7)

# 上传目录
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")

# 数据库路径
DATABASE = os.path.join(BASE_DIR, "users.db")

# 163 邮箱配置（生产环境需修改）
EMAIL_HOST = "smtp.163.com"
EMAIL_PORT = 465
EMAIL_USER = ""          # 你的邮箱地址
EMAIL_PASSWORD = ""  # 授权码（不是登录密码）

# 验证码配置
VERIFICATION_CODE_EXPIRE = 300  # 5分钟

# 开发模式：为 True 时，验证码直接打印到终端，不实际发邮件
DEBUG_EMAIL = True