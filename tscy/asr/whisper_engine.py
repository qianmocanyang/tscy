"""
语音识别引擎 —— faster-whisper 封装。

这一个模块同时解决需求里的两件事：
  1. 识别说了什么（ASR）
  2. 自动判断说的是中/英/韩/日/俄哪种语言（语种检测 LID）

关键在于：Whisper 是多语统一模型，一次前向传播就能同时吐出"文本 + 语种 + 置信度"，
不需要先跑分类模型再切换 ASR 模型。这也是本方案唯一选定 Whisper 系的根本原因。
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import numpy as np

from ..log import get_logger
from ..lang import normalize_whisper
from ..paths import app_root
from ..types import Utterance
from .base import BaseASR

log = get_logger("asr")

# 模型下载端点。huggingface.co 在国内基本连不上，hf-mirror.com 是常用的国内镜像。
HF_OFFICIAL = "https://huggingface.co"
HF_MIRROR = "https://hf-mirror.com"


def _reachable(url: str, timeout: float = 4.0) -> bool:
    try:
        import requests
        requests.head(url, timeout=timeout, allow_redirects=True)
        return True
    except Exception:
        return False


def apply_hf_endpoint(mode: str = "auto", timeout: float = 4.0) -> str:
    """
    决定用哪个 HuggingFace 端点下载模型，并写进环境变量。

    为什么需要这一步：
        huggingface.co 在国内几乎必然 ConnectTimeout，直接跑会卡几分钟后失败，
        用户只会看到一句看不懂的报错。这里在下载前先探一下，不通就自动切镜像。

    注意 huggingface_hub 是在模块导入时读取 HF_ENDPOINT 的，
    所以除了设环境变量，还要直接改它的常量，否则已经 import 过时环境变量不生效。
    """
    mode = (mode or "auto").strip()
    if mode == "auto":
        endpoint = HF_OFFICIAL if _reachable(HF_OFFICIAL, timeout) else HF_MIRROR
    elif mode == "official":
        endpoint = HF_OFFICIAL
    elif mode == "mirror":
        endpoint = HF_MIRROR
    else:
        endpoint = mode.rstrip("/")     # 允许填自定义镜像地址

    os.environ["HF_ENDPOINT"] = endpoint

    # Xet 存储是 HF 的新一代分发协议，下载时要额外连 cas-server.xethub.hf.co。
    # 实测镜像站和国内网络下这条路会 401 / 超时，直接禁用退回普通 HTTP 下载。
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

    try:
        import huggingface_hub.constants as hfc
        for attr in ("HF_ENDPOINT", "_HF_DEFAULT_ENDPOINT", "ENDPOINT"):
            if hasattr(hfc, attr):
                setattr(hfc, attr, endpoint)
    except Exception:
        pass
    return endpoint

# Whisper 在静音/噪声上会产生的典型幻觉文本。
# 短音频（<1s）尤其容易触发，直接丢弃比翻译出来误导人强。
HALLUCINATIONS = {
    "thank you for watching.",
    "thanks for watching!",
    "thanks for watching.",
    "please subscribe",
    "subscribe to my channel",
    "请不吝点赞",
    "请点赞",
    "订阅",
    "字幕由志愿者提供",
    "字幕由社群提供",
    "谢谢观看",
    "谢谢收看",
    "みんなで応援してください",
    "ご視聴ありがとうございました",
    "감사합니다",
    "спасибо за просмотр",
    "пожалуйста, подпишитесь",
}


class WhisperEngine(BaseASR):
    """faster-whisper 封装，模型懒加载（第一次用到才下载/载入）。"""

    name = "whisper"

    def __init__(
        self,
        model_size: str = "base",
        device: str = "auto",
        compute_type: str = "auto",
        beam_size: int = 1,
        vad_filter: bool = True,
        condition_on_previous_text: bool = False,
        min_conf: float = 0.5,
        model_dir: str = "models",
        initial_prompt: str = "",
        sample_rate: int = 16000,
        hf_endpoint: str = "auto",
    ):
        self.model_size = model_size
        self.device_req = device
        self.compute_req = compute_type
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.condition_on_previous_text = condition_on_previous_text
        self.min_conf = min_conf
        self.model_dir = Path(model_dir)
        if not self.model_dir.is_absolute():
            self.model_dir = app_root() / model_dir
        self.initial_prompt = initial_prompt or ""
        self.sample_rate = sample_rate
        self.hf_endpoint = hf_endpoint or "auto"

        self._model = None
        self._lock = threading.Lock()
        self._resolved: tuple[str, str] | None = None   # (device, compute_type)

    # ---------- 模型加载 ----------

    @staticmethod
    def _cuda_runtime_available() -> bool:
        """
        只检测到有 NVIDIA 卡还不够：很多机器有卡但只装了驱动、没装 CUDA runtime。
        如果直接上 CUDA，第一次 transcribe 会报 cublas64_12.dll 缺失。
        这里预检关键 DLL，避免用户看到一堆红字。
        """
        try:
            import ctypes
            ctypes.windll.LoadLibrary("cublas64_12.dll")
            # cuDNN 8 优先，再试 7
            for cudnn in ("cudnn64_8.dll", "cudnn64_7.dll"):
                try:
                    ctypes.windll.LoadLibrary(cudnn)
                    return True
                except OSError:
                    continue
            return True   # 有的旧卡/环境可能不需要 cuDNN 也能跑
        except OSError:
            return False

    def _resolve_device(self) -> tuple[str, str]:
        """决定实际跑在什么设备上。GPU 真正可用才上 GPU，否则 CPU int8 量化。"""
        if self._resolved:
            return self._resolved

        device = self.device_req
        compute = self.compute_req

        gpu_count = 0
        cuda_ready = False
        try:
            import ctranslate2
            gpu_count = ctranslate2.get_cuda_device_count()
            cuda_ready = gpu_count > 0 and self._cuda_runtime_available()
        except Exception:
            cuda_ready = False

        if device == "auto":
            device = "cuda" if cuda_ready else "cpu"

        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        elif compute == "int8_float16" and device != "cuda":
            compute = "int8"

        self._resolved = (device, compute)
        log.info(
            f"推理设备: {device} / {compute} "
            f"（检测到 {gpu_count} 张 CUDA 卡，CUDA runtime {'可用' if cuda_ready else '不可用'}）"
        )
        return self._resolved

    def has_cached_model(self) -> bool:
        """本地是否已有该模型（有就不去探测下载源，省时间）。"""
        if not self.model_dir.exists():
            return False
        return any(self.model_dir.rglob("model.bin"))

    def load(self, force: bool = False) -> None:
        """加载模型。首次运行会从 HuggingFace 下载到 models/ 目录。"""
        with self._lock:
            if self._model is not None and not force:
                return
            from faster_whisper import WhisperModel

            device, compute = self._resolve_device()
            self.model_dir.mkdir(parents=True, exist_ok=True)

            # 本地已有模型就不用管端点；否则先解决下载源可达性问题
            if not self.has_cached_model():
                endpoint = apply_hf_endpoint(self.hf_endpoint)
                log.info(f"模型下载源: {endpoint}")
            log.info(f"正在加载 Whisper 模型 '{self.model_size}'（首次运行需下载，请稍候）...")

            try:
                model = WhisperModel(
                    self.model_size,
                    device=device,
                    compute_type=compute,
                    download_root=str(self.model_dir),
                )
            except Exception as e:
                # GPU 初始化失败（驱动/CUDA 版本不匹配）时自动退回 CPU
                if device == "cuda":
                    log.warning(f"CUDA 初始化失败，回退 CPU: {e}")
                    self._resolved = ("cpu", "int8")
                    model = WhisperModel(
                        self.model_size, device="cpu", compute_type="int8",
                        download_root=str(self.model_dir),
                    )
                else:
                    raise

            self._model = model
            log.info("模型加载完成")

    @property
    def is_loaded(self) -> bool:
        """模型是否已经加载完成（供 UI 状态展示）。"""
        return self._model is not None

    @property
    def model(self):
        if self._model is None:
            self.load()
        return self._model

    def warmup(self) -> None:
        """用一段静音预热模型，避免第一次真实识别时卡一下。"""
        try:
            self.load()
            self.model.transcribe(
                np.zeros(self.sample_rate, dtype=np.float32),
                beam_size=1, vad_filter=False,
            )
            log.debug("模型预热完成")
        except Exception as e:
            log.debug(f"预热跳过: {e}")

    # ---------- 识别 ----------

    def transcribe(
        self,
        audio: np.ndarray,
        task: str = "transcribe",
        lang: str | None = None,
        audio_ms: int = 0,
    ) -> Utterance:
        """
        识别一段音频。

        task="transcribe"  → 输出原语言文本（配合翻译层用）
        task="translate"   → 直接输出英文译文（目标语言是英文时可跳过翻译层，省一次网络往返）

        返回 Utterance，其中 lang 是自动检测到的源语种。
        """
        if audio.size == 0:
            return Utterance(text="", lang="", lang_conf=0.0, audio_ms=audio_ms)

        t0 = time.perf_counter()
        model = self.model

        try:
            segments, info = model.transcribe(
                audio,
                beam_size=self.beam_size,
                task=task,
                language=lang,                      # None = 自动检测
                vad_filter=self.vad_filter,
                condition_on_previous_text=self.condition_on_previous_text,
                initial_prompt=self.initial_prompt or None,
            )
            text = "".join(seg.text for seg in segments).strip()
            detected = normalize_whisper(getattr(info, "language", "") or "")
            conf = float(getattr(info, "language_probability", 0.0) or 0.0)
        except Exception as e:
            log.error(f"识别失败: {e}")
            return Utterance(text="", lang="", lang_conf=0.0, audio_ms=audio_ms)

        ms = int((time.perf_counter() - t0) * 1000)

        if _is_hallucination(text):
            log.debug(f"丢弃幻觉文本: {text!r}")
            text = ""

        return Utterance(text=text, lang=detected, lang_conf=conf, asr_ms=ms, audio_ms=audio_ms)

    # ---------- 工具 ----------

    def reload_with(self, model_size: str | None = None) -> None:
        """运行时换模型（比如从 base 切到 small），会重新加载。"""
        if model_size and model_size != self.model_size:
            self.model_size = model_size
            self.load(force=True)

    def shutdown(self) -> None:
        self._model = None


def _is_hallucination(text: str) -> bool:
    if not text:
        return False
    t = text.strip().lower().rstrip("。.!！?？ ")
    return t in HALLUCINATIONS
