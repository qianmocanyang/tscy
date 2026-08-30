"""
千问 (DashScope / CosyVoice) 云端语音合成后端。

模型：cosyvoice-v3-flash（默认）
支持语种：中､英､德､法､俄､日､韩 等（具体看 CosyVoice 官方音色列表）

为什么作为可选：
    优点：音质远超 edge-tts，多语言发音自然，可选手富情感。
    缺点：需要 API Key、按量计费、首包延迟比 edge-tts 略高。

默认仍走 edge-tts；在 config.json 里把 output.tts_engine 改成 "qwen" 启用。
"""

from __future__ import annotations

from ..log import get_logger

log = get_logger("tts")


class QwenTTS:
    """DashScope CosyVoice 语音合成封装。"""

    def __init__(
        self,
        api_key: str,
        model: str = "cosyvoice-v3-flash",
        voice: str = "longanyang",
    ):
        self.api_key = api_key or ""
        self.model = model
        self.voice = voice

    def available(self) -> bool:
        return bool(self.api_key)

    def synth(self, text: str, lang: str = "") -> bytes:
        """返回 mp3 bytes。"""
        if not self.available():
            raise RuntimeError("未配置 DashScope API Key")

        try:
            import dashscope
            from dashscope.audio.tts_v2 import SpeechSynthesizer
        except Exception as e:
            raise RuntimeError(f"DashScope SDK 未安装: {e}") from e

        if not dashscope.api_key:
            dashscope.api_key = self.api_key

        synthesizer = SpeechSynthesizer(model=self.model, voice=self.voice)
        audio = synthesizer.call(text)
        if audio is None:
            raise RuntimeError("千问 TTS 返回空音频")
        return bytes(audio) if not isinstance(audio, bytes) else audio

    def reconfigure(self, cfg: dict) -> None:
        self.api_key = cfg.get("qwen_key", "") or ""
        self.model = cfg.get("qwen_tts_model", "cosyvoice-v3-flash") or "cosyvoice-v3-flash"
        self.voice = cfg.get("qwen_tts_voice", "longanyang") or "longanyang"
