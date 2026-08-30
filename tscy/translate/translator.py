"""
翻译门面 —— 策略选择 + 缓存 + 失败降级。

对外只暴露一个 translate() 方法，内部处理全部复杂情况：
  源语言 == 目标语言  → 直接透传，不发请求
  缓存命中            → 零延迟返回
  主后端失败          → 自动切降级后端
  全部失败            → 返回原文并标记，绝不抛异常打断流水线

为什么缓存这么重要：
    游戏对话高度重复（"nice"、"gg"、"谢谢"、"跟我来"、"小心后面"）。
    实测缓存命中率能到 30%+，命中时整条链路省掉 0.3~0.5 秒网络往返。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from pathlib import Path

from ..log import get_logger
from ..paths import app_root
from ..types import Translation
from .base import BaseTranslator
from .deepl import DeepLTranslator
from .google import GoogleTranslator
from .mymemory import MyMemoryTranslator
from .openai import OpenAITranslator

log = get_logger("translate")

BACKENDS: dict[str, type[BaseTranslator]] = {
    "google": GoogleTranslator,
    "mymemory": MyMemoryTranslator,
    "deepl": DeepLTranslator,
    "openai": OpenAITranslator,
}


class NullTranslator(BaseTranslator):
    """不翻译，原文直通。用于只想要转写字幕的场景。"""

    name = "none"

    def _translate(self, text: str, src: str, tgt: str) -> str:  # noqa: ARG002
        return text


class Translator:
    """翻译门面。"""

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.cfg = cfg
        self.timeout = float(cfg.get("timeout", 6))
        self.max_cache = int(cfg.get("cache_size", 2000))

        # auto 模式的探测顺序：先试质量高的付费后端（前提是配了 Key），再试免费后端
        self.auto_order = ["deepl", "openai", "mymemory", "google"]
        self._auto_backend: str | None = None

        cache_file = cfg.get("cache_file", "cache/trans_cache.json")
        self.cache_path = Path(cache_file)
        if not self.cache_path.is_absolute():
            self.cache_path = app_root() / cache_file

        self._cache: OrderedDict[str, str] = OrderedDict()
        self._load_cache()

        self._backends: dict[str, BaseTranslator] = {}
        self._primary_name = (cfg.get("backend") or "google").lower()
        self._fallback_name = (cfg.get("fallback") or "").lower()
        self._dirty = False

    # ---------- 后端管理 ----------

    def _backend(self, name: str) -> BaseTranslator | None:
        if not name or name == "none":
            return None
        if name in self._backends:
            return self._backends[name]
        cls = BACKENDS.get(name)
        if not cls:
            log.warning(f"未知翻译后端: {name}")
            return None
        inst = cls(self.cfg)
        self._backends[name] = inst
        return inst

    def _probe(self) -> str | None:
        """
        auto 模式：按顺序试译一句话，选出第一个真正能通的后端。

        为什么要探测而不是写死默认后端：
            各后端的网络可达性因地而异 —— 实测国内环境里 Google / 有道 / 微软翻译
            全部超时，只有 MyMemory 和配了 Key 的 DeepL 可用。
            写死一个默认值会导致部分用户开箱即用、部分用户一直在报错。
        """
        if self._auto_backend:
            return self._auto_backend

        for name in self.auto_order:
            b = self._backend(name)
            if b is None or not b.available():
                continue
            try:
                # 探测时用更短的超时，避免启动时卡太久
                old_timeout, b.timeout = b.timeout, min(b.timeout, 5.0)
                try:
                    out = b.translate("hello", "en", "zh")
                finally:
                    b.timeout = old_timeout
                if out and out.strip():
                    log.info(f"后端探测: {name} 可用（试译 hello → {out}）")
                    self._auto_backend = name
                    return name
                log.debug(f"后端探测: {name} 返回空结果")
            except Exception as e:
                log.debug(f"后端探测: {name} 不可用 ({e})")

        log.error("没有任何翻译后端可用")
        return None

    def reconfigure(self, cfg: dict) -> None:
        """配置热重载后重建后端（比如换了 Key 或换了后端）。"""
        self.cfg = cfg
        self.timeout = float(cfg.get("timeout", 6))
        self.max_cache = int(cfg.get("cache_size", 2000))
        new_primary = (cfg.get("backend") or "google").lower()
        new_fallback = (cfg.get("fallback") or "").lower()
        if new_primary != self._primary_name or new_fallback != self._fallback_name:
            self._primary_name = new_primary
            self._fallback_name = new_fallback
            self._auto_backend = None      # 换了后端配置就重新探测
            log.info(f"翻译后端切换为 {new_primary}（降级：{new_fallback or '无'}）")
        # Key 可能变了，重建全部后端实例
        for b in self._backends.values():
            b.close()
        self._backends.clear()

    # ---------- 翻译 ----------

    def translate(
        self,
        text: str,
        src: str,
        tgt: str,
        low_conf: bool = False,
    ) -> Translation:
        text = (text or "").strip()
        if not text:
            return Translation(src, "", src, tgt, backend="none")

        # 1) 源语言 == 目标语言：无需翻译
        if src and src == tgt:
            return Translation(text, text, src, tgt, backend="passthrough", skipped=True)

        # 2) 缓存查询
        key = self._key(text, src, tgt)
        if key in self._cache:
            self._cache.move_to_end(key)
            return Translation(text, self._cache[key], src, tgt, backend="cache", cached=True)

        t0 = time.perf_counter()
        result = ""

        # 3) 主后端（auto 模式下先探测出一个能用的）
        if self._primary_name == "auto":
            self._primary_name = self._probe() or "none"
            log.info(f"自动探测选定翻译后端: {self._primary_name}")

        primary = self._backend(self._primary_name)
        backend_used = "none"
        if primary is not None:
            try:
                result = primary.translate(text, src, tgt)
                backend_used = self._primary_name
            except Exception as e:
                log.warning(f"[{self._primary_name}] 翻译失败: {e}")

        # 4) 降级后端
        if not result and self._fallback_name and self._fallback_name != self._primary_name:
            fb = self._backend(self._fallback_name)
            if fb is not None:
                try:
                    result = fb.translate(text, src, tgt)
                    backend_used = self._fallback_name
                    log.info(f"已降级到 {self._fallback_name}")
                except Exception as e:
                    log.warning(f"[{self._fallback_name}] 降级翻译也失败: {e}")

        # 5) 全失败：保留原文，让浮层显示"翻译失败"而不是空白
        if not result:
            result = text
            backend_used = "failed"
            log.error("所有翻译后端均失败，保留原文输出")

        ms = int((time.perf_counter() - t0) * 1000)

        # 只缓存成功的结果，避免把失败原文写进缓存污染后续
        if backend_used not in ("failed", "none"):
            self._cache[key] = result
            self._cache.move_to_end(key)
            self._dirty = True
            while len(self._cache) > self.max_cache:
                self._cache.popitem(last=False)

        return Translation(
            src_text=text,
            dst_text=result,
            src_lang=src,
            dst_lang=tgt,
            backend=backend_used,
            cached=False,
            ms=ms,
            low_conf=low_conf,
        )

    # ---------- 缓存持久化 ----------

    def _key(self, text: str, src: str, tgt: str) -> str:
        raw = f"{src}>{tgt}\x1f{text}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _load_cache(self) -> None:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._cache = OrderedDict(list(data.items())[-self.max_cache:])
                log.info(f"已载入 {len(self._cache)} 条翻译缓存")
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning(f"翻译缓存载入失败，从空缓存开始: {e}")

    def save_cache(self) -> None:
        if not self._dirty:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(self._cache, ensure_ascii=False), encoding="utf-8"
            )
            self._dirty = False
        except Exception as e:
            log.warning(f"翻译缓存保存失败: {e}")

    def clear_cache(self) -> None:
        self._cache.clear()
        self._dirty = True
        self.save_cache()

    def close(self) -> None:
        for b in self._backends.values():
            b.close()
        self.save_cache()

    # ---------- 自检 ----------

    def selftest(self) -> dict[str, str]:
        """给 --selftest 用：逐个后端试译一句，返回 {后端名: 结果或错误}。"""
        out: dict[str, str] = {}
        for name in ("google", "mymemory", "deepl", "openai"):
            b = self._backend(name)
            if b is None:
                out[name] = "未注册"
                continue
            if not b.available():
                out[name] = "未配置 Key（跳过）"
                continue
            try:
                out[name] = b.translate("Hello, good game!", "en", "zh")
            except Exception as e:
                out[name] = f"失败: {e}"
        return out
