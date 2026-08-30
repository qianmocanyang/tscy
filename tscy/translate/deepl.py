"""
DeepL 翻译后端 —— 质量最高的选择，需要 API Key。

Key 请写到 config/secrets.json： {"deepl_key": "xxxxx:fx"}
免费版额度 50 万字符/月，个人使用完全够。

注意：免费版 Key 结尾是 ":fx"，必须走 api-free.deepl.com，
      走付费域名会返回 403。用 deepl_free 配置切换。
"""

from __future__ import annotations

from ..lang import display, get as get_lang
from .base import BaseTranslator, TranslateError


class DeepLTranslator(BaseTranslator):
    name = "deepl"
    max_chars = 1000

    def __init__(self, cfg: dict | None = None):
        super().__init__(cfg)
        cfg = cfg or {}
        self.key: str = cfg.get("deepl_key", "") or ""
        self.free: bool = bool(cfg.get("deepl_free", True))
        if self.key:
            self.session.headers.update({"Authorization": f"DeepL-Auth-Key {self.key}"})

    def available(self) -> bool:
        return bool(self.key)

    @property
    def endpoint(self) -> str:
        return "https://api-free.deepl.com/v2/translate" if self.free else "https://api.deepl.com/v2/translate"

    def _translate(self, text: str, src: str, tgt: str) -> str:
        if not self.key:
            raise TranslateError("deepl 未配置 API Key（写进 config/secrets.json）")

        t = get_lang(tgt)
        payload: dict = {
            "text": [text],
            "target_lang": (t.deepl if t else tgt.upper()),
        }
        # 源语言已知时带上，能明显提升质量
        # DeepL 的 source_lang 不接受地区后缀，只接受 EN / ZH / KO / JA / RU
        # src == "auto" 表示语种未检测出来，此时不传该字段，让 DeepL 自己判断
        if src and src.lower() != "auto":
            s = get_lang(src)
            src_code = (s.deepl if s else src).split("-")[0].upper()
            payload["source_lang"] = src_code

        r = self.session.post(self.endpoint, json=payload, timeout=self.timeout)
        if r.status_code == 403:
            raise TranslateError("deepl 403：Key 无效，或免费 Key 用错了付费域名")
        if r.status_code == 456:
            raise TranslateError("deepl 456：本月额度已用尽")
        if r.status_code != 200:
            raise TranslateError(f"deepl HTTP {r.status_code}: {r.text[:120]}")

        data = r.json()
        tr = data.get("translations") or []
        if not tr:
            raise TranslateError("deepl 返回空译文")
        return (tr[0].get("text") or "").strip()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DeepLTranslator free={self.free} configured={bool(self.key)} target={display}>"
