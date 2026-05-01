# app/email_utils.py
import smtplib
import re
import random
import string
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASSWORD, DEBUG_EMAIL

def send_email(to_email, subject, body):
    """发送邮件，开发模式下打印到控制台"""
    if DEBUG_EMAIL:
        # 提取验证码（假设正文包含6位数字）
        match = re.search(r'\b\d{6}\b', body)
        code = match.group(0) if match else "无"
        print("\n" + "="*50)
        print(f"[开发模式] 邮件发送给: {to_email}")
        print(f"主题: {subject}")
        print(f"验证码: {code}")
        print("="*50 + "\n")
        return True

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
    """生成指定长度的数字验证码"""
    return ''.join(random.choices(string.digits, k=length))