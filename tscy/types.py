"""
贯穿流水线的数据结构。

设计原则：模块之间只传这两个 dataclass，不传裸 tuple。
这样以后想加字段（比如加"说话人 ID"做多人区分）不用改所有调用点。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Utterance:
    """一次语音识别的结果。"""

    text: str                    # 识别出的原文
    lang: str                    # 源语言内部代码（如 "ja"），未识别出来为空串
    lang_conf: float = 0.0       # 语种置信度 0~1
    asr_ms: int = 0              # 识别耗时（毫秒）
    audio_ms: int = 0            # 这段音频本身的时长（毫秒）
    at: float = field(default_factory=time.time)

    @property
    def duration_hint(self) -> str:
        """给日志用的简短描述。"""
        return f"[{self.lang or '??'} {self.lang_conf:.2f}] {self.text}"


@dataclass
class Translation:
    """一次翻译的最终产物，交给输出层渲染。"""

    src_text: str                # 原文
    dst_text: str                # 译文
    src_lang: str                # 源语言内部代码
    dst_lang: str                # 目标语言内部代码
    backend: str = "none"        # 实际使用的翻译后端
    cached: bool = False         # 是否命中缓存
    ms: int = 0                  # 翻译耗时
    skipped: bool = False        # True = 源语言等于目标语言，直接透传没有翻译
    low_conf: bool = False       # 源语种置信度低于阈值，译文可能不可靠

    def as_lines(self) -> tuple[str, str]:
        """返回 (原文行, 译文行)，供浮层直接渲染。"""
        return self.src_text, self.dst_text


@dataclass
class SubtitleEntry:
    """浮层上的一条字幕。"""

    src_text: str = ""
    dst_text: str = ""
    src_lang: str = ""
    dst_lang: str = ""
    kind: str = "final"          # final | partial | info
    expires_at: float = 0.0      # 过期时间戳（time.time），0 表示不自动消失
    backend: str = ""

    def is_expired(self, now: float | None = None) -> bool:
        if self.expires_at <= 0:
            return False
        return (now or time.time()) >= self.expires_at
