"""
千问 (通义千问 / DashScope) 云端 ASR 后端。

模型：paraformer-v2（录音文件识别）
支持语种：中文､英文､日语､韩语､德语､法语､俄语（paraformer-v2 官方说明）

为什么作为可选：
    优点：云端 GPU 推理，中文识别率通常优于本地 Whisper base，不抢本地 CPU。
    缺点：需要 DashScope API Key、必须联网、上传音频会多几十到几百毫秒延迟。

默认仍走本地 Whisper；在 config.json 里把 asr.backend 改成 "qwen" 即可启用。
"""

from __future__ import annotations

import os
import tempfile
import wave

import numpy as np

from ..log import get_logger
from ..types import Utterance
from .base import BaseASR

log = get_logger("asr")


def _save_wav(path: str, audio: np.ndarray, sample_rate: int = 16000) -> None:
    """把 float32 音频保存成 16-bit PCM WAV（DashScope 文件识别需要标准格式）。"""
    if audio.size == 0:
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
        return
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


class QwenEngine(BaseASR):
    """DashScope Paraformer 云端识别后端。"""

    name = "qwen"

    # paraformer-v2 官方支持的主要语言（用于 language_hints 和回退语种）
    HINTS = ["zh", "en", "ja", "ko", "de", "fr", "ru"]
    LANG_MAP = {
        "zh": "zh", "en": "en", "ja": "ja", "ko": "ko", "de": "de", "fr": "fr", "ru": "ru"
    }

    def __init__(
        self,
        api_key: str = "",
        model: str = "paraformer-v2",
        language_hints: list[str] | None = None,
        sample_rate: int = 16000,
    ):
        self.api_key = api_key or ""
        self.model = model
        self.language_hints = language_hints or list(self.HINTS)
        self.sample_rate = sample_rate
        self._recognition_cls = None

    def available(self) -> bool:
        return bool(self.api_key)

    def _lazy_import(self):
        if self._recognition_cls is not None:
            return self._recognition_cls
        try:
            from dashscope.audio.asr import Recognition
            self._recognition_cls = Recognition
            return Recognition
        except Exception as e:
            raise RuntimeError(f"DashScope SDK 未安装或不可用: {e}") from e

    def transcribe(
        self,
        audio: np.ndarray,
        task: str = "transcribe",  # noqa: ARG002
        lang: str | None = None,
        audio_ms: int = 0,
    ) -> Utterance:
        if not self.available():
            return Utterance(text="", lang="", lang_conf=0.0, audio_ms=audio_ms)

        Recognition = self._lazy_import()  # noqa: N806

        tmp_path = ""
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            _save_wav(tmp_path, audio, self.sample_rate)

            # SDK 内部会上传到阿里 OSS 并转写
            recognition = Recognition(
                model=self.model,
                format="wav",
                sample_rate=self.sample_rate,
                language_hints=self.language_hints,
            )
            # API Key 可通过环境变量 DASHSCOPE_API_KEY 或 dashscope.api_key 设置
            import dashscope
            if not dashscope.api_key:
                dashscope.api_key = self.api_key

            result = recognition.call(tmp_path)

            text = ""
            if hasattr(result, "get_sentence") and result.get_sentence():
                sentences = result.get_sentence()
                if isinstance(sentences, list):
                    text = "".join(s.get("text", "") for s in sentences if isinstance(s, dict)).strip()
                elif isinstance(sentences, dict):
                    text = sentences.get("text", "")

            # paraformer-v2 不返回检测到的语种，根据 language_hints 做最粗回退
            detected = lang or "auto"
            return Utterance(text=text, lang=detected, lang_conf=0.9, audio_ms=audio_ms)
        except Exception as e:
            log.error(f"千问 ASR 识别失败: {e}")
            return Utterance(text="", lang="", lang_conf=0.0, audio_ms=audio_ms)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def shutdown(self) -> None:
        pass
