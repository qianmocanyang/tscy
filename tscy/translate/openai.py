"""
OpenAI 兼容接口翻译后端 —— 质量最好，且能理解游戏语境。

支持所有 OpenAI 兼容端点（OpenAI / DeepSeek / 通义 /  moonshot / 自建 vLLM 等），
改 openai_base_url 即可切换。

相比传统翻译 API 的独特优势：
    可以注入**术语表**，让模型知道 "gg=打得漂亮"、"rush B=冲B点"。
    这是游戏场景里传统翻译引擎永远翻不对的地方。
"""

from __future__ import annotations

from ..lang import display, get as get_lang
from .base import BaseTranslator, TranslateError

SYSTEM_TMPL = (
    "你是嵌入在游戏中的实时同声传译引擎。\n"
    "任务：把{src}翻译成{tgt}。\n"
    "规则：\n"
    "1. 只输出译文本身，不要任何解释、引号、前缀或换行说明。\n"
    "2. 保持口语化，像玩家之间对话那样自然，不要书面腔。\n"
    "3. 保留游戏术语、人名、地名的原有含义；缩写按玩家习惯处理。\n"
    "4. 原文若有语气词、脏话，按同强度表达，不要弱化。\n"
    "{glossary}"
)


class OpenAITranslator(BaseTranslator):
    name = "openai"
    max_chars = 1200

    def __init__(self, cfg: dict | None = None):
        super().__init__(cfg)
        cfg = cfg or {}
        self.key: str = cfg.get("openai_key", "") or ""
        self.base_url: str = (cfg.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/")
        self.model: str = cfg.get("openai_model", "gpt-4o-mini") or "gpt-4o-mini"
        self.glossary: dict = cfg.get("glossary", {}) or {}
        if self.key:
            self.session.headers.update({
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
            })

    def available(self) -> bool:
        return bool(self.key)

    def _system_prompt(self, src: str, tgt: str) -> str:
        if self.glossary:
            lines = "\n".join(f"  - {k} → {v}" for k, v in list(self.glossary.items())[:60])
            gloss = f"5. 遵循以下术语对照表：\n{lines}\n"
        else:
            gloss = ""
        # src 为空或 "auto" 表示语种没检测出来，让模型自己判断源语言
        src_desc = "原文" if (not src or src == "auto") else display(src)
        return SYSTEM_TMPL.format(src=src_desc, tgt=display(tgt), glossary=gloss)

    def _translate(self, text: str, src: str, tgt: str) -> str:
        if not self.key:
            raise TranslateError("openai 未配置 API Key（写进 config/secrets.json）")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt(src, tgt)},
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
            "max_tokens": 800,
        }

        r = self.session.post(
            f"{self.base_url}/chat/completions", json=payload, timeout=self.timeout
        )
        if r.status_code == 401:
            raise TranslateError("openai 401：API Key 无效")
        if r.status_code == 429:
            raise TranslateError("openai 429：速率限制或余额不足")
        if r.status_code != 200:
            raise TranslateError(f"openai HTTP {r.status_code}: {r.text[:120]}")

        data = r.json()
        try:
            out = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as e:
            raise TranslateError(f"openai 响应结构异常: {e}") from e

        # 有些模型会自作主张加引号，去掉
        if len(out) > 2 and out[0] == out[-1] and out[0] in "\"'「」《》":
            out = out[1:-1].strip()
        return out
