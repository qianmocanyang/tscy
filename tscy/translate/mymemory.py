"""
MyMemory 翻译后端 —— 免费备用源，默认作为降级后端。

限额：匿名 5000 字符/日；在 config 里填 mymemory_email 可提到 50000 字符/日。
质量比 Google 略差，但作为备用够用。
"""

from __future__ import annotations

import html

from ..lang import get as get_lang
from .base import BaseTranslator, TranslateError

ENDPOINT = "https://api.mymemory.translated.net/get"

# 额度耗尽时 MyMemory 会把警告直接拼在译文里，必须识别出来
_QUOTA_MARKERS = (
    "MYMEMORY WARNING",
    "YOU USED ALL AVAILABLE FREE TRANSLATIONS",
    "QUERY LENGTH LIMIT",
)


class MyMemoryTranslator(BaseTranslator):
    name = "mymemory"
    max_chars = 450

    def __init__(self, cfg: dict | None = None):
        super().__init__(cfg)
        self.email: str = (cfg or {}).get("mymemory_email", "") or ""

    def _translate(self, text: str, src: str, tgt: str) -> str:
        s = get_lang(src)
        t = get_lang(tgt)
        # MyMemory 不支持 auto 检测，语种未知时按英文处理（这种情况极罕见：
        # Whisper 的语种检测几乎总能给出结果）
        src_code = (s.mymemory if s else "en-US") if src and src != "auto" else "en-US"
        tgt_code = t.mymemory if t else tgt
        pair = f"{src_code}|{tgt_code}"

        params = {"q": text, "langpair": pair}
        if self.email:
            params["de"] = self.email

        r = self.session.get(ENDPOINT, params=params, timeout=self.timeout)
        if r.status_code != 200:
            raise TranslateError(f"mymemory HTTP {r.status_code}")

        data = r.json()
        status = str(data.get("responseStatus", ""))
        if status not in ("200", "OK"):
            raise TranslateError(f"mymemory 状态异常: {data.get('responseDetails', status)}")

        out = (data.get("responseData") or {}).get("translatedText", "") or ""
        out = html.unescape(out).strip()

        if not out:
            raise TranslateError("mymemory 返回空译文")
        if any(m in out.upper() for m in _QUOTA_MARKERS):
            raise TranslateError("mymemory 免费额度已用尽")

        return out
