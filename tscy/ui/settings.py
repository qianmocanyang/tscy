"""
设置面板 —— 全部用 tkinter Canvas 自绘，不用默认 widget，避免"千窗一面"。

视觉方向：深色玻璃拟态 + 蓝紫渐变强调色，与 logo 的色调一致。
"""

from __future__ import annotations

import math
import tkinter as tk
from pathlib import Path
from typing import Callable

from PIL import Image, ImageTk

from ..config import Config
from ..lang import CYCLE_ORDER, display
from ..log import get_logger
from ..paths import resource_dir

log = get_logger("ui")

# ---------- 配色 ----------
COL_BG = "#0a0c1a"           # 窗口底色
COL_CARD = "#12162e"         # 卡片底色
COL_CARD_BORDER = "#242a55"  # 卡片边框
COL_TEXT = "#eef0fa"         # 主文字
COL_DIM = "#8b94b8"          # 次要文字
COL_ACCENT1 = "#3b82f6"      # 蓝
COL_ACCENT2 = "#a855f7"      # 紫
COL_SELECTED = "#3b82f6"     # 选中态
COL_HOVER = "#1f274f"        # hover 背景
COL_DANGER = "#ef4444"       # 危险/退出

FONT_TITLE = ("Microsoft YaHei UI", 16, "bold")
FONT_CARD_TITLE = ("Microsoft YaHei UI", 11, "bold")
FONT_BODY = ("Microsoft YaHei UI", 10)
FONT_SMALL = ("Microsoft YaHei UI", 9)

TRANSPARENT = "#010203"


def _round_rect(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float,
                r: float, **kwargs) -> int:
    """在 Canvas 上画一个圆角矩形。"""
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    pts: list[float] = []
    corners = [
        (x0 + r, y0 + r, math.pi, 1.5 * math.pi),
        (x1 - r, y0 + r, 1.5 * math.pi, 2.0 * math.pi),
        (x1 - r, y1 - r, 0.0, 0.5 * math.pi),
        (x0 + r, y1 - r, 0.5 * math.pi, math.pi),
    ]
    for cx, cy, a0, a1 in corners:
        for i in range(8):
            a = a0 + (a1 - a0) * (i / 7)
            pts.append(cx + r * math.cos(a))
            pts.append(cy + r * math.sin(a))
    return canvas.create_polygon(pts, smooth=0, **kwargs)


class SettingsWindow:
    """高颜值设置面板。"""

    WIDTH = 620
    HEIGHT = 760

    def __init__(self, cfg: Config, on_change: Callable[[], None] | None = None):
        self.cfg = cfg
        self.on_change = on_change
        self.window: tk.Toplevel | None = None
        self.canvas: tk.Canvas | None = None
        self._logo_tk: ImageTk.PhotoImage | None = None
        self._visible = False
        self._mouse = {"x": 0, "y": 0, "dragging": False}
        # 保存控件 id 到回调的映射
        self._click_map: dict[int, Callable] = {}
        self._hover_map: dict[int, tuple[int, str]] = {}  # id -> (bg_id, normal_fill)

    # ---------- 生命周期 ----------

    def open(self, parent: tk.Tk) -> None:
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            self.window.focus_force()
            return

        self.window = tk.Toplevel(parent)
        self.window.title("tscy 设置")
        self.window.overrideredirect(True)
        self.window.config(bg=TRANSPARENT)
        self.window.attributes("-transparentcolor", TRANSPARENT)
        self.window.attributes("-topmost", True)
        self.window.geometry(f"{self.WIDTH}x{self.HEIGHT}+"
                             f"{parent.winfo_screenwidth() // 2 - self.WIDTH // 2}+"
                             f"{parent.winfo_screenheight() // 2 - self.HEIGHT // 2}")

        self.canvas = tk.Canvas(self.window, width=self.WIDTH, height=self.HEIGHT,
                                bg=TRANSPARENT, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self._bind_drag()
        self._build()
        self._visible = True

    def close(self) -> None:
        if self.window and self.window.winfo_exists():
            self.window.destroy()
        self.window = None
        self.canvas = None
        self._visible = False

    def is_visible(self) -> bool:
        return self._visible and self.window is not None and self.window.winfo_exists()

    # ---------- 绘制 ----------

    def _build(self) -> None:
        cv = self.canvas
        assert cv is not None

        # 背景底
        _round_rect(cv, 0, 0, self.WIDTH, self.HEIGHT, 24,
                    fill=COL_BG, outline=COL_CARD_BORDER, width=1)

        # 标题栏（可拖拽区）
        cv.create_rectangle(0, 0, self.WIDTH, 64, fill=COL_BG, outline="")
        # Logo
        try:
            img = Image.open(resource_dir() / "assets" / "logo.png")
            img = img.resize((36, 36), Image.LANCZOS)
            self._logo_tk = ImageTk.PhotoImage(img)
            cv.create_image(26, 32, image=self._logo_tk, anchor="w")
        except Exception:
            pass
        cv.create_text(72, 32, text="同声传译", anchor="w", font=FONT_TITLE, fill=COL_TEXT)
        cv.create_text(72, 50, text="tscy settings", anchor="w", font=FONT_SMALL, fill=COL_DIM)

        # 关闭按钮
        self._make_button(self.WIDTH - 44, 18, 26, 26, "×",
                          cmd=self.close, fg=COL_DIM, hover_fg=COL_TEXT)

        y = 84
        gap = 14

        # ASR 引擎
        y = self._draw_group(y, "识别引擎",
                             [("whisper", "本地 Whisper（默认，速度优先）"),
                              ("qwen", "千问云端（需 Key，识别率更高）")],
                             self.cfg.get("asr.backend", "whisper"),
                             lambda v: self.cfg.set("asr.backend", v))
        y += gap

        # 翻译后端
        y = self._draw_group(y, "翻译后端",
                             [("auto", "自动探测"),
                              ("mymemory", "MyMemory（免费）"),
                              ("google", "Google"),
                              ("deepl", "DeepL（需 Key）"),
                              ("openai", "OpenAI 兼容（需 Key）"),
                              ("none", "不翻译")],
                             self.cfg.get("translate.backend", "auto"),
                             lambda v: self.cfg.set("translate.backend", v))
        y += gap

        # TTS 引擎
        y = self._draw_group(y, "语音播报",
                             [("edge", "Edge 在线语音（默认）"),
                              ("qwen", "千问 CosyVoice（需 Key）"),
                              ("pyttsx3", "系统离线语音"),
                              ("off", "关闭")],
                             self.cfg.get("output.tts_engine", "edge"),
                             lambda v: self.cfg.set("output.tts_engine", v))
        y += gap

        # 目标语言
        y = self._draw_lang_row(y)
        y += gap

        # 触发模式
        y = self._draw_group(y, "触发模式",
                             [("two_step", "两步式（按 F9 录音，F10 输出）"),
                              ("ptt", "按住说话（松手即翻）"),
                              ("auto", "自动监听（VAD 断句）")],
                             self.cfg.get("mode", "two_step"),
                             lambda v: self.cfg.set("mode", v))
        y += gap

        # 开关 + 模型
        y = self._draw_toggles_and_model(y)
        y += gap

        # 底部保存/取消
        self._make_button(self.WIDTH // 2 - 110, self.HEIGHT - 58, 100, 36,
                          "取消", cmd=self.close,
                          bg=COL_CARD, fg=COL_TEXT, hover=COL_HOVER, radius=10)
        self._make_button(self.WIDTH // 2 + 10, self.HEIGHT - 58, 100, 36,
                          "保存", cmd=self._save,
                          bg=COL_SELECTED, fg=COL_TEXT, hover="#2563eb", radius=10)

    def _draw_group(self, y: int, title: str, options: list[tuple[str, str]],
                    current: str, setter: Callable[[str], None]) -> int:
        """绘制一个单选卡片组，返回下一个 y 坐标。"""
        cv = self.canvas
        card_h = 52 + len(options) * 38
        card_id = _round_rect(cv, 18, y, self.WIDTH - 18, y + card_h, 16,
                              fill=COL_CARD, outline=COL_CARD_BORDER, width=1)
        cv.create_text(36, y + 22, text=title, anchor="w", font=FONT_CARD_TITLE, fill=COL_TEXT)

        yy = y + 46
        for value, label in options:
            selected = (value == current)
            # 选项背景（hover 用）
            bg_id = _round_rect(cv, 30, yy, self.WIDTH - 30, yy + 30, 8,
                                fill=COL_CARD if not selected else "#1e274f",
                                outline="" if not selected else COL_SELECTED, width=1)
            # 单选点
            dot_color = COL_SELECTED if selected else COL_CARD_BORDER
            dot_id = cv.create_oval(44, yy + 10, 56, yy + 22, fill=dot_color, outline=COL_TEXT if selected else "")
            if selected:
                cv.create_oval(47, yy + 13, 53, yy + 19, fill=COL_TEXT, outline="")
            # 文字
            txt_id = cv.create_text(68, yy + 16, text=label, anchor="w",
                                    font=FONT_BODY, fill=COL_TEXT if selected else COL_DIM)

            def make_click(v=value, setter=setter):
                def _click():
                    setter(v)
                    self._refresh()
                return _click

            for item_id in (bg_id, dot_id, txt_id):
                self._click_map[item_id] = make_click()
                self._hover_map[bg_id] = (bg_id, COL_CARD)
                cv.tag_bind(item_id, "<Button-1>", lambda e, cb=make_click(): cb())
                cv.tag_bind(item_id, "<Enter>", lambda e, bid=bg_id: cv.itemconfig(bid, fill=COL_HOVER))
                cv.tag_bind(item_id, "<Leave>", lambda e, bid=bg_id, val=value, cur=current:
                            cv.itemconfig(bid, fill=COL_SELECTED if val == cur else COL_CARD))
            yy += 36
        return y + card_h + 4

    def _draw_lang_row(self, y: int) -> int:
        cv = self.canvas
        current = self.cfg.get("target_lang", "zh")
        card_h = 100
        _round_rect(cv, 18, y, self.WIDTH - 18, y + card_h, 16,
                    fill=COL_CARD, outline=COL_CARD_BORDER, width=1)
        cv.create_text(36, y + 22, text="目标语言", anchor="w", font=FONT_CARD_TITLE, fill=COL_TEXT)

        codes = CYCLE_ORDER
        w = (self.WIDTH - 80) // len(codes)
        for i, code in enumerate(codes):
            selected = (code == current)
            x0 = 36 + i * w
            x1 = x0 + w - 8
            y0 = y + 44
            y1 = y0 + 40
            bg = COL_SELECTED if selected else COL_BG
            fg = COL_TEXT if selected else COL_DIM
            bid = _round_rect(cv, x0, y0, x1, y1, 10, fill=bg, outline=COL_CARD_BORDER, width=1)
            l = display(code)
            tid = cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=l,
                                 font=FONT_BODY, fill=fg)

            def make_click(c=code):
                def _click():
                    self.cfg.set("target_lang", c)
                    self._refresh()
                return _click

            for item_id in (bid, tid):
                self._click_map[item_id] = make_click()
                cv.tag_bind(item_id, "<Button-1>", lambda e, cb=make_click(): cb())
                cv.tag_bind(item_id, "<Enter>", lambda e, bid=bid, sel=selected:
                            cv.itemconfig(bid, fill="#2563eb" if sel else COL_HOVER))
                cv.tag_bind(item_id, "<Leave>", lambda e, bid=bid, sel=selected:
                            cv.itemconfig(bid, fill=COL_SELECTED if sel else COL_BG))
        return y + card_h + 4

    def _draw_toggles_and_model(self, y: int) -> int:
        cv = self.canvas
        card_h = 140
        _round_rect(cv, 18, y, self.WIDTH - 18, y + card_h, 16,
                    fill=COL_CARD, outline=COL_CARD_BORDER, width=1)
        cv.create_text(36, y + 22, text="功能开关", anchor="w", font=FONT_CARD_TITLE, fill=COL_TEXT)

        # 字幕开关
        sub = bool(self.cfg.get("output.subtitle", True))
        self._draw_toggle(36, y + 52, "显示字幕浮层", sub,
                          lambda v: self.cfg.set("output.subtitle", v))

        # 语音开关
        spk = bool(self.cfg.get("output.speech", True))
        self._draw_toggle(36, y + 90, "语音播报", spk,
                          lambda v: self.cfg.set("output.speech", v))

        # Whisper 模型大小
        model = self.cfg.get("asr.model_size", "base")
        cv.create_text(self.WIDTH // 2 + 20, y + 52, text="Whisper 模型", anchor="w",
                       font=FONT_BODY, fill=COL_DIM)
        models = ["tiny", "base", "small", "medium"]
        mw = 58
        for i, m in enumerate(models):
            selected = (m == model)
            x0 = self.WIDTH // 2 + 20 + i * (mw + 8)
            bid = _round_rect(cv, x0, y + 70, x0 + mw, y + 98, 8,
                              fill=COL_SELECTED if selected else COL_BG,
                              outline=COL_CARD_BORDER, width=1)
            tid = cv.create_text(x0 + mw / 2, y + 84, text=m, font=FONT_BODY,
                                 fill=COL_TEXT if selected else COL_DIM)

            def make_click(mm=m):
                def _click():
                    self.cfg.set("asr.model_size", mm)
                    self._refresh()
                return _click

            for item_id in (bid, tid):
                self._click_map[item_id] = make_click()
                cv.tag_bind(item_id, "<Button-1>", lambda e, cb=make_click(): cb())

        return y + card_h + 4

    def _draw_toggle(self, x: int, y: int, text: str, value: bool,
                     setter: Callable[[bool], None]) -> None:
        cv = self.canvas
        cv.create_text(x, y, text=text, anchor="w", font=FONT_BODY, fill=COL_TEXT)
        # 轨道
        track_w, track_h = 40, 20
        tx = self.WIDTH - 50 - track_w
        ty = y - track_h // 2
        on = value
        track_color = COL_SELECTED if on else COL_CARD_BORDER
        track_id = _round_rect(cv, tx, ty, tx + track_w, ty + track_h, track_h / 2,
                               fill=track_color, outline="")
        # 圆点
        dot_r = 7
        dot_x = tx + track_w - dot_r - 3 if on else tx + dot_r + 3
        dot_id = cv.create_oval(dot_x - dot_r, ty + track_h / 2 - dot_r,
                                dot_x + dot_r, ty + track_h / 2 + dot_r,
                                fill=COL_TEXT, outline="")

        def click():
            setter(not value)
            self._refresh()

        for item_id in (track_id, dot_id):
            self._click_map[item_id] = click
            cv.tag_bind(item_id, "<Button-1>", lambda e, cb=click: cb())

    def _make_button(self, x: int, y: int, w: int, h: int, text: str,
                     cmd: Callable, bg: str, fg: str, hover: str,
                     radius: int = 12) -> None:
        cv = self.canvas
        bid = _round_rect(cv, x, y, x + w, y + h, radius,
                          fill=bg, outline="")
        tid = cv.create_text(x + w / 2, y + h / 2, text=text,
                             font=FONT_BODY, fill=fg)

        def on_enter(e=None):  # noqa: ARG001
            cv.itemconfig(bid, fill=hover)

        def on_leave(e=None):  # noqa: ARG001
            cv.itemconfig(bid, fill=bg)

        for item_id in (bid, tid):
            self._click_map[item_id] = cmd
            cv.tag_bind(item_id, "<Button-1>", lambda e, cb=cmd: cb())
            cv.tag_bind(item_id, "<Enter>", lambda e: on_enter())
            cv.tag_bind(item_id, "<Leave>", lambda e: on_leave())

    # ---------- 交互 ----------

    def _bind_drag(self) -> None:
        win = self.window
        cv = self.canvas

        def on_press(event):
            self._mouse["x"] = event.x_root
            self._mouse["y"] = event.y_root
            self._mouse["dragging"] = True

        def on_move(event):
            if not self._mouse["dragging"]:
                return
            dx = event.x_root - self._mouse["x"]
            dy = event.y_root - self._mouse["y"]
            self._mouse["x"] = event.x_root
            self._mouse["y"] = event.y_root
            win.geometry(f"+{win.winfo_x() + dx}+{win.winfo_y() + dy}")

        def on_release(_event=None):
            self._mouse["dragging"] = False

        cv.bind("<Button-1>", on_press)
        cv.bind("<B1-Motion>", on_move)
        cv.bind("<ButtonRelease-1>", on_release)

    def _refresh(self) -> None:
        """配置项变了，重绘整个面板。"""
        if self.canvas:
            self.canvas.delete("all")
            self._click_map.clear()
            self._hover_map.clear()
            self._build()

    def _save(self) -> None:
        """保存配置并触发外部重载。"""
        try:
            self.cfg.save()
            if self.on_change:
                self.on_change()
            self.close()
            log.info("设置已保存")
        except Exception as e:
            log.error(f"保存设置失败: {e}")
