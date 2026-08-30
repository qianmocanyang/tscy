"""
语言注册表 —— 全系统语言代码的唯一真相来源。

设计约束：新增一种语言 = 只改这个文件的 LANGS 字典。
其余模块（ASR / 翻译 / TTS / 浮层）一律通过本模块取代码，禁止硬编码。

为什么需要这张表：
    各家的语言代码格式互不兼容 ——
    Whisper 要 "zh"，Google 要 "zh-CN"，DeepL 要 "ZH"，MyMemory 要 "zh-CN"，
    edge-tts 要完整音色名 "zh-CN-XiaoxiaoNeural"。
    如果没有统一注册表，每加一个后端就要写一堆 if/elif 做转换，极易出错。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Lang:
    """一种目标语言的全部表示形式。"""

    code: str            # 系统内部统一代码（config 里用的就是这个）
    name: str            # 显示名
    short: str           # 短标签，用于浮层左上角的小徽标
    whisper: str         # Whisper / faster-whisper 语种代码
    google: str          # Google 翻译端点代码
    mymemory: str        # MyMemory 代码
    deepl: str           # DeepL 代码
    tts: str             # edge-tts 默认音色
    font: str = "Microsoft YaHei UI"   # 浮层渲染该语言时优先使用的字体

    def code_for(self, backend: str) -> str:
        """按后端名取出对应的语言代码。"""
        return getattr(self, backend, self.code)


LANGS: dict[str, Lang] = {
    "zh": Lang(
        code="zh", name="中文", short="中",
        whisper="zh", google="zh-CN", mymemory="zh-CN", deepl="ZH",
        tts="zh-CN-XiaoxiaoNeural", font="Microsoft YaHei UI",
    ),
    "en": Lang(
        code="en", name="English", short="EN",
        whisper="en", google="en", mymemory="en-US", deepl="EN-US",
        tts="en-US-AriaNeural", font="Segoe UI",
    ),
    "ko": Lang(
        code="ko", name="한국어", short="한",
        whisper="ko", google="ko", mymemory="ko-KR", deepl="KO",
        tts="ko-KR-SunHiNeural", font="Malgun Gothic",
    ),
    "ja": Lang(
        code="ja", name="日本語", short="日",
        whisper="ja", google="ja", mymemory="ja-JP", deepl="JA",
        tts="ja-JP-NanamiNeural", font="Yu Gothic UI",
    ),
    "ru": Lang(
        code="ru", name="Русский", short="RU",
        whisper="ru", google="ru", mymemory="ru-RU", deepl="RU",
        tts="ru-RU-SvetlanaNeural", font="Segoe UI",
    ),
}

# 目标语言循环切换顺序（快捷键 ctrl+alt+l 用）
CYCLE_ORDER: list[str] = ["zh", "en", "ko", "ja", "ru"]

# Whisper 返回的语种代码 → 内部代码。
# Whisper 偶尔会吐出带地区后缀的代码（如 "zh-CN"），这里统一归一。
_WHISPER_ALIASES: dict[str, str] = {}
for _l in LANGS.values():
    _WHISPER_ALIASES[_l.whisper] = _l.code
    _WHISPER_ALIASES[_l.code] = _l.code


def get(code: str) -> Lang | None:
    """按内部代码取语言对象，不存在返回 None。"""
    if not code:
        return None
    return LANGS.get(code.lower())


def normalize_whisper(lang: str) -> str:
    """
    把 Whisper / faster-whisper 输出的语种代码归一到内部代码。

    例： "zh" -> "zh"，"en" -> "en"，未收录的语种原样返回（调用方会降级处理）。
    """
    if not lang:
        return ""
    lang = lang.strip().lower()
    return _WHISPER_ALIASES.get(lang, lang)


def is_supported(code: str) -> bool:
    return code.lower() in LANGS


def supported_codes() -> list[str]:
    return list(LANGS.keys())


def next_lang(current: str) -> str:
    """按 CYCLE_ORDER 取下一个语言，用于快捷键循环切换。"""
    try:
        i = CYCLE_ORDER.index(current.lower())
    except ValueError:
        return CYCLE_ORDER[0]
    return CYCLE_ORDER[(i + 1) % len(CYCLE_ORDER)]


def display(code: str) -> str:
    """人类可读名称，未知代码原样返回。"""
    l = get(code)
    return l.name if l else code
