"""
路径统一入口 —— 区分开发环境和 PyInstaller 打包后的运行环境。

开发时：根目录就是代码仓库根目录。
PyInstaller 单文件 exe：exe 所在目录作为用户数据目录（config / cache / logs / models），
                     assets 等打包资源从 _MEIPASS 临时目录读取。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否被 PyInstaller 等工具打包。"""
    return getattr(sys, "frozen", False)


def exe_dir() -> Path:
    """exe 所在目录（开发时等价于 python 解释器目录）。"""
    return Path(sys.executable).parent


def app_root() -> Path:
    """
    用户数据根目录。
    打包后放在 exe 旁边，方便用户直接改 config.json。
    """
    if is_frozen():
        return exe_dir()
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """
    只读资源目录（assets/logo.ico 等）。
    打包后从 _MEIPASS 读取；开发时从仓库 assets/ 读取。
    """
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return app_root()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
