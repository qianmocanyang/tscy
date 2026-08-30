"""
翻译后端抽象基类。

所有后端只需要实现 _translate(text, src, tgt) 这一个方法；
长文本切分、重试、超时这些通用逻辑由基类的 translate() 统一处理。

这样加一个新翻译服务 = 新建一个文件 + 实现 20 行代码，
不用在每个后端里重复写切分和重试。
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod

import requests

from ..log import get_logger

log = get_logger("translate")

# 句子边界：优先在这些标点后切分，避免把一句话从中间劈开
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")
DEFAULT_CHUNK = 450


class TranslateError(Exception):
    """翻译失败，交给上层降级处理。"""


class BaseTranslator(ABC):
    """翻译后端基类。"""

    name: str = "base"
    # 单次请求的最大字符数，子类按服务端限制覆盖
    max_chars: int = DEFAULT_CHUNK
    # 该后端支持的源/目标语言内部代码；空列表表示全部支持
    supported: list[str] = []

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.timeout: float = float(cfg.get("timeout", 6))
        self.retries: int = int(cfg.get("retries", 1))
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
            )
        })

    # ---------- 子类必须实现 ----------

    @abstractmethod
    def _translate(self, text: str, src: str, tgt: str) -> str:
        """翻译一段文本（已保证不超过 max_chars）。失败请 raise。"""

    # ---------- 可选覆盖 ----------

    def available(self) -> bool:
        """后端是否可用（比如 Key 没填就返回 False）。"""
        return True

    def supports(self, lang: str) -> bool:
        return (not self.supported) or (lang in self.supported)

    # ---------- 通用逻辑 ----------

    def translate(self, text: str, src: str, tgt: str) -> str:
        """
        对外统一入口：切分 → 逐块翻译（带重试）→ 拼接。

        为什么要切分：
            Google 的 gtx 端点对超长 query 会静默截断；
            DeepL 单次也有长度上限。切成句子块再拼，长句才不会丢尾巴。
        """
        text = (text or "").strip()
        if not text:
            return ""

        if len(text) <= self.max_chars:
            return self._with_retry(text, src, tgt)

        chunks = self._split(text)
        out: list[str] = []
        for ch in chunks:
            if not ch.strip():
                continue
            try:
                out.append(self._with_retry(ch, src, tgt))
            except Exception as e:
                log.warning(f"[{self.name}] 分块翻译失败，保留原文: {e}")
                out.append(ch)
        return " ".join(x for x in out if x).strip()

    def _with_retry(self, text: str, src: str, tgt: str) -> str:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._translate(text, src, tgt)
            except Exception as e:
                last = e
                if attempt < self.retries:
                    time.sleep(0.25 * (attempt + 1))
        raise TranslateError(f"{self.name}: {last}")

    def _split(self, text: str) -> list[str]:
        """按句子边界切分，尽量不破坏语义。"""
        pieces = _SENT_SPLIT.split(text)
        chunks: list[str] = []
        buf = ""
        for p in pieces:
            if len(buf) + len(p) <= self.max_chars:
                buf += p
            else:
                if buf:
                    chunks.append(buf)
                # 单句本身就超长，硬切
                while len(p) > self.max_chars:
                    chunks.append(p[: self.max_chars])
                    p = p[self.max_chars:]
                buf = p
        if buf:
            chunks.append(buf)
        return chunks

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass
