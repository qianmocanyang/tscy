"""
语音播报 —— edge-tts 在线合成（主）+ pyttsx3 离线（兜底）。

播放方式的选择：
    edge-tts 输出的是 mp3，而 Python 标准库的 winsound 只认 wav，
    引入 pygame/playsound 又太重（且 playsound 1.2 有已知死锁 bug）。
    这里直接调 Windows 自带的 MCI（Media Control Interface）播 mp3 —— 零依赖、
    系统自带、支持打断，是 Windows 上最省事的做法。

缓存策略：
    同一句话合成一次存成 mp3，之后直接播本地文件。
    "nice""gg""谢谢"这类高频话术命中缓存后，省掉 0.4~0.8 秒合成时间。
"""

from __future__ import annotations

import asyncio
import hashlib
import queue
import threading
import time
from pathlib import Path

from ..lang import get as get_lang
from ..log import get_logger
from ..paths import app_root, ensure_dir
from .qwen_tts import QwenTTS

log = get_logger("tts")

CACHE_DIR = ensure_dir(app_root() / "cache" / "tts")

_IS_WINDOWS = __import__("os").name == "nt"


class SpeechError(Exception):
    pass


# ---------------------------------------------------------------- 播放底层

def _mci(cmd: str) -> str:
    """向 Windows MCI 发一条命令字符串。"""
    import ctypes
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.winmm.mciSendStringW(cmd, buf, 256, 0)
    return buf.value


class _MCIPlayer:
    """用 MCI 播放 mp3，支持打断。"""

    ALIAS = "tscy_tts"

    def play(self, path: Path, stop_event: threading.Event, timeout: float = 30.0) -> None:
        if not _IS_WINDOWS:
            raise SpeechError("非 Windows 平台不支持 MCI 播放")

        self.close()
        p = str(path).replace('"', "")
        ret = _mci(f'open "{p}" type mpegvideo alias {self.ALIAS}')
        if not ret.strip().isdigit() or int(ret.strip()) != 0:
            # MCI 返回 0 表示成功；非 0 是错误码
            if "0" not in ret:
                raise SpeechError(f"MCI open 失败: {ret}")

        try:
            _mci(f"play {self.ALIAS}")
            deadline = time.time() + timeout
            while time.time() < deadline:
                if stop_event.is_set():
                    _mci(f"stop {self.ALIAS}")
                    break
                mode = _mci(f"status {self.ALIAS} mode").strip().lower()
                if mode in ("stopped", "played", ""):
                    break
                time.sleep(0.05)
        finally:
            self.close()

    def close(self) -> None:
        try:
            _mci(f"close {self.ALIAS}")
        except Exception:
            pass


# ---------------------------------------------------------------- TTS 主体

class TTS:
    """
    语音播报器。内部起一个后台线程串行播放，调用方立即返回不阻塞。
    """

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.engine = (cfg.get("tts_engine") or "edge").lower()
        self.interrupt = bool(cfg.get("tts_interrupt", True))
        self.volume = float(cfg.get("tts_volume", 1.0))
        self.rate = cfg.get("tts_rate") or "+0%"
        self.fixed_voice = cfg.get("tts_voice") or ""
        self.max_chars = int(cfg.get("max_tts_chars", 200))

        self.qwen = QwenTTS(
            api_key=cfg.get("qwen_key", "") or "",
            model=cfg.get("qwen_tts_model", "cosyvoice-v3-flash") or "cosyvoice-v3-flash",
            voice=cfg.get("qwen_tts_voice", "longanyang") or "longanyang",
        )

        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        self._q: queue.Queue[tuple[str, str] | None] = queue.Queue()
        self._stop_event = threading.Event()
        self._player = _MCIPlayer()
        self._pyttsx3 = None
        self._thread = threading.Thread(target=self._loop, daemon=True, name="tts")
        self._thread.start()

    # ---------- 对外 ----------

    def speak(self, text: str, lang: str = "") -> None:
        """投递一句语音。立即返回，实际播放在后台线程。"""
        text = (text or "").strip()
        if not text or self.engine == "off":
            return
        if len(text) > self.max_chars:
            log.debug(f"文本过长({len(text)}字)，跳过语音播报")
            return
        if self.interrupt:
            # 打断上一条：清空队列并通知播放器停止
            while not self._q.empty():
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    break
            self._stop_event.set()
        self._q.put((text, lang))

    def stop(self) -> None:
        self._stop_event.set()
        self._player.close()

    def shutdown(self) -> None:
        self.stop()
        self._q.put(None)
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def reconfigure(self, cfg: dict) -> None:
        self.engine = (cfg.get("tts_engine") or "edge").lower()
        self.interrupt = bool(cfg.get("tts_interrupt", True))
        self.volume = float(cfg.get("tts_volume", 1.0))
        self.rate = cfg.get("tts_rate") or "+0%"
        self.fixed_voice = cfg.get("tts_voice") or ""
        self.max_chars = int(cfg.get("max_tts_chars", 200))
        self.qwen.reconfigure(cfg)

    # ---------- 内部 ----------

    def _voice_for(self, lang: str) -> str:
        if self.fixed_voice:
            return self.fixed_voice
        l = get_lang(lang)
        return l.tts if l else "zh-CN-XiaoxiaoNeural"

    @staticmethod
    def _volume_str(v: float) -> str:
        pct = int(round(max(0.0, min(1.0, v)) * 100)) - 100
        return f"{pct:+d}%"

    def _cache_path(self, voice: str, text: str) -> Path:
        key = hashlib.md5(f"{voice}|{self.rate}|{text}".encode("utf-8")).hexdigest()
        return CACHE_DIR / f"{key}.mp3"

    def _loop(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            text, lang = item
            self._stop_event.clear()
            try:
                self._speak_one(text, lang)
            except Exception as e:
                log.warning(f"语音播报失败: {e}")
            finally:
                self._stop_event.clear()

    def _speak_one(self, text: str, lang: str) -> None:
        if self.engine == "pyttsx3":
            self._speak_pyttsx3(text)
            return

        if self.engine == "qwen":
            path = self._cache_path(self.qwen.voice, text)
            if not path.exists():
                data = self.qwen.synth(text, lang)
                path.write_bytes(data)
            if path.exists() and path.stat().st_size > 0:
                self._player.play(path, self._stop_event)
            return

        voice = self._voice_for(lang)
        path = self._cache_path(voice, text)

        if not path.exists():
            self._synth_edge(text, voice, path)

        if path.exists() and path.stat().st_size > 0:
            self._player.play(path, self._stop_event)

    def _synth_edge(self, text: str, voice: str, path: Path) -> None:
        import edge_tts

        async def _run():
            comm = edge_tts.Communicate(
                text, voice, rate=self.rate, volume=self._volume_str(self.volume)
            )
            await comm.save(str(path))

        try:
            asyncio.run(_run())
        except Exception as e:
            if path.exists():
                try:
                    path.unlink()   # 合成一半失败会留下残缺文件，删掉避免下次播到截断音频
                except OSError:
                    pass
            raise SpeechError(f"edge-tts 合成失败: {e}") from e

    def _speak_pyttsx3(self, text: str) -> None:
        try:
            if self._pyttsx3 is None:
                import pyttsx3
                self._pyttsx3 = pyttsx3.init()
                self._pyttsx3.setProperty("volume", self.volume)
            self._pyttsx3.say(text)
            self._pyttsx3.runAndWait()
        except Exception as e:
            raise SpeechError(f"pyttsx3 播报失败（可能需要 pip install pywin32）: {e}") from e
