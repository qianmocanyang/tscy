"""
主控制窗口 —— 启动即显示，替代"启动后只有托盘图标"的困惑体验。

与设置面板同风格（暗色玻璃拟态 + 蓝紫渐变），自绘 Canvas，不用默认 widget。
提供：
  · 运行状态总览（ASR / 翻译 / 语音 / 模式）
  · 目标语言一键切换
  · 最近翻译记录滚动展示
  · 打开设置 / 退出

窗口关闭 = 隐藏到托盘（程序继续后台跑），托盘菜单可唤回。
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageTk

from ..config import Config
from ..lang import CYCLE_ORDER, display, get as get_lang
from ..log import get_logger
from ..paths import resource_dir
from .settings import _round_rect  # 复用圆角矩形

log = get_logger("ui")

# 配色与 settings.py 统一
COL_BG = "#0a0c1a"
COL_CARD = "#12162e"
COL_CARD_BORDER = "#242a55"
COL_TEXT = "#eef0fa"
COL_DIM = "#8b94b8"
COL_ACCENT1 = "#3b82f6"
COL_ACCENT2 = "#a855f7"
COL_SELECTED = "#3b82f6"
COL_HOVER = "#1f274f"
COL_DANGER = "#ef4444"
COL_GREEN = "#34d399"
COL_AMBER = "#f59e0b"
COL_RED = "#f87171"

FONT_TITLE = ("Microsoft YaHei UI", 15, "bold")
FONT_SUB = ("Microsoft YaHei UI", 9)
FONT_CARD = ("Microsoft YaHei UI", 10, "bold")
FONT_BODY = ("Microsoft YaHei UI", 10)
FONT_SMALL = ("Microsoft YaHei UI", 9)

TRANSPARENT = "#010203"

W = 500
H = 660

MODE_NAMES = {"ptt": "按住说话", "two_step": "两步式", "auto": "自动监听"}


class MainWindow:
    """主控制窗口。UI 操作必须只在主线程调用（外部线程用 open/add_record 会被内部调度）。"""

    def __init__(
        self,
        cfg: Config,
        on_set_lang: Callable[[str], None],
        on_open_settings: Callable[[], None],
        on_quit: Callable[[], None],
        status_provider: Optional[Callable[[], dict]] = None,
    ):
        self.cfg = cfg
        self.on_set_lang = on_set_lang
        self.on_open_settings = on_open_settings
        self.on_quit = on_quit
        self.status_provider = status_provider

        self.window: tk.Toplevel | None = None
        self.canvas: tk.Canvas | None = None
        self._logo_tk: ImageTk.PhotoImage | None = None
        self._records: list[tuple[str, str, str]] = []   # (src_text, dst_text, tag)
        self._hidden = False
        self._parent_tk: tk.Tk | None = None
        self._main_thread_id = threading.get_ident()
        self._click_map: dict[int, Callable] = {}

    # ---------- 生命周期 ----------

    def open(self, parent: tk.Tk) -> None:
        # 防御：非主线程调用时调度回主线程（热键/托盘线程）
        if threading.get_ident() != self._main_thread_id:
            try:
                parent.after(0, lambda: self.open(parent))
            except Exception:
                pass
            return
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            self._hidden = False
            return

        self.window = tk.Toplevel(parent)
        self.window.title("同声传译 tscy")
        self.window.overrideredirect(True)
        self.window.config(bg=TRANSPARENT)
        self.window.attributes("-transparentcolor", TRANSPARENT)
        self.window.attributes("-topmost", True)
        self.window.geometry(f"{W}x{H}+"
                             f"{parent.winfo_screenwidth() // 2 - W // 2}+"
                             f"{max(40, parent.winfo_screenheight() // 2 - H // 2)}")

        self.canvas = tk.Canvas(self.window, width=W, height=H,
                                bg=TRANSPARENT, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._bind_drag()
        self._hidden = False
        self._rebuild()

        # 状态与记录刷新
        self.window.after(800, self._tick_refresh)
        log.info("主控制窗口已打开")

    def hide(self) -> None:
        """隐藏到托盘（程序继续后台运行）。"""
        self._hidden = True
        if self.window and self.window.winfo_exists():
            self.window.withdraw()

    def show(self) -> None:
        self._hidden = False
        if self.window and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
        elif self._parent_tk is not None:
            self.open(self._parent_tk)

    def is_visible(self) -> bool:
        return (not self._hidden) and self.window is not None and self.window.winfo_exists()

    def attach_parent(self, tk_root: tk.Tk) -> None:
        self._parent_tk = tk_root

    # ---------- 外部线程安全接口 ----------

    def add_record(self, src_text: str, dst_text: str, tag: str) -> None:
        """追加一条翻译记录（任何线程可调）。"""
        def _add():
            self._records.append((src_text, dst_text, tag))
            if len(self._records) > 5:
                self._records.pop(0)
            if not self._hidden:
                self._rebuild()
        self._schedule(_add)

    def refresh(self) -> None:
        self._schedule(self._rebuild)

    # ---------- 内部 ----------

    def _schedule(self, fn) -> None:
        if threading.get_ident() == self._main_thread_id and self.window is not None:
            try:
                fn()
            except Exception as e:
                log.debug(f"主窗口操作失败: {e}")
        elif self.window is not None:
            try:
                self.window.after(0, fn)
            except Exception:
                pass

    def _tick_refresh(self) -> None:
        if self.window is None or not self.window.winfo_exists():
            return
        try:
            self._rebuild()
        except Exception:
            pass
        self.window.after(1500, self._tick_refresh)

    def _rebuild(self) -> None:
        cv = self.canvas
        if cv is None:
            return
        cv.delete("all")
        self._click_map.clear()
        self._draw()

    def _bind_drag(self) -> None:
        win = self.window
        cv = self.canvas
        state = {"x": 0, "y": 0, "drag": False}

        def on_press(e):
            state.update(x=e.x_root, y=e.y_root, drag=True)

        def on_move(e):
            if not state["drag"]:
                return
            dx, dy = e.x_root - state["x"], e.y_root - state["y"]
            state.update(x=e.x_root, y=e.y_root)
            win.geometry(f"+{win.winfo_x() + dx}+{win.winfo_y() + dy}")

        def on_release(_e=None):
            state["drag"] = False

        cv.bind("<Button-1>", on_press)
        cv.bind("<B1-Motion>", on_move)
        cv.bind("<ButtonRelease-1>", on_release)

    def _status(self) -> dict:
        if self.status_provider:
            try:
                return self.status_provider()
            except Exception:
                pass
        return {}

    def _make_button(self, x: int, y: int, w: int, h: int, text: str,
                     cmd: Callable, bg: str, fg: str, hover: str, radius: int = 11) -> None:
        cv = self.canvas
        bid = _round_rect(cv, x, y, x + w, y + h, radius, fill=bg, outline="")
        tid = cv.create_text(x + w / 2, y + h / 2, text=text, font=FONT_BODY, fill=fg)
        for iid in (bid, tid):
            self._click_map[iid] = cmd
            cv.tag_bind(iid, "<Button-1>", lambda e, cb=cmd: self._safe(cb))
            cv.tag_bind(iid, "<Enter>", lambda e: cv.itemconfig(bid, fill=hover))
            cv.tag_bind(iid, "<Leave>", lambda e: cv.itemconfig(bid, fill=bg))

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except Exception as e:
            log.error(f"主窗口点击回调异常: {e}")

    # ---------- 绘制 ----------

    def _draw(self) -> None:
        cv = self.canvas
        _round_rect(cv, 0, 0, W, H, 22, fill=COL_BG, outline=COL_CARD_BORDER, width=1)

        # ---- 标题栏 ----
        try:
            img = Image.open(resource_dir() / "assets" / "logo.png").resize((34, 34), Image.LANCZOS)
            self._logo_tk = ImageTk.PhotoImage(img)
            cv.create_image(24, 30, image=self._logo_tk, anchor="w")
        except Exception:
            pass
        cv.create_text(66, 25, text="同声传译", anchor="w", font=FONT_TITLE, fill=COL_TEXT)
        cv.create_text(66, 46, text="tscy · 游戏实时同声传译", anchor="w", font=FONT_SMALL, fill=COL_DIM)

        # 运行状态点
        st = self._status()
        recording = bool(st.get("recording"))
        asr_ready = bool(st.get("asr_ready"))
        dot_color = COL_RED if recording else (COL_GREEN if asr_ready else COL_AMBER)
        dot_txt = "录音中" if recording else ("运行中" if asr_ready else "加载中")
        cv.create_oval(W - 118, 20, W - 106, 32, fill=dot_color, outline="")
        cv.create_text(W - 98, 26, text=dot_txt, anchor="w", font=FONT_SMALL, fill=COL_DIM)

        # 最小化 / 关闭
        self._make_button(W - 82, 12, 30, 30, "—", self.hide, COL_CARD, COL_DIM, COL_HOVER, 8)
        self._make_button(W - 46, 12, 30, 30, "×", self.hide, COL_CARD, COL_DIM, COL_HOVER, 8)

        y = 74
        tgt = self.cfg.get("target_lang", "zh")
        mode = MODE_NAMES.get(self.cfg.get("mode", "two_step"), self.cfg.get("mode", ""))
        cv.create_text(
            24, y, anchor="w", font=FONT_BODY, fill=COL_DIM,
            text=f"自动识别 中 / 英 / 韩 / 日 / 俄  ·  模式：{mode}",
        )

        # ---- 目标语言 ----
        y += 26
        self._draw_lang_row(y, tgt)

        # ---- 引擎状态 ----
        y += 118
        self._draw_engine_status(y, st)

        # ---- 最近翻译记录 ----
        y += 150
        self._draw_records(y)

        # ---- 底部按钮 ----
        self._make_button(24, H - 58, 130, 38, "⚙ 打开设置", self.on_open_settings,
                          COL_CARD, COL_TEXT, COL_HOVER)
        self._make_button(W - 154, H - 58, 130, 38, "退出", self.on_quit,
                          "#7f1d1d", COL_TEXT, "#991b1b")

    def _draw_lang_row(self, y: int, current: str) -> None:
        cv = self.canvas
        _round_rect(cv, 18, y, W - 18, y + 108, 16, fill=COL_CARD, outline=COL_CARD_BORDER, width=1)
        cv.create_text(36, y + 20, text="目标语言", anchor="w", font=FONT_CARD, fill=COL_TEXT)

        n = len(CYCLE_ORDER)
        cw = (W - 76) // n
        for i, code in enumerate(CYCLE_ORDER):
            l = get_lang(code)
            selected = (code == current)
            x0 = 34 + i * cw
            x1 = x0 + cw - 8
            y0 = y + 42
            y1 = y0 + 52
            bg = COL_SELECTED if selected else COL_BG
            fg = COL_TEXT if selected else COL_DIM
            bid = _round_rect(cv, x0, y0, x1, y1, 10, fill=bg, outline=COL_CARD_BORDER, width=1)
            cv.create_text((x0 + x1) / 2, (y0 + y1) / 2 - 4, text=(l.short if l else code),
                           font=FONT_BODY, fill=fg)
            cv.create_text((x0 + x1) / 2, (y0 + y1) / 2 + 14, text=(l.name if l else ""),
                           font=("Microsoft YaHei UI", 8), fill=COL_DIM if not selected else "#dbeafe")

            def _click(c=code):
                self.on_set_lang(c)
            for iid in (bid,):
                self._click_map[iid] = _click
                cv.tag_bind(iid, "<Button-1>", lambda e, cb=_click: self._safe(cb))
                cv.tag_bind(iid, "<Enter>", lambda e, bid=bid: cv.itemconfig(bid, fill="#2563eb"))
                cv.tag_bind(iid, "<Leave>", lambda e, bid=bid, sel=selected:
                            cv.itemconfig(bid, fill=COL_SELECTED if sel else COL_BG))

    def _draw_engine_status(self, y: int, st: dict) -> None:
        cv = self.canvas
        _round_rect(cv, 18, y, W - 18, y + 142, 16, fill=COL_CARD, outline=COL_CARD_BORDER, width=1)
        cv.create_text(36, y + 20, text="引擎状态", anchor="w", font=FONT_CARD, fill=COL_TEXT)

        asr_txt = st.get("asr", "")
        tr_txt = st.get("translate", "")
        tts_txt = st.get("tts", "")
        ready = bool(st.get("asr_ready"))

        rows = [
            ("识别引擎", asr_txt or "本地 Whisper（加载中）", COL_GREEN if ready else COL_AMBER),
            ("翻译后端", tr_txt or "自动探测中", COL_DIM),
            ("语音播报", tts_txt or "edge-tts", COL_DIM),
            ("热键", self._hotkey_summary(), COL_DIM),
        ]
        ry = y + 46
        for name, val, color in rows:
            cv.create_text(36, ry, text=name, anchor="w", font=FONT_BODY, fill=COL_DIM)
            cv.create_oval(W - 40, ry - 5, W - 30, ry + 5, fill=color, outline="")
            cv.create_text(W - 44, ry, text=val, anchor="e", font=FONT_SMALL, fill=COL_TEXT)
            ry += 27

    def _hotkey_summary(self) -> str:
        hk = self.cfg.section("hotkeys")
        rec = hk.get("record", "f9")
        emt = hk.get("emit", "f10")
        mode = self.cfg.get("mode", "two_step")
        return f"F9 录音 · F10 输出 · Ctrl+Alt+P 设置" if mode == "two_step" \
            else f"{rec} 说话 · Ctrl+Alt+P 设置"

    def _draw_records(self, y: int) -> None:
        cv = self.canvas
        _round_rect(cv, 18, y, W - 18, y + 190, 16, fill=COL_CARD, outline=COL_CARD_BORDER, width=1)
        cv.create_text(36, y + 20, text="最近翻译", anchor="w", font=FONT_CARD, fill=COL_TEXT)

        if not self._records:
            cv.create_text(36, y + 70, text="还没有记录 —— 说话后这里会显示译文",
                           anchor="w", font=FONT_BODY, fill=COL_DIM)
            return

        ry = y + 48
        for src, dst, tag in self._records[-5:]:
            cv.create_text(36, ry, text=src, anchor="w", font=FONT_SMALL, fill=COL_DIM)
            cv.create_text(36, ry + 20, text=dst, anchor="w", font=FONT_BODY, fill=COL_TEXT)
            cv.create_text(W - 40, ry + 10, text=tag, anchor="e", font=("Microsoft YaHei UI", 8), fill=COL_ACCENT2)
            ry += 42
