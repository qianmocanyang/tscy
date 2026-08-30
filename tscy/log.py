"""
日志模块 —— 统一输出格式，控制台带颜色，同时落盘到 logs/tscy.log。

Windows 控制台默认不解析 ANSI 颜色码，这里尝试打开虚拟终端模式；
失败就自动降级为无颜色输出，不会报错。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from .paths import app_root

LOG_DIR = app_root() / "logs"
LOG_FILE = LOG_DIR / "tscy.log"

_COLORS = {
    "DEBUG": "\033[36m",     # 青
    "INFO": "\033[37m",      # 白
    "WARN": "\033[33m",      # 黄
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",     # 红
    "CRITICAL": "\033[35m",  # 品红
}
_RESET = "\033[0m"
_DIM = "\033[90m"

_initialized = False
_color_enabled = True


def _enable_windows_vt() -> bool:
    """Windows 10+ 开启控制台 ANSI 虚拟终端解析。"""
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            return True
    except Exception:
        pass
    return False


def setup(level: str = "INFO", quiet: bool = False) -> None:
    global _initialized, _color_enabled
    if _initialized:
        get_logger().setLevel(_get_level(level))
        return

    _color_enabled = _enable_windows_vt()

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("tscy")
    root.setLevel(_get_level(level))
    root.handlers.clear()
    root.propagate = False

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-5s] %(name)-14s │ %(message)s",
        datefmt="%H:%M:%S",
    )

    if not quiet:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(_ColorFormatter())
        root.addHandler(sh)

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    _initialized = True


class _ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, datefmt="%H:%M:%S")
        name = record.name.replace("tscy.", "") or "core"
        msg = record.getMessage()
        if _color_enabled:
            c = _COLORS.get(record.levelname, "")
            return f"{_DIM}{ts}{_RESET} {c}{record.levelname:<5}{_RESET} {_DIM}{name:<12}│{_RESET} {msg}"
        return f"{ts} [{record.levelname:<5}] {name:<12}| {msg}"


def _get_level(level: str) -> int:
    return getattr(logging, str(level).upper(), logging.INFO)


def get_logger(name: str = "tscy") -> logging.Logger:
    if not name.startswith("tscy"):
        name = f"tscy.{name}"
    return logging.getLogger(name)
