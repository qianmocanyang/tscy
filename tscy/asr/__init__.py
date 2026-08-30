"""识别层：faster-whisper 封装（文本 + 语种一次推理同时输出）。"""

from .whisper_engine import WhisperEngine

__all__ = ["WhisperEngine"]
