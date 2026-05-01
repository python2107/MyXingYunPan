# app/utils.py
import os
import math
from pathlib import Path

def human_readable_size(size: int) -> str:
    """将字节数转换为人类可读的格式"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"

def safe_filename(original: str) -> str:
    """
    将原始文件名转换为安全的文件名：
    - 只保留字母、数字、点、下划线、横线、空格
    - 其他字符（包括路径分隔符、特殊符号）替换为下划线
    """
    base = os.path.basename(original)
    safe_chars = []
    for ch in base:
        if ch.isalnum() or ch in ".-_ ":
            safe_chars.append(ch)
        else:
            safe_chars.append("_")
    return "".join(safe_chars).strip()

def unique_filename(directory: Path, filename: str) -> str:
    """
    如果目录下已存在同名文件，则在文件名后添加序号。
    例如：file.txt -> file (1).txt
    """
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1
    new_name = filename
    while (directory / new_name).exists():
        new_name = f"{stem} ({counter}){suffix}"
        counter += 1
    return new_name

def get_file_icon(filename: str) -> str:
    """根据文件扩展名返回 FontAwesome 图标类名"""
    ext = filename.split('.')[-1].lower() if '.' in filename else ''
    icons = {
        'pdf': 'fa-file-pdf',
        'jpg': 'fa-file-image', 'jpeg': 'fa-file-image', 'png': 'fa-file-image', 'gif': 'fa-file-image', 'webp': 'fa-file-image',
        'mp4': 'fa-file-video', 'mkv': 'fa-file-video', 'mov': 'fa-file-video',
        'mp3': 'fa-file-audio', 'wav': 'fa-file-audio',
        'zip': 'fa-file-archive', 'rar': 'fa-file-archive', '7z': 'fa-file-archive', 'tar': 'fa-file-archive',
        'doc': 'fa-file-word', 'docx': 'fa-file-word',
        'xls': 'fa-file-excel', 'xlsx': 'fa-file-excel',
        'ppt': 'fa-file-powerpoint', 'pptx': 'fa-file-powerpoint',
        'txt': 'fa-file-alt', 'md': 'fa-file-alt',
    }
    return icons.get(ext, 'fa-file')