"""
流水线编排 —— 把采集 / 识别 / 翻译 / 输出串成一条链，并管理三种触发模式。

线程模型（严格遵守，这是稳定性的关键）：
  ┌─ Main ──────────  tkinter 主循环（浮层 UI 只能在主线程跑）
  ├─ Audio Callback ─  PortAudio 线程，收 PCM 帧（绝不能被阻塞）
  ├─ Worker ────────  消费音频段：ASR → 翻译（重计算 + 网络 IO 都在这）
  └─ Hotkey ────────  keyboard 监听线程，回调里**只发信号**

铁律：热键回调里绝不推理，否则按住键时整个键鼠监听会僵死。
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from typing import Optional

import numpy as np

from .asr.whisper_engine import WhisperEngine
from .audio.capture import MicCapture
from .audio.vad import EnergyVAD, trim_silence
from .config import Config
from .hotkey import HotkeyManager, is_admin
from .lang import display, next_lang
from .log import get_logger
from .output.overlay import Overlay
from .output.tts import TTS
from .translate.translator import Translator
from .types import Translation, Utterance
from .ui.main_window import MainWindow
from .ui.settings import SettingsWindow
from .ui.tray import TrayIcon

log = get_logger("pipeline")

MODE_PTT = "ptt"          # 按住说话，松开即翻
MODE_TWO_STEP = "two_step"  # 按录音键 → 按输出键
MODE_AUTO = "auto"        # VAD 自动断句


class Pipeline:
    def __init__(self, cfg: Config):
        self.cfg = cfg

        # ---------- 组件 ----------
        self.overlay = Overlay(cfg.section("overlay"))

        asr_cfg = cfg.section("asr")
        asr_backend = (asr_cfg.get("backend") or "whisper").lower()

        if asr_backend == "qwen":
            from .asr.qwen_engine import QwenEngine

            self.asr = QwenEngine(
                api_key=(asr_cfg.get("qwen_key") or cfg.get("translate.qwen_key") or ""),
                model=asr_cfg.get("qwen_model", "paraformer-v2"),
                language_hints=asr_cfg.get("qwen_language_hints"),
                sample_rate=int(cfg.get("audio.sample_rate", 16000)),
            )
            log.info(f"ASR 后端: qwen ({self.asr.model})")
        else:
            self.asr = WhisperEngine(
                model_size=asr_cfg.get("model_size", "base"),
                device=asr_cfg.get("device", "auto"),
                compute_type=asr_cfg.get("compute_type", "auto"),
                beam_size=int(asr_cfg.get("beam_size", 1)),
                vad_filter=bool(asr_cfg.get("vad_filter", True)),
                condition_on_previous_text=bool(asr_cfg.get("condition_on_previous_text", False)),
                min_conf=float(asr_cfg.get("min_conf", 0.5)),
                model_dir=asr_cfg.get("model_dir", "models"),
                initial_prompt=asr_cfg.get("initial_prompt", "") or "",
                sample_rate=int(cfg.get("audio.sample_rate", 16000)),
                hf_endpoint=asr_cfg.get("hf_endpoint", "auto"),
            )

        self.translator = Translator(cfg.section("translate"))

        out_cfg = cfg.section("output")
        self.tts = TTS(out_cfg) if out_cfg.get("speech", True) else None

        audio_cfg = cfg.section("audio")
        self.sample_rate = int(audio_cfg.get("sample_rate", 16000))
        self.frame_ms = int(audio_cfg.get("frame_ms", 30))
        self.capture = MicCapture(
            on_frame=self._on_frame,
            device=audio_cfg.get("device"),
            sample_rate=self.sample_rate,
            frame_ms=self.frame_ms,
            channels=int(audio_cfg.get("channels", 1)),
        )

        vad_cfg = cfg.section("vad")
        self.vad = EnergyVAD(
            frame_ms=self.frame_ms,
            start_db=float(vad_cfg.get("start_db", -40)),
            end_db=float(vad_cfg.get("end_db", -45)),
            start_frames=int(vad_cfg.get("start_frames", 3)),
            silence_ms=int(vad_cfg.get("silence_ms", 600)),
        )
        preroll_n = max(1, int(vad_cfg.get("pre_roll_ms", 300) / self.frame_ms))
        self._preroll: deque[np.ndarray] = deque(maxlen=preroll_n)

        # ---------- 运行时状态 ----------
        self.mode = (cfg.get("mode") or MODE_PTT).lower()
        self._jobs: queue.Queue = queue.Queue()
        self._running = False
        self._recording = False
        self._buffer: list[np.ndarray] = []
        self._buffered_ms = 0
        self._pending: tuple[Utterance, bool] | None = None   # (识别结果, 是否已由 Whisper 译英)
        self._last: Translation | None = None
        self._worker_thread: threading.Thread | None = None
        self.hotkeys = HotkeyManager()
        self.settings = SettingsWindow(cfg, on_change=self._on_config_changed)
        self.main_window = MainWindow(
            cfg,
            on_set_lang=self.set_target_lang,
            on_open_settings=self.on_open_settings,
            on_quit=self.on_quit,
            status_provider=self.get_status,
        )
        self.tray = TrayIcon(self)

        # 记录主线程 ID，热键/托盘回调来自其它线程，
        # 所有 UI 操作必须调度回主线程执行（tkinter 非线程安全）
        self._main_thread_id = threading.get_ident()

        # 录音缓冲同时被 PortAudio 线程和热键线程访问，用一把锁保护
        self._state_lock = threading.RLock()

    # ==================== 生命周期 ====================

    def start(self) -> None:
        """启动。会阻塞在 tkinter 主循环上，退出时返回。"""
        self.overlay.start()

        # 主控制窗口：启动即显示（父窗口是 overlay 的 Tk）
        self.main_window.attach_parent(self.overlay.root)
        self.main_window.open(self.overlay.root)

        if not is_admin():
            log.warning(
                "⚠ 当前不是管理员权限，全局热键可能无效。"
                "请右键 run.bat → 以管理员身份运行。"
            )

        self._setup_hotkeys()
        self.hotkeys.start()
        self.tray.start()

        self._running = True
        self._worker_thread = threading.Thread(target=self._worker, daemon=True, name="worker")
        self._worker_thread.start()

        self.capture.start()
        self.cfg.start_watch(self._on_config_changed)

        # 模型放后台预热，避免第一次按键时卡一下
        threading.Thread(target=self._warmup, daemon=True, name="warmup").start()

        tgt = self.cfg.get("target_lang", "zh")
        mode_tip = {"ptt": "按住说话", "two_step": "两步式", "auto": "自动监听"}.get(self.mode, self.mode)
        self.overlay.show_info(
            f"同声传译已启动 · {mode_tip} · 目标 {display(tgt)}", ttl_ms=3200
        )
        log.info("=" * 60)
        log.info(f"模式={self.mode}  目标语言={display(tgt)}  模型={self.asr.model_size}")
        log.info(
            "热键: "
            + "  ".join(f"{k}={v}" for k, v in self.cfg.section("hotkeys").items())
        )
        log.info("=" * 60)

        try:
            self.overlay.run()
        finally:
            self.stop()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        log.info("正在退出...")
        try:
            self.hotkeys.stop()
        except Exception:
            pass
        try:
            self.tray.stop()
        except Exception:
            pass
        try:
            self.capture.stop()
        except Exception:
            pass
        self._jobs.put(None)
        if self.tts:
            self.tts.shutdown()
        try:
            self.translator.close()
        except Exception:
            pass
        self.cfg.stop_watch()
        log.info("已退出，翻译缓存已保存")

    def _warmup(self) -> None:
        try:
            t0 = time.perf_counter()
            self.asr.warmup()
            log.info(f"模型预热完成（{int((time.perf_counter() - t0) * 1000)}ms），可以开始说话了")
            self.overlay.show_info("模型就绪", ttl_ms=1500)
        except Exception as e:
            log.error(f"模型预热失败: {e}")

    # ==================== 热键 ====================

    def _setup_hotkeys(self) -> None:
        hk = self.cfg.section("hotkeys")
        self.hotkeys.register("record", hk.get("record", ""),
                              on_press=self.on_record_press,
                              on_release=self.on_record_release)
        self.hotkeys.register("emit", hk.get("emit", ""), on_press=self.on_emit)
        self.hotkeys.register("cancel", hk.get("cancel", ""), on_press=self.on_cancel)
        self.hotkeys.register("cycle_target", hk.get("cycle_target", ""), on_press=self.on_cycle_target)
        self.hotkeys.register("toggle_overlay", hk.get("toggle_overlay", ""), on_press=self.on_toggle_overlay)
        self.hotkeys.register("toggle_speech", hk.get("toggle_speech", ""), on_press=self.on_toggle_speech)
        self.hotkeys.register("settings", hk.get("settings", ""), on_press=self.on_open_settings)
        self.hotkeys.register("show_main", hk.get("show_main", ""), on_press=self.on_show_main)
        self.hotkeys.register("quit", hk.get("quit", ""), on_press=self.on_quit)

    def _ui(self, fn) -> None:
        """
        把 UI 操作调度到主线程执行。

        为什么必须这样：
            热键（keyboard）和托盘（pystray）的回调跑在各自线程里，
            直接在这些线程里创建/修改 tkinter 窗口是未定义行为 ——
            窗口会"一闪而过"或随机崩溃。tkinter 只能在主线程碰。
        """
        root = self.overlay.root if self.overlay else None
        if root is None:
            try:
                fn()
            except Exception as e:
                log.error(f"UI 操作失败: {e}")
            return
        if threading.get_ident() == self._main_thread_id:
            try:
                fn()
            except Exception as e:
                log.error(f"UI 操作失败: {e}")
        else:
            try:
                root.after(0, fn)
            except Exception as e:
                log.error(f"UI 调度失败: {e}")

    def on_record_press(self) -> None:
        if self.mode == MODE_PTT:
            self._start_record(use_preroll=False)
        elif self.mode == MODE_TWO_STEP:
            if self._recording:
                self._submit()
            else:
                self._start_record(use_preroll=False)
        else:
            # auto 模式下手动按键 = 强制开始录一段
            self._start_record(use_preroll=False)

    def on_record_release(self) -> None:
        if self.mode == MODE_PTT and self._recording:
            self._submit()

    def on_emit(self) -> None:
        """两步模式：把识别到的原文翻译并输出。"""
        self._jobs.put(("emit",))

    def on_cancel(self) -> None:
        with self._state_lock:
            self._recording = False
            self._buffer.clear()
            self._buffered_ms = 0
            self._pending = None
        self.vad.reset()
        self.overlay.clear()
        log.info("已取消当前片段")

    def on_cycle_target(self) -> None:
        self._ui(self._do_cycle_target)

    def _do_cycle_target(self) -> None:
        cur = self.cfg.get("target_lang", "zh")
        nxt = next_lang(cur)
        self.cfg.set("target_lang", nxt, save=True)
        self.overlay.show_info(f"目标语言 → {display(nxt)}", nxt, ttl_ms=1800)
        log.info(f"目标语言切换: {display(cur)} → {display(nxt)}")

    def on_toggle_overlay(self) -> None:
        self._ui(self._do_toggle_overlay)

    def _do_toggle_overlay(self) -> None:
        vis = self.overlay.toggle()
        log.info(f"字幕浮层: {'显示' if vis else '隐藏'}")

    def on_toggle_speech(self) -> None:
        cur = bool(self.cfg.get("output.speech", True))
        self.cfg.set("output.speech", not cur, save=True)
        if not cur and self.tts is None:
            self.tts = TTS(self.cfg.section("output"))
        elif cur and self.tts is not None:
            self.tts.stop()
        log.info(f"语音播报: {'开' if not cur else '关'}")

    def on_open_settings(self) -> None:
        self._ui(self._do_open_settings)

    def _do_open_settings(self) -> None:
        if self.overlay and self.overlay.root:
            self.settings.open(self.overlay.root)

    def on_show_main(self) -> None:
        self._ui(self.main_window.show)

    def show_main(self) -> None:
        """托盘/其它线程唤回主窗口。"""
        self._ui(self.main_window.show)

    def set_target_lang(self, code: str) -> None:
        """托盘菜单直接指定目标语言。"""
        self._ui(lambda: self._do_set_target_lang(code))

    def _do_set_target_lang(self, code: str) -> None:
        from .lang import supported_codes

        if code not in supported_codes():
            return
        self.cfg.set("target_lang", code, save=True)
        self.overlay.show_info(f"目标语言 → {display(code)}", code, ttl_ms=1800)
        log.info(f"目标语言切换: {display(code)}")

    def on_quit(self) -> None:
        self.overlay.stop()

    # ==================== 录音 ====================

    def _start_record(self, use_preroll: bool) -> None:
        if self._recording:
            return
        self._buffer = list(self._preroll) if use_preroll else []
        self._buffered_ms = len(self._buffer) * self.frame_ms
        self._recording = True
        if not use_preroll:
            self.vad.reset()
        self.overlay.show_info("● 录音中", ttl_ms=0)
        log.debug("开始录音")

    def _submit(self) -> None:
        if not self._recording:
            return
        self._recording = False
        frames, self._buffer = self._buffer, []
        self._buffered_ms = 0

        if not frames:
            return

        audio = np.concatenate(frames)
        audio_ms = int(len(audio) / self.sample_rate * 1000)

        vad_cfg = self.cfg.section("vad")
        if audio_ms < int(vad_cfg.get("min_ms", 300)):
            log.debug(f"音频过短({audio_ms}ms)，丢弃")
            self.overlay.clear()
            return

        if vad_cfg.get("trim_silence", True):
            trimmed = trim_silence(
                audio, frame_ms=self.frame_ms, sample_rate=self.sample_rate,
                threshold_db=float(vad_cfg.get("end_db", -45)),
            )
            if trimmed.size:
                audio = trimmed

        if audio.size == 0:
            self.overlay.clear()
            return

        log.debug(f"提交音频 {audio_ms}ms → 识别队列")
        self._jobs.put(("asr", audio, audio_ms))

    # ==================== 音频回调（PortAudio 线程） ====================

    def _on_frame(self, frame: np.ndarray) -> None:
        if not self._running:
            return

        if self._recording:
            self._buffer.append(frame)
            self._buffered_ms += self.frame_ms

            vad_cfg = self.cfg.section("vad")
            max_ms = int(vad_cfg.get("max_ms", 15000))

            if self.mode == MODE_AUTO:
                if self.vad.feed(frame) == EnergyVAD.EVENT_END:
                    self._submit()
                    return
            if self._buffered_ms >= max_ms:
                log.warning(f"录音超过 {max_ms}ms，自动提交")
                self._submit()
        else:
            if self.mode == MODE_AUTO:
                self._preroll.append(frame)
                if self.vad.feed(frame) == EnergyVAD.EVENT_START:
                    self._start_record(use_preroll=True)

    # ==================== Worker 线程 ====================

    def _worker(self) -> None:
        while self._running:
            try:
                job = self._jobs.get(timeout=0.25)
            except queue.Empty:
                continue
            if job is None:
                break
            try:
                kind = job[0]
                if kind == "asr":
                    self._handle_asr(job[1], job[2])
                elif kind == "emit":
                    self._handle_emit()
            except Exception as e:
                log.error(f"处理任务出错: {e}")
        log.debug("worker 线程退出")

    def _handle_asr(self, audio: np.ndarray, audio_ms: int) -> None:
        tgt = self.cfg.get("target_lang", "zh")
        asr_cfg = self.cfg.section("asr")

        # 目标语言是英文时，直接用 Whisper 的翻译任务一步到位，省掉一次网络往返
        whisper_translate = (
            tgt == "en" and bool(asr_cfg.get("prefer_whisper_translate", True))
        )
        task = "translate" if whisper_translate else "transcribe"

        self.overlay.show_info("识别中…", ttl_ms=0)
        u = self.asr.transcribe(audio, task=task, audio_ms=audio_ms)

        if not u.text:
            self.overlay.show_info("没听清，请再说一次", ttl_ms=1600)
            log.info("识别结果为空（可能是静音或噪声）")
            return

        log.info(
            f"识别 {u.audio_ms}ms → [{u.lang} {u.lang_conf:.2f}] {u.text}  ({u.asr_ms}ms)"
        )

        if self.mode == MODE_TWO_STEP:
            # 先把原文亮出来，等玩家按输出键确认后再翻译
            self._pending = (u, whisper_translate)
            self.overlay.show_partial(f"{u.text}", u.lang)
        else:
            self._pending = (u, whisper_translate)
            self._emit()

    def _handle_emit(self) -> None:
        if self._pending is not None:
            self._emit()
        elif self._last is not None:
            # PTT/auto 模式下按输出键 = 重播上一条译文
            self.overlay.show_translation(self._last)
            if self.tts and self.cfg.get("output.speech", True):
                self.tts.speak(self._last.dst_text, self._last.dst_lang)
            log.info("重播上一条译文")
        else:
            self.overlay.show_info("没有待输出的内容", ttl_ms=1400)

    def _emit(self) -> None:
        if self._pending is None:
            return
        u, whisper_translated = self._pending
        self._pending = None

        tgt = self.cfg.get("target_lang", "zh")
        min_conf = float(self.cfg.section("asr").get("min_conf", 0.5))
        low_conf = bool(u.lang) and u.lang_conf < min_conf

        # 三种不需要走翻译层的情况，直接输出
        if whisper_translated and u.lang and u.lang != "en":
            tr = Translation(u.text, u.text, u.lang, "en",
                             backend="whisper", low_conf=low_conf)
        elif u.lang and u.lang == tgt:
            tr = Translation(u.text, u.text, u.lang, tgt,
                             backend="passthrough", skipped=True, low_conf=low_conf)
        elif whisper_translated and u.lang == "en":
            tr = Translation(u.text, u.text, u.lang, "en",
                             backend="passthrough", skipped=True, low_conf=low_conf)
        else:
            src = u.lang or "auto"
            tr = self.translator.translate(u.text, src, tgt, low_conf=low_conf)

        self._last = tr
        self._output(tr)

    def _output(self, tr: Translation) -> None:
        # 低置信时给译文加个提示前缀，避免玩家把听错的内容当真
        if tr.low_conf:
            tr.dst_text = f"⚠ {tr.dst_text}"

        self.overlay.show_translation(tr)

        if self.tts and self.cfg.get("output.speech", True):
            self.tts.speak(tr.dst_text, tr.dst_lang)

        tag = "缓存" if tr.cached else tr.backend
        log.info(f"输出 [{u_arrow(tr.src_lang, tr.dst_lang)} · {tag} {tr.ms}ms] {tr.dst_text}")

        # 主控制窗口的最近翻译记录（线程安全，内部会调度回主线程）
        try:
            self.main_window.add_record(tr.src_text, tr.dst_text, tag)
        except Exception as e:
            log.debug(f"主窗口记录追加失败: {e}")

    # ==================== 状态查询（主窗口用） ====================

    def get_status(self) -> dict:
        """给主控制窗口的状态总览。"""
        asr_backend = (self.cfg.get("asr.backend") or "whisper").lower()
        if asr_backend == "qwen":
            asr_txt = f"千问 {getattr(self.asr, 'model', 'paraformer-v2')}"
            asr_ready = bool(getattr(self.asr, "available", lambda: False)())
        else:
            asr_txt = f"Whisper {self.asr.model_size}"
            asr_ready = bool(getattr(self.asr, "is_loaded", False))

        tr = self.translator
        backend_cfg = (self.cfg.get("translate.backend") or "auto").lower()
        if backend_cfg == "auto":
            chosen = getattr(tr, "_primary_name", "auto")
            tr_txt = f"{chosen}（auto）" if chosen != "auto" else "探测中…"
        else:
            tr_txt = backend_cfg

        tts_cfg = (self.cfg.get("output.tts_engine") or "edge").lower()
        tts_txt = {
            "edge": "Edge-tts", "qwen": "千问 CosyVoice",
            "pyttsx3": "系统语音", "off": "关闭",
        }.get(tts_cfg, tts_cfg)

        return {
            "asr": asr_txt,
            "asr_ready": asr_ready,
            "translate": tr_txt,
            "tts": tts_txt,
            "recording": self._recording,
        }

    # ==================== 配置热重载 ====================

    def _on_config_changed(self) -> None:
        try:
            self.mode = (self.cfg.get("mode") or MODE_PTT).lower()
            self.overlay.apply_config(self.cfg.section("overlay"))
            self.translator.reconfigure(self.cfg.section("translate"))
            if self.tts:
                self.tts.reconfigure(self.cfg.section("output"))

            vad_cfg = self.cfg.section("vad")
            self.vad.start_db = float(vad_cfg.get("start_db", -40))
            self.vad.end_db = float(vad_cfg.get("end_db", -45))
            self.vad.start_frames = int(vad_cfg.get("start_frames", 3))
            self.vad.silence_ms = int(vad_cfg.get("silence_ms", 600))

            asr_cfg = self.cfg.section("asr")
            self.asr.min_conf = float(asr_cfg.get("min_conf", 0.5))
            new_model = asr_cfg.get("model_size")
            if new_model and new_model != self.asr.model_size:
                threading.Thread(
                    target=self.asr.reload_with, args=(new_model,), daemon=True
                ).start()
        except Exception as e:
            log.error(f"应用新配置失败: {e}")


def u_arrow(src: str, dst: str) -> str:
    return f"{src or '?'}→{dst}"
