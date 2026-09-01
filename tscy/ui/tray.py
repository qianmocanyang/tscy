"""
系统托盘图标 —— 用 pystray 实现，logo 同时作为 app 图标和状态栏图标。

托盘菜单提供：
  · 打开设置面板
  · 快速切换目标语言
  · 显示/隐藏字幕
  · 开启/关闭语音播报
  · 退出

注意：pystray 的回调跑在它自己的线程里，操作 UI 或配置时都要通过 root.after 回到主线程。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from PIL import Image

from ..lang import CYCLE_ORDER, display, get as get_lang
from ..log import get_logger
from ..paths import resource_dir

log = get_logger("tray")


class TrayIcon:
    """系统托盘图标封装。"""

    DEFAULT_LOGO = resource_dir() / "assets" / "logo.png"

    def __init__(self, controller, logo_path: str | None = None):
        """
        controller 必须实现：
            open_settings() -> None
            set_target_lang(code: str) -> None
            toggle_overlay() -> bool
            toggle_speech() -> bool
        """
        self.controller = controller
        self.logo_path = Path(logo_path) if logo_path else self.DEFAULT_LOGO
        self.icon = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="tray")
        self._thread.start()

    def _run(self) -> None:
        import pystray

        try:
            image = Image.open(self.logo_path)
        except Exception as e:
            log.error(f"无法加载托盘图标: {e}")
            image = Image.new("RGBA", (64, 64), (76, 139, 245, 255))

        def _safe(cb: Callable) -> Callable:
            def wrapper(icon, item):  # noqa: ARG001
                try:
                    cb()
                except Exception as e:
                    log.error(f"托盘菜单操作失败: {e}")
            return wrapper

        def lang_handler(code: str):
            def _h():
                self.controller.set_target_lang(code)
            return _h

        lang_menu = pystray.Menu(*[
            pystray.MenuItem(
                f"{l.short} {display(c)}" if (l := get_lang(c)) else c,
                _safe(lang_handler(c)),
            )
            for c in CYCLE_ORDER
        ])

        menu = pystray.Menu(
            pystray.MenuItem("显示主界面", _safe(self.controller.show_main)),
            pystray.MenuItem("打开设置", _safe(self.controller.open_settings)),
            pystray.MenuItem("目标语言", lang_menu),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("切换字幕", _safe(self.controller.toggle_overlay)),
            pystray.MenuItem("切换语音", _safe(self.controller.toggle_speech)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", _safe(self.controller.quit)),
        )

        self.icon = pystray.Icon("tscy", image, "同声传译 tscy", menu)
        self.icon.run()

    def stop(self) -> None:
        if self.icon:
            try:
                self.icon.stop()
            except Exception as e:
                log.debug(f"停止托盘图标时出错: {e}")
