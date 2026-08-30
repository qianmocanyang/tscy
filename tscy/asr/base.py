"""
ASR 抽象基类 —— 让本地 Whisper 和云端千问可以互换。

默认用本地 Whisper（免费、离线、速度可控），千问作为可选增强（识别率更高，但需要 API Key）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import Utterance


class BaseASR(ABC):
    name: str = "base"

    @abstractmethod
    def transcribe(
        self,
        audio,
        task: str = "transcribe",
        lang: str | None = None,
        audio_ms: int = 0,
    ) -> Utterance:
        """
        识别音频。

        task="translate" 表示直接译成英文（目前只有 Whisper 支持此优化）。
        云端 ASR 遇到 translate 任务时，应忽略 task，只返回原文文本。
        """

    def available(self) -> bool:
        return True

    def warmup(self) -> None:
        pass

    def shutdown(self) -> None:
        pass
