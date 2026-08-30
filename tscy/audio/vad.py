"""
端点检测（VAD）—— 纯 numpy 能量 VAD。

作用：判断"什么时候开始说话、什么时候说完了"，把连续音频切成一个个句子。

为什么不用 webrtcvad / silero-vad：
    webrtcvad 在 Python 3.13 上需要编译（Windows 没装 VS 构建工具就会失败）；
    silero-vad 要拉一个 onnx 模型 + onnxruntime。
    游戏场景下能量 VAD 完全够用，且零依赖、参数直观、好调。

核心机制 —— 滞回（hysteresis）：
    起说阈值 start_db(-40) 比落音阈值 end_db(-45) 高 5dB。
    如果两个阈值相同，音量在临界点抖动时状态会疯狂翻转，一句话被切成十几段。
    留 5dB 余量后，一旦进入"说话"状态，音量要掉得更低才会判定结束。
"""

from __future__ import annotations

import numpy as np

_EPS = 1e-10


def dbfs(frame: np.ndarray) -> float:
    """计算一帧音频的 RMS 电平（dBFS）。静音约 -90dB，正常说话 -30~-10dB。"""
    if frame.size == 0:
        return -100.0
    rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
    return 20.0 * np.log10(rms + _EPS)


class EnergyVAD:
    """两态状态机：静音 ⇄ 说话。"""

    EVENT_NONE = ""
    EVENT_START = "start"
    EVENT_END = "end"

    def __init__(
        self,
        frame_ms: int = 30,
        start_db: float = -40.0,
        end_db: float = -45.0,
        start_frames: int = 3,
        silence_ms: int = 600,
    ):
        self.frame_ms = frame_ms
        self.start_db = start_db
        self.end_db = end_db
        self.start_frames = max(1, start_frames)
        self.silence_ms = silence_ms

        self.speaking = False
        self._loud_streak = 0     # 连续超阈值的帧数
        self._silent_ms = 0       # 说话状态下累计静音时长

    def reset(self) -> None:
        self.speaking = False
        self._loud_streak = 0
        self._silent_ms = 0

    def feed(self, frame: np.ndarray) -> str:
        """
        喂入一帧，返回事件：""（无）/ "start"（开始说话）/ "end"（说完）。

        注意 "end" 只在**已经处于说话状态**且静音超时后触发，
        所以调用方应把它当作"提交这段音频"的信号。
        """
        level = dbfs(frame)

        if not self.speaking:
            if level >= self.start_db:
                self._loud_streak += 1
                if self._loud_streak >= self.start_frames:
                    self.speaking = True
                    self._silent_ms = 0
                    self._loud_streak = 0
                    return self.EVENT_START
            else:
                self._loud_streak = 0
            return self.EVENT_NONE

        # 已在说话状态
        if level < self.end_db:
            self._silent_ms += self.frame_ms
            if self._silent_ms >= self.silence_ms:
                self.speaking = False
                self._silent_ms = 0
                return self.EVENT_END
        else:
            self._silent_ms = 0
        return self.EVENT_NONE


def trim_silence(
    audio: np.ndarray,
    frame_ms: int = 30,
    sample_rate: int = 16000,
    threshold_db: float = -45.0,
    keep_ms: int = 120,
) -> np.ndarray:
    """
    裁掉音频首尾的静音段。

    为什么要裁：
        按下 PTT 到真正开口通常有 0.3~0.5 秒间隔，松开时也一样。
        这些纯静音会让 Whisper 的注意力被稀释，短句尤其容易识别成幻听文本
        （Whisper 在静音上会输出 "谢谢观看"、"请不吝点赞" 这类幻觉）。

    keep_ms：裁完再前后各留一小段，避免把第一个字的起音削掉。
    """
    if audio.size == 0:
        return audio

    frame_len = max(1, int(sample_rate * frame_ms / 1000))
    n = audio.size // frame_len
    if n < 3:
        return audio

    frames = audio[: n * frame_len].reshape(n, frame_len)
    levels = np.array([dbfs(f) for f in frames])

    loud = np.where(levels >= threshold_db)[0]
    if loud.size == 0:
        return audio[:0]  # 全程静音，返回空

    keep = max(1, int(keep_ms / frame_ms))
    start = max(0, int(loud[0]) - keep)
    end = min(n, int(loud[-1]) + 1 + keep)

    return audio[start * frame_len: end * frame_len]
