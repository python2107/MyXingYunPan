import tkinter as tk
from tkinter import messagebox
import sqlite3
from werkzeug.security import check_password_hash  # 关键！和Web一致

# --------------------------
# 数据库路径（放你的 users.db）
# --------------------------
DB_PATH = "users.db"

class CloudDriveLogin:
    def __init__(self, root):
        self.root = root
        self.root.title("个人云盘 - 登录")
        self.root.geometry("420x260")
        self.root.resizable(False, False)

        # 界面
        tk.Label(root, text="账号密码登录", font=("微软雅黑", 16)).pack(pady=20)

        # 账号
        tk.Label(root, text="账号：", font=("微软雅黑", 12)).place(x=60, y=80)
        self.entry_user = tk.Entry(root, font=("微软雅黑", 12), width=25)
        self.entry_user.place(x=120, y=80)

        # 密码
        tk.Label(root, text="密码：", font=("微软雅黑", 12)).place(x=60, y=120)
        self.entry_pwd = tk.Entry(root, font=("微软雅黑", 12), width=25, show="*")
        self.entry_pwd.place(x=120, y=120)

        # 登录按钮
        tk.Button(root, text="登录", font=("微软雅黑", 12), width=12,
                  command=self.check_login).place(x=150, y=170)

    # --------------------------
    # 核心：和 Web 完全一样的校验
    # --------------------------
    def check_login(self):
        username = self.entry_user.get().strip()
        password = self.entry_pwd.get().strip()

        # 1. 前端格式校验（和网页规则完全一致）
        if len(username) < 3 or len(username) > 20:
            messagebox.showerror("错误", "账号必须 3-20 位")
            return
        if len(password) < 6:
            messagebox.showerror("错误", "密码至少 6 位")
            return

        try:
            # 2. 连接 SQLite 数据库（同一个 users.db）
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            # 3. 查询用户（和 Web 端 SQL 一样）
            cursor.execute("SELECT username, password FROM users WHERE username = ?", (username,))
            user = cursor.fetchone()

            if not user:
                messagebox.showerror("失败", "账号不存在")
                return

            db_username, db_password_hash = user

            # 4. ✅ 关键：Werkzeug 哈希校验（和网页登录逻辑一模一样）
            if check_password_hash(db_password_hash, password):
                messagebox.showinfo("成功", f"欢迎回来，{username}！")
                self.open_main_window()  # 登录成功
            else:
                messagebox.showerror("失败", "密码错误")

            conn.close()

        except Exception as e:
            messagebox.showerror("错误", f"数据库异常：{str(e)}")

    def open_main_window(self):
        # 登录成功后打开主界面
        self.root.destroy()
        main = tk.Tk()
        main.title("云盘主界面")
        main.geometry("800x500")
        tk.Label(main, text="登录成功，已连接 Web 云端数据库", font=("微软雅黑", 18)).pack(pady=50)
        main.mainloop()

if __name__ == "__main__":
    root = tk.Tk()
    app = CloudDriveLogin(root)
    root.mainloop()