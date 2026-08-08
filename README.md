# MyXingYunPan

A cloud use python flask.

## 简介

MyXingYunPan 是基于 Python Flask 打造的社交云盘项目，支持文件存储与管理、实时聊天、好友系统、动态关注等丰富功能，兼容移动端访问，适合个人和小型团队部署使用。

## 项目亮点

- 点赞与收藏奖励机制，形成良性激励闭环
- 完善的好友系统：支持搜索、添加好友、接受请求
- 实时聊天功能和动态消息推送
- 用户关注体系与动态流
- 个人主页支持头像/信息/粉丝/关注/公开文件展示
- 个人中心一站式管理：密码、邮箱、用户名可修改
- 管理员后台：用户&文件管理，星币调整、安全重置
- 支持文件分享、移动端适配、验证码与时区优化

## 快速开始

1. **安装依赖**
   ```bash
   pip install -r requirements.txt
   ```

2. **初始化数据库**
   ```bash
   python init_db.py
   ```

3. **运行项目**
   ```bash
   python app.py
   ```
   或（多模块时）
   ```bash
   flask run
   ```

4. **访问地址**
   - 本地浏览器访问：http://127.0.0.1:5000

## 使用说明

- **注册与登录**：支持邮箱注册，安全验证与密码修改
- **好友系统**：搜索用户，发送/接受好友请求，支持实时聊天
- **关注与动态**：可关注感兴趣用户，第一时间获取其公开动态
- **文件管理**：上传、分享文件，底部链接附带项目开源地址
- **移动端适配**：自动留白，触摸区域友好
- **管理员功能**：后台入口见导航菜单，可管理所有用户&文件

## 目录结构

```text
.
├── app.py
├── init_db.py
├── requirements.txt
├── config.py
├── README.md
├── .gitignore
├── users.db
├── static/
│   ├── css/
│   │   └── style.css
│   ├── js/
│   │   └── main.js
│   └── images/
│       └── logo.png
├── templates/
│   ├── index.html
│   ├── login.html
│   ├── register.html
│   ├── dashboard.html
│   ├── chat.html
│   ├── profile.html
│   ├── admin.html
│   ├── 404.html
│   └── layout.html
├── uploads/
│   └── （用户上传内容文件夹）
├── utils/
│   ├── email_utils.py
│   └── security.py
├── pathfile.py
├── numfile.py
├── filepath.py
...
```

## 贡献指南

欢迎 Issue、PR 及反馈建议，详见 [GitHub 项目地址](https://github.com/python2107/MyXingYunPan)。

---
