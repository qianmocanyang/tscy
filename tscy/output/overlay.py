"""
游戏字幕浮层 —— 置顶、透明、鼠标穿透。

三项关键技术：
1. 完全透明背景
   tkinter 没有真正的每像素 alpha，但可以用 -transparentcolor 把**某一种特定颜色**
   变成完全透明。这里选了一个几乎不可能出现在内容里的颜色 #010203 作为"透明色"，
   窗口涂满它 → 除内容外全部透视到游戏画面。

2. 鼠标穿透（click-through）
   光透明还不够：普通透明窗口仍然会吃掉鼠标点击，游戏里会挡住操作。
   所以再用 Win32 API 给窗口加上 WS_EX_TRANSPARENT 样式 —— 鼠标事件直接穿透到下层窗口。
   这是浮层能真正"贴"在游戏上而不干扰操作的关键。

3. 线程安全
   tkinter 不是线程安全的，所有 UI 操作必须回到主线程。
   这里的做法是：任何线程调用 show() 都通过 root.after(0, ...) 投递到主线程执行。
   ASR/翻译线程因此可以放心调用，不用担心随机崩溃。
"""

from __future__ import annotations

import math
import threading
import time
import tkinter as tk
from pathlib import Path

from ..lang import get as get_lang
from ..log import get_logger
from ..types import SubtitleEntry, Translation

log = get_logger("overlay")

# 被当作"完全透明"处理的颜色，内容里不要用这个颜色
TRANSPARENT_KEY = "#010203"

# Win32 常量
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080


def _round_rect_points(x0: float, y0: float, x1: float, y1: float, r: float, steps: int = 7):
    """生成圆角矩形的多边形顶点（tkinter 没有原生圆角矩形，用多边形逼近）。"""
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    pts: list[float] = []
    corners = [
        (x0 + r, y0 + r, math.pi, 1.5 * math.pi),          # 左上
        (x1 - r, y0 + r, 1.5 * math.pi, 2.0 * math.pi),    # 右上
        (x1 - r, y1 - r, 0.0, 0.5 * math.pi),              # 右下
        (x0 + r, y1 - r, 0.5 * math.pi, math.pi),          # 左下
    ]
    for cx, cy, a0, a1 in corners:
        for i in range(steps + 1):
            a = a0 + (a1 - a0) * (i / steps)
            pts.append(cx + r * math.cos(a))
            pts.append(cy + r * math.sin(a))
    return pts


class Overlay:
    """字幕浮层。必须在主线程创建；线程安全的 show() 供其它线程调用。"""

    def __init__(self, cfg: dict | None = None):
        self.cfg = dict(cfg or {})
        self.root: tk.Tk | None = None
        self.canvas: tk.Canvas | None = None
        self._entries: list[SubtitleEntry] = []
        self._lock = threading.Lock()
        self._visible = True
        self._running = False
        self._main_thread_id = threading.get_ident()

    # ---------- 生命周期 ----------

    def start(self) -> None:
        """创建窗口。必须在主线程调用。"""
        if self.root is not None:
            return

        self.root = tk.Tk()
        self.root.title("tscy-overlay")
        self.root.overrideredirect(True)          # 无边框无标题栏
        self.root.attributes("-topmost", True)    # 永远置顶
        self.root.config(bg=TRANSPARENT_KEY)
        self.root.attributes("-transparentcolor", TRANSPARENT_KEY)

        self.canvas = tk.Canvas(
            self.root, bg=TRANSPARENT_KEY, highlightthickness=0, bd=0
        )
        self.canvas.pack(fill="both", expand=True)

        self._apply_window_style()
        self._apply_alpha()

        self._running = True
        # 不 withdraw：overlay 的 Tk 是设置面板/主窗口的父窗口，
        # withdraw 父窗口在某些情况下会导致子窗口也跟着消失（UI 一闪而过）。
        # 改为缩到 1x1 全透明，等有字幕内容时再展开。
        self._hide()
        self.root.after(120, self._tick)
        log.info("字幕浮层已就绪")

    def _hide(self) -> None:
        """把浮层缩成 1x1 透明点，但保持窗口存在。"""
        if self.root is None:
            return
        try:
            self.root.geometry("1x1+0+0")
            self.canvas.delete("all")
        except Exception as e:
            log.debug(f"隐藏浮层失败: {e}")

    def run(self) -> None:
        """进入 tkinter 主循环（阻塞，必须主线程）。"""
        if self.root is None:
            self.start()
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass

    def stop(self) -> None:
        self._running = False
        if self.root is not None:
            try:
                self.root.after(0, self.root.quit)
            except Exception:
                pass

    # ---------- 对外接口（线程安全） ----------

    def show_translation(self, tr: Translation) -> None:
        cfg = self.cfg
        dur = int(cfg.get("duration_ms", 5000)) / 1000.0
        entry = SubtitleEntry(
            src_text=tr.src_text if cfg.get("show_source", True) else "",
            dst_text=tr.dst_text or tr.src_text,
            src_lang=tr.src_lang,
            dst_lang=tr.dst_lang,
            kind="final",
            expires_at=time.time() + dur,
            backend=tr.backend,
        )
        self._push(entry)

    def show_info(self, text: str, lang: str = "", ttl_ms: int = 2200) -> None:
        self._push(SubtitleEntry(
            src_text="", dst_text=text, src_lang=lang, dst_lang=lang,
            kind="info", expires_at=time.time() + ttl_ms / 1000.0,
        ))

    def show_partial(self, text: str, lang: str = "") -> None:
        """识别出的原文（两步模式下先给玩家看，确认后再翻译）。"""
        self._push(SubtitleEntry(
            src_text="", dst_text=text, src_lang=lang, dst_lang=lang,
            kind="partial", expires_at=0,
        ))

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
        self._schedule(self._redraw)

    def toggle(self) -> None:
        self._visible = not self._visible
        self._schedule(self._redraw)
        return self._visible

    def apply_config(self, cfg: dict) -> None:
        self.cfg = dict(cfg or {})
        self._schedule(self._redraw)

    # ---------- 内部 ----------

    def _push(self, entry: SubtitleEntry) -> None:
        max_entries = int(self.cfg.get("max_entries", 3))
        with self._lock:
            # partial / info 类只保留最新一条，不堆叠
            if entry.kind in ("partial", "info"):
                self._entries = [e for e in self._entries if e.kind != entry.kind]
            self._entries.append(entry)
            while len(self._entries) > max_entries:
                self._entries.pop(0)
        self._schedule(self._redraw)

    def _schedule(self, fn) -> None:
        """把 UI 操作投递到主线程执行（tkinter 线程安全铁律）。"""
        if self.root is None:
            return
        if threading.get_ident() == self._main_thread_id:
            fn()
        else:
            try:
                self.root.after(0, fn)
            except Exception:
                pass

    def _tick(self) -> None:
        """每 120ms 检查一次过期字幕并重绘。"""
        if not self._running or self.root is None:
            return
        now = time.time()
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if not e.is_expired(now)]
            changed = len(self._entries) != before
        if changed:
            self._redraw()
        self.root.after(120, self._tick)

    # ---------- 渲染 ----------

    def _apply_alpha(self) -> None:
        if self.root is None:
            return
        try:
            self.root.attributes("-alpha", float(self.cfg.get("bg_alpha", 0.78)))
        except Exception as e:
            log.debug(f"设置透明度失败: {e}")

    def _apply_window_style(self) -> None:
        """加 WS_EX_TRANSPARENT 实现鼠标穿透（仅 Windows）。"""
        if self.root is None or not self.cfg.get("click_through", True):
            return
        if threading.get_ident() != self._main_thread_id:
            return
        try:
            import ctypes
            from ctypes import wintypes

            u32 = ctypes.windll.user32
            gwl = ctypes.windll.user32.GetWindowLongW
            swl = ctypes.windll.user32.SetWindowLongW
            gwl.restype = wintypes.LONG
            swl.restype = wintypes.LONG

            hwnd = self.root.winfo_id()
            for h in (hwnd, u32.GetParent(hwnd)):
                if not h:
                    continue
                style = gwl(h, GWL_EXSTYLE)
                swl(h, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)
            log.debug("已启用鼠标穿透")
        except Exception as e:
            log.warning(f"启用鼠标穿透失败（不影响字幕，但会挡鼠标）: {e}")

    def _font(self, size: int, weight: str = "normal", lang: str = ""):
        fam = "Microsoft YaHei UI"
        l = get_lang(lang)
        if l:
            fam = l.font
        return (fam, size, weight)

    def _redraw(self) -> None:
        if self.root is None or self.canvas is None:
            return

        cv = self.canvas
        cv.delete("all")

        with self._lock:
            entries = list(self._entries)

        if not self._visible or not entries:
            self._hide()
            return

        self.root.deiconify()
        self.root.attributes("-topmost", True)

        cfg = self.cfg
        pad = int(cfg.get("padding", 12))
        radius = int(cfg.get("radius", 14))
        fg = cfg.get("fg", "#FFFFFF")
        src_fg = cfg.get("src_fg", "#9FB3C8")
        accent = cfg.get("accent", "#4FC3F7")
        warn = cfg.get("warn", "#FFB74D")
        bg = cfg.get("bg", "#000000")
        font_size = int(cfg.get("font_size", 21))
        src_size = int(cfg.get("src_font_size", 14))

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        win_w = max(320, int(sw * float(cfg.get("width_ratio", 0.52))))

        # 先量出每张卡片需要多高，再决定窗口高度
        cards: list[dict] = []
        total_h = 0
        gap = 8

        for e in entries:
            is_info = e.kind == "info"
            is_partial = e.kind == "partial"
            inner_w = win_w - pad * 2 - 46   # 左侧留给语种徽标

            main_size = font_size if not is_info else font_size - 3
            main_item = cv.create_text(
                0, 0, anchor="nw", width=inner_w,
                text=e.dst_text, font=self._font(main_size, "bold", e.dst_lang),
                fill=warn if is_partial else (accent if is_info else fg),
            )
            main_bbox = cv.bbox(main_item)
            main_h = (main_bbox[3] - main_bbox[1]) if main_bbox else main_size + 6

            src_h = 0
            src_item = None
            if e.src_text and cfg.get("show_source", True) and not is_info:
                src_item = cv.create_text(
                    0, 0, anchor="nw", width=inner_w,
                    text=e.src_text, font=self._font(src_size, "normal", e.src_lang),
                    fill=src_fg,
                )
                sb = cv.bbox(src_item)
                src_h = (sb[3] - sb[1]) + 4 if sb else src_size + 6

            card_h = pad * 2 + main_h + src_h
            cards.append({
                "entry": e, "main_item": main_item, "src_item": src_item,
                "main_h": main_h, "src_h": src_h, "h": card_h,
                "info": is_info,
            })
            total_h += card_h + gap

        total_h = max(0, total_h - gap)
        win_h = total_h + 4
        cv.config(width=win_w, height=win_h)

        # 定位：cfg 的 x/y 是屏幕比例，y 作为底边锚点（字幕从下往上长，不会顶到屏幕外）
        pos_x = int(sw * float(cfg.get("x", 0.5)) - win_w / 2)
        pos_y = int(sh * float(cfg.get("y", 0.86)) - win_h)
        pos_x = max(0, min(pos_x, sw - win_w))
        pos_y = max(0, min(pos_y, sh - win_h))
        self.root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")

        # 逐张绘制：背景 → 文字提到最上层
        y = 2
        for c in cards:
            e: SubtitleEntry = c["entry"]

            poly = cv.create_polygon(
                _round_rect_points(1, y, win_w - 1, y + c["h"], radius),
                fill=bg, outline="",
            )
            cv.tag_lower(poly)

            # 语种徽标（竖排小色块 + 短标签）
            badge = (get_lang(e.dst_lang).short if get_lang(e.dst_lang) else "?") if not c["info"] else "•"
            badge_color = accent if not c["info"] else accent
            cv.create_text(
                pad, y + pad + 2, anchor="nw",
                text=badge, font=self._font(src_size - 1, "bold"), fill=badge_color,
            )

            text_x = pad + 30
            # 文字描边：先画一层偏移的黑字，再画主色，保证在亮色画面上也看得清
            for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                shadow = cv.create_text(
                    text_x + dx, y + pad + dy, anchor="nw",
                    width=win_w - text_x - pad,
                    text=e.dst_text,
                    font=self._font(font_size if not c["info"] else font_size - 3, "bold", e.dst_lang),
                    fill="#000000",
                )
                cv.tag_raise(shadow)
            cv.delete(c["main_item"])
            real = cv.create_text(
                text_x, y + pad, anchor="nw",
                width=win_w - text_x - pad,
                text=e.dst_text,
                font=self._font(font_size if not c["info"] else font_size - 3, "bold", e.dst_lang),
                fill=(warn if e.kind == "partial" else (accent if c["info"] else fg)),
            )
            cv.tag_raise(real)

            if c["src_item"] is not None:
                cv.delete(c["src_item"])
                cv.create_text(
                    text_x, y + pad + c["main_h"] + 4, anchor="nw",
                    width=win_w - text_x - pad,
                    text=e.src_text,
                    font=self._font(src_size, "normal", e.src_lang),
                    fill=src_fg,
                )

            y += c["h"] + gap
