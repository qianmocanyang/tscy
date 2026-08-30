"""
麦克风采集 —— 基于 sounddevice 的回调式采集，输出 16kHz 单声道 float32。

为什么用回调模式而不是轮询 read()：
    回调跑在 PortAudio 自己的后台线程上，实时性由音频驱动保证。
    如果在主线程轮询 read()，一旦 ASR 推理卡住 500ms，音频缓冲就会溢出丢帧。

为什么必须是 16kHz 单声道：
    Whisper 的训练数据就是 16k 单声道。喂别的采样率会导致识别率断崖式下跌。
    声卡不支持 16k 时，这里做线性重采样兜底。
"""

from __future__ import annotations

import threading
from typing import Callable

import numpy as np

from ..log import get_logger

log = get_logger("audio")


class MicCapture:
    """麦克风采集器。start() 后每帧通过 on_frame 回调抛出。"""

    def __init__(
        self,
        on_frame: Callable[[np.ndarray], None],
        device: int | str | None = None,
        sample_rate: int = 16000,
        frame_ms: int = 30,
        channels: int = 1,
    ):
        self.on_frame = on_frame
        self.device = device
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.channels = channels

        self._stream = None
        self._running = threading.Event()
        self._actual_rate = sample_rate
        self._resample = False
        # 重采样用的目标位置缓存（线性插值）
        self._ratio = 1.0

    # ---------- 生命周期 ----------

    def start(self) -> None:
        import sounddevice as sd

        if self._stream is not None:
            return

        blocksize = int(self.sample_rate * self.frame_ms / 1000)

        # 先按 16k 试；部分声卡（尤其是虚拟声卡）不支持，回退到设备默认采样率再重采样
        try:
            self._stream = sd.InputStream(
                device=self.device,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="float32",
                blocksize=blocksize,
                callback=self._callback,
            )
            self._actual_rate = self.sample_rate
            self._resample = False
        except Exception as e:
            log.warning(f"以 {self.sample_rate}Hz 打开失败，改用设备默认采样率后重采样: {e}")
            info = sd.query_devices(self.device, "input") if self.device is not None else sd.query_devices(kind="input")
            dev_rate = int(float(info["default_samplerate"]))
            self._actual_rate = dev_rate
            self._resample = dev_rate != self.sample_rate
            self._ratio = self.sample_rate / dev_rate
            self._stream = sd.InputStream(
                device=self.device,
                samplerate=dev_rate,
                channels=self.channels,
                dtype="float32",
                blocksize=int(dev_rate * self.frame_ms / 1000),
                callback=self._callback,
            )

        self._running.set()
        self._stream.start()
        log.info(
            f"麦克风已启动 device={self.device} rate={self._actual_rate}Hz "
            f"frame={self.frame_ms}ms resample={self._resample}"
        )

    def stop(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception as e:
            log.debug(f"关闭音频流时出错: {e}")
        finally:
            self._stream = None
            self._running.clear()
            log.info("麦克风已停止")

    @property
    def running(self) -> bool:
        return self._running.is_set()

    # ---------- 内部 ----------

    def _callback(self, indata, frames, time_info, status):  # noqa: ARG002
        """PortAudio 线程回调 —— 只做格式转换和转发，绝不干重活。"""
        if status:
            log.debug(f"音频流状态: {status}")
        if not self._running.is_set():
            return

        # (frames, channels) -> (frames,)，多通道取平均混成单声道
        if indata.ndim > 1 and indata.shape[1] > 1:
            data = indata.mean(axis=1)
        else:
            data = indata[:, 0] if indata.ndim > 1 else indata.reshape(-1)

        data = np.ascontiguousarray(data, dtype=np.float32)

        if self._resample:
            n_out = max(1, int(round(len(data) * self._ratio)))
            src_pos = np.linspace(0.0, len(data) - 1, num=len(data), dtype=np.float32)
            dst_pos = np.linspace(0.0, len(data) - 1, num=n_out, dtype=np.float32)
            data = np.interp(dst_pos, src_pos, data).astype(np.float32)

        try:
            self.on_frame(data)
        except Exception as e:
            log.error(f"帧回调异常: {e}")

    # ---------- 工具 ----------

    @staticmethod
    def list_devices() -> list[dict]:
        """列出所有可用输入设备，供 --list-devices 使用。"""
        import sounddevice as sd

        out: list[dict] = []
        try:
            devices = sd.query_devices()
            default_in = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else sd.default.device
        except Exception as e:
            log.error(f"查询音频设备失败: {e}")
            return out

        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                out.append({
                    "id": i,
                    "name": d.get("name", "?"),
                    "channels": d.get("max_input_channels"),
                    "rate": d.get("default_samplerate"),
                    "default": (i == default_in),
                })
        return out


def frames_to_seconds(n_frames: int, sample_rate: int = 16000) -> float:
    return n_frames / sample_rate
