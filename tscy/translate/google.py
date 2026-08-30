"""
Google 翻译后端（非官方 gtx 端点）—— 默认后端。

优点：免费、无需 Key、支持中英韩日俄互译，质量对日常对话够用。
缺点：非官方接口，可能被限流（表现为返回 429 或空结果）。

应对限流的策略：
  1. 三个域名轮询（googleapis / google.com / google.cn）
  2. 失败后由上层 Translator 自动降级到 fallback 后端
"""

from __future__ import annotations

from ..lang import get as get_lang
from .base import BaseTranslator, TranslateError

HOSTS = [
    "https://translate.googleapis.com/translate_a/single",
    "https://translate.google.com/translate_a/single",
    "https://translate.google.cn/translate_a/single",
]


class GoogleTranslator(BaseTranslator):
    name = "google"
    max_chars = 450

    def _translate(self, text: str, src: str, tgt: str) -> str:
        s = get_lang(src)
        t = get_lang(tgt)
        params = {
            "client": "gtx",
            "sl": (s.google if s else "auto"),
            "tl": (t.google if t else "zh-CN"),
            "dt": "t",
            "q": text,
        }

        last_err: Exception | None = None
        for host in HOSTS:
            try:
                r = self.session.get(host, params=params, timeout=self.timeout)
                if r.status_code == 429:
                    last_err = TranslateError("google 429 限流")
                    continue
                if r.status_code != 200:
                    last_err = TranslateError(f"google HTTP {r.status_code}")
                    continue

                data = r.json()
                # 响应形如 [[["译文","原文",...],["译文2",...]], null, "ja", ...]
                parts = data[0] if isinstance(data, list) and data else []
                out = "".join(seg[0] for seg in parts if seg and isinstance(seg[0], str))
                if out.strip():
                    return out.strip()

                last_err = TranslateError("google 返回空译文")
            except Exception as e:
                last_err = e

        raise TranslateError(f"google 全部域名失败: {last_err}")
