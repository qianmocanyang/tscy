"""UI 冒烟测试（临时）：主窗口 + 跨线程设置面板 + 记录追加。跑完即删。"""
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import tkinter as tk

from tscy.config import load_config
from tscy.ui.main_window import MainWindow
from tscy.ui.settings import SettingsWindow

cfg = load_config()

root = tk.Tk()
root.overrideredirect(True)
root.attributes("-transparentcolor", "#010203")
root.geometry("1x1+0+0")

results = []

mw = MainWindow(
    cfg,
    on_set_lang=lambda c: results.append(f"set_lang={c}"),
    on_open_settings=lambda: results.append("open_settings"),
    on_quit=lambda: root.after(300, root.quit),
)
sw = SettingsWindow(cfg, on_change=lambda: None)

mw.attach_parent(root)
mw.open(root)
print("[1] 主窗口已打开:", bool(mw.window and mw.window.winfo_exists()))


def from_thread():
    """模拟热键/托盘线程：跨线程打开窗口（依赖窗口内部的防御调度）。"""
    time.sleep(0.8)
    sw.open(root)   # 直接跨线程调用，内部应自动调度回主线程
    time.sleep(0.6)
    visible = bool(sw.window and sw.window.winfo_viewable())
    print("[2] 跨线程打开设置面板, 600ms 后仍可见:", visible)
    results.append(f"settings_visible={visible}")

    time.sleep(0.3)
    mw.add_record("Hello, nice play!", "打得漂亮！", "mymemory")
    mw.add_record("안녕하세요, 잘 부탁드립니다", "你好，请多关照", "cache")
    mw.add_record("Привет", "你好", "google")
    print("[3] 主窗口追加 3 条记录")
    time.sleep(0.5)
    root.after(0, root.quit)


threading.Thread(target=from_thread, daemon=True).start()
root.after(6000, root.quit)
root.mainloop()

main_alive = bool(mw.window and mw.window.winfo_exists())
print("[4] 主循环退出时主窗口存在:", main_alive)
print("[5] 主窗口记录数:", len(mw._records))
print("RESULTS:", results)
ok = main_alive and len(mw._records) == 3 and all("settings_visible=True" in r for r in results)
print("UI_SMOKE:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
