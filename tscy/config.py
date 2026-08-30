"""
配置模块 —— 加载 / 保存 / 热重载。

设计要点：
1. 深度合并默认值：用户配置里只写想改的字段，缺失的自动补默认值。
   这样程序升级新增配置项时，老配置文件不会炸。
2. 热重载：后台线程轮询 mtime，改完 config.json 立刻生效，不用重启。
3. 密钥分离：API Key 放 config/secrets.json（已 gitignore），程序只读不合回主配置。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .log import get_logger
from .paths import app_root

log = get_logger("config")

ROOT = app_root()
CONFIG_FILE = ROOT / "config" / "config.json"
SECRETS_FILE = ROOT / "config" / "secrets.json"

DEFAULTS: dict[str, Any] = {
    # ---------- 基本行为 ----------
    # two_step = 按录音键说完 → 按输出键才翻译（最贴近"识别到语言后按键返回译文"）
    # ptt      = 按住说话，松手立刻翻（游戏里延迟最低，推荐竞技类游戏用）
    # auto     = 不用按键，检测到说话自动收音
    "mode": "two_step",
    "target_lang": "zh",           # 目标语言：zh en ko ja ru
    "source_lang": "auto",         # 源语言，一般保持 auto

    # ---------- 音频 ----------
    "audio": {
        "device": None,            # 麦克风设备 ID，None = 系统默认
        "sample_rate": 16000,      # Whisper 要求 16k
        "frame_ms": 30,            # 每帧 30ms
        "channels": 1,
    },

    # ---------- 端点检测 VAD ----------
    "vad": {
        "start_db": -40,           # 起说阈值（dBFS）
        "end_db": -45,             # 落音阈值，比 start 低 5dB 形成滞回
        "start_frames": 3,         # 连续 N 帧超阈值才算开始说话
        "silence_ms": 600,         # 静音多久判定说完（auto 模式）
        "min_ms": 300,             # 短于此丢弃
        "max_ms": 15000,           # 最长录音，防忘松手
        "trim_silence": True,      # 提交前裁掉首尾静音（提升识别率）
        "pre_roll_ms": 300,        # 保留起说前的这段缓冲，避免吃掉第一个字
    },

    # ---------- 语音识别 ----------
    "asr": {
        "backend": "whisper",      # whisper(本地，默认) / qwen(千问云端)
        "model_size": "base",      # tiny / base / small / medium / large-v3 / turbo
        "device": "auto",          # auto / cpu / cuda
        "compute_type": "auto",    # auto / int8 / int8_float16 / float16 / float32
        "beam_size": 1,            # 贪心解码，实时场景不要调大
        "vad_filter": True,
        "condition_on_previous_text": False,   # 关闭跨句上下文，防语种串味
        "min_conf": 0.5,           # 语种置信度下限，低于此值打低置信标记
        "prefer_whisper_translate": True,      # 目标=英文时直接用 Whisper 译英，跳过翻译层
        "model_dir": "models",
        "initial_prompt": "",      # 可选：给 Whisper 的提示词（填游戏术语可提升识别）
        # 模型下载端点：auto(自动探测) / official / mirror / 自定义镜像 URL
        # 国内网络下 huggingface.co 基本连不通，auto 会自动切到 hf-mirror.com
        "hf_endpoint": "auto",
        # --- 千问 ASR 参数（Key 写到 secrets.json） ---
        "qwen_key": "",
        "qwen_model": "paraformer-v2",
        "qwen_language_hints": ["zh", "en", "ja", "ko", "ru"],
    },

    # ---------- 翻译 ----------
    "translate": {
        # auto = 启动时自动探测可用的后端（推荐，国内外网络环境差异大）
        # 也可手动指定：google / mymemory / deepl / openai / none
        "backend": "auto",
        "fallback": "mymemory",    # 主后端失败时的降级后端
        "timeout": 6,              # 单后端超时（秒）
        "retries": 1,              # 单后端重试次数
        "cache_size": 2000,
        "cache_file": "cache/trans_cache.json",
        "glossary": {},            # 术语表 {"gg": "打得好"}，OpenAI 后端可用
        # --- 各后端参数（Key 请写到 secrets.json）---
        "deepl_key": "",
        "deepl_free": True,        # True = api-free.deepl.com
        "mymemory_email": "",
        "openai_key": "",
        "openai_base_url": "https://api.openai.com/v1",
        "openai_model": "gpt-4o-mini",
        # 千问 (DashScope) 的 Key 同时给 ASR 和 TTS 用
        "qwen_key": "",
    },

    # ---------- 输出 ----------
    "output": {
        "subtitle": True,          # 显示字幕浮层
        "speech": True,            # 语音播报
        "tts_engine": "edge",      # edge / pyttsx3 / qwen / off
        "tts_interrupt": True,     # 新句子打断上一条语音
        "tts_volume": 1.0,
        "tts_rate": "+0%",         # edge-tts 语速，如 "+20%"
        "tts_voice": "",           # 留空 = 按目标语言自动选音色
        "max_tts_chars": 200,      # 超过此长度不朗读（避免长篇念个没完）
        # --- 千问 TTS 参数（Key 写到 secrets.json） ---
        "qwen_key": "",
        "qwen_tts_model": "cosyvoice-v3-flash",
        "qwen_tts_voice": "longanyang",
    },

    # ---------- 浮层样式 ----------
    "overlay": {
        "x": 0.5,                  # 屏幕比例坐标 0~1
        "y": 0.86,
        "width_ratio": 0.52,       # 宽度占屏比
        "font_size": 21,           # 译文字号
        "src_font_size": 14,       # 原文字号
        "duration_ms": 5000,       # 字幕停留时长
        "show_source": True,       # 显示原文行
        "max_entries": 3,          # 同屏最多几条字幕
        "bg": "#000000",
        "bg_alpha": 0.78,          # 整体不透明度
        "fg": "#FFFFFF",
        "src_fg": "#9FB3C8",       # 原文行颜色（淡蓝灰）
        "accent": "#4FC3F7",       # 语种徽标颜色
        "warn": "#FFB74D",         # 低置信提示色
        "padding": 12,
        "radius": 14,
        "click_through": True,     # 鼠标穿透，绝不挡游戏操作
    },

    # ---------- 热键 ----------
    # 语法遵循 keyboard 库：单键 "f9"；组合键 "ctrl+alt+l"
    # 注意：PTT 录音键若想支持"按住/松开"，必须用单键（不能带 +）
    "hotkeys": {
        "record": "f9",            # PTT:按住说话；two_step/auto:按一下开始、再按停止
        "emit": "f10",             # two_step 模式：翻译并输出
        "cancel": "f11",           # 丢弃当前片段
        "cycle_target": "ctrl+alt+l",   # 循环切换目标语言
        "toggle_overlay": "ctrl+alt+o", # 显示/隐藏浮层
        "toggle_speech": "ctrl+alt+s",  # 开关语音播报
        "settings": "ctrl+alt+p",       # 打开设置面板
        "quit": "ctrl+alt+q",
    },

    # ---------- 日志 ----------
    "log": {
        "level": "INFO",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并，override 优先；返回新 dict，不改动入参。"""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _get_path(data: dict, path: str, default: Any = None) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _set_path(data: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = data
    for p in parts[:-1]:
        if not isinstance(cur.get(p), dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


class Config:
    """配置门面：点路径读写 + 文件热重载。"""

    def __init__(self, data: dict, path: Path = CONFIG_FILE):
        self._data = data
        self.path = path
        self._mtime = self._read_mtime()
        self._lock = threading.RLock()
        self._watch_thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ---------- 基础读写 ----------

    @property
    def data(self) -> dict:
        with self._lock:
            return self._data

    def get(self, path: str, default: Any = None) -> Any:
        with self._lock:
            val = _get_path(self._data, path, default)
        return val

    def set(self, path: str, value: Any, save: bool = False) -> None:
        with self._lock:
            _set_path(self._data, path, value)
        if save:
            self.save()

    def section(self, name: str) -> dict:
        with self._lock:
            v = self._data.get(name, {})
            return dict(v) if isinstance(v, dict) else {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                snapshot = dict(self._data)
            self.path.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._mtime = self._read_mtime()
        except Exception as e:
            log.error(f"保存配置失败: {e}")

    # ---------- 热重载 ----------

    def _read_mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    def reload(self) -> bool:
        """重新读盘并与默认值合并。返回是否真的变了。"""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False
        except Exception as e:
            log.error(f"配置解析失败，保持原配置: {e}")
            return False
        merged = _deep_merge(DEFAULTS, raw)
        merged = _apply_secrets(merged)
        with self._lock:
            changed = merged != self._data
            self._data = merged
        self._mtime = self._read_mtime()
        if changed:
            log.info("检测到配置变更，已热重载")
        return changed

    def start_watch(self, on_change: Callable[[], None], interval: float = 1.0) -> None:
        """后台轮询文件 mtime，变了就回调。"""
        if self._watch_thread and self._watch_thread.is_alive():
            return

        def _loop():
            while not self._stop.wait(interval):
                try:
                    mt = self._read_mtime()
                    if mt and abs(mt - self._mtime) > 1e-6:
                        if self.reload():
                            on_change()
                except Exception as e:
                    log.debug(f"配置监听异常: {e}")

        self._watch_thread = threading.Thread(target=_loop, daemon=True, name="cfg-watch")
        self._watch_thread.start()

    def stop_watch(self) -> None:
        self._stop.set()


def _apply_secrets(data: dict) -> dict:
    """把 secrets.json 里的密钥注入到配置里（不写回 config.json）。"""
    try:
        sec = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return data
    except Exception as e:
        log.warning(f"secrets.json 解析失败: {e}")
        return data

    mapping = {
        "deepl_key": "translate.deepl_key",
        "openai_key": "translate.openai_key",
        "openai_base_url": "translate.openai_base_url",
        "openai_model": "translate.openai_model",
        "mymemory_email": "translate.mymemory_email",
        "qwen_key": "translate.qwen_key",
    }
    for sec_key, cfg_path in mapping.items():
        if sec.get(sec_key):
            _set_path(data, cfg_path, sec[sec_key])

    # 千问 Key 同时注入 ASR 和 TTS 配置
    if data.get("translate", {}).get("qwen_key"):
        qk = data["translate"]["qwen_key"]
        _set_path(data, "asr.qwen_key", qk)
        _set_path(data, "output.qwen_key", qk)
    return data


def load_config(path: Path = CONFIG_FILE, overrides: dict | None = None) -> Config:
    """
    加载配置：默认值 ← 用户文件 ← 命令行覆盖 ← 密钥文件
    文件不存在时自动用默认值生成一份，方便用户照着改。
    """
    raw: dict = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log.error(f"{path} 解析失败，回退默认配置: {e}")
            raw = {}
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(f"未找到配置文件，已生成默认配置: {path}")

    merged = _deep_merge(DEFAULTS, raw)
    if overrides:
        merged = _deep_merge(merged, overrides)
    merged = _apply_secrets(merged)
    return Config(merged, path)
