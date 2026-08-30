"""翻译层：可插拔后端 + 缓存 + 失败降级。"""

from .translator import Translator, BACKENDS
from .base import BaseTranslator, TranslateError

__all__ = ["Translator", "BaseTranslator", "TranslateError", "BACKENDS"]
