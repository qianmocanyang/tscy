"""
全局热键 —— 游戏全屏状态下也能响应。

两种绑定方式：
  1. 单键（如 "f9"）  → 分别绑定 按下 / 松开，用于 PTT 按住说话。
  2. 组合键（如 "ctrl+alt+l"）→ 只能绑定"触发"，因为 keyboard 库的
     on_press_key 不支持组合键。

⚠️ Windows 重要限制：
    keyboard 库通过全局钩子监听按键，非管理员进程注册会失败（或只能在本进程生效）。
    所以 run.bat 必须"以管理员身份运行"。本模块启动时会检测并给出明确提示。

⚠️ 铁律：
    热键回调里**只能发信号，绝不能干重活**（推理/网络请求）。
    回调跑在 keyboard 的监听线程上，一旦卡住，整个键鼠监听都会僵死。
"""

from __future__ import annotations

import threading
from typing import Callable

from .log import get_logger

log = get_logger("hotkey")


def is_admin() -> bool:
    """Windows 下判断当前进程是否以管理员身份运行。"""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return True   # 非 Windows 平台不做限制


class HotkeyManager:
    def __init__(self):
        self._bindings: list[dict] = []
        self._down: set[str] = set()        # 去重：按住不放时 press 会被反复触发
        self._lock = threading.Lock()
        self._started = False

    def register(
        self,
        name: str,
        spec: str,
        on_press: Callable[[], None] | None = None,
        on_release: Callable[[], None] | None = None,
    ) -> None:
        """注册一个热键。spec 为空字符串表示不绑定。"""
        spec = (spec or "").strip()
        if not spec:
            log.debug(f"热键 {name} 未配置，跳过绑定")
            return
        self._bindings.append({
            "name": name, "spec": spec,
            "press": on_press, "release": on_release,
        })

    def start(self) -> None:
        import keyboard

        if self._started:
            return

        for b in self._bindings:
            spec = b["spec"]
            # 单键 + 需要松开事件 → 分别绑定 press/release
            if b["release"] is not None and "+" not in spec:
                self._bind_press_release(spec, b["press"], b["release"])
                log.info(f"热键 [{spec}] → {b['name']}（按住/松开）")
            else:
                cb = b["press"] or b["release"]
                if cb is None:
                    continue
                try:
                    keyboard.add_hotkey(spec, self._wrap(spec, cb), suppress=False)
                    log.info(f"热键 [{spec}] → {b['name']}")
                except Exception as e:
                    log.error(f"热键 [{spec}] 绑定失败: {e}")

        self._started = True

    def _bind_press_release(
        self,
        spec: str,
        on_press: Callable[[], None] | None,
        on_release: Callable[[], None] | None,
    ) -> None:
        import keyboard

        def _press(e=None):  # noqa: ARG001
            with self._lock:
                if spec in self._down:
                    return          # 系统自动重复，忽略
                self._down.add(spec)
            if on_press:
                self._safe(on_press)

        def _release(e=None):  # noqa: ARG001
            with self._lock:
                if spec not in self._down:
                    return
                self._down.discard(spec)
            if on_release:
                self._safe(on_release)

        keyboard.on_press_key(spec, _press)
        keyboard.on_release_key(spec, _release)

    def _wrap(self, spec: str, cb: Callable[[], None]):
        def _fn():
            self._safe(cb)
        return _fn

    @staticmethod
    def _safe(cb: Callable[[], None]) -> None:
        """回调里抛异常不能让监听线程死掉。"""
        try:
            cb()
        except Exception as e:
            log.error(f"热键回调异常: {e}", exc_info=False)

    def stop(self) -> None:
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass
        self._started = False
