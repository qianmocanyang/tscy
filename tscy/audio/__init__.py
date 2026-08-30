"""音频层：麦克风采集 + 端点检测。"""

from .capture import MicCapture
from .vad import EnergyVAD, dbfs, trim_silence

__all__ = ["MicCapture", "EnergyVAD", "dbfs", "trim_silence"]
