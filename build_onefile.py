"""
打包脚本：生成单文件 exe。

产物在 dist/tscy.exe，把它和 assets/、config/ 一起发给用户即可。
首次运行会在 exe 旁边自动创建 config.json、cache/、logs/、models/。
"""

import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent

# 清理旧产物（忽略沙箱/权限导致的删除失败）
for p in [ROOT / "build", ROOT / "dist" / "tscy.exe", ROOT / "tscy.spec"]:
    try:
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    except OSError:
        pass

args = [
    str(ROOT / "main.py"),
    "--onefile",
    "--name", "tscy",
    "--icon", str(ROOT / "assets" / "logo.ico"),
    "--noconsole",
    "--clean",
    "--add-data", f"{ROOT / 'assets'};assets",
    # 关键隐藏导入，避免打包后找不到 C 扩展或子模块
    "--hidden-import", "faster_whisper",
    "--hidden-import", "ctranslate2",
    "--hidden-import", "onnxruntime",
    "--hidden-import", "av",
    "--hidden-import", "dashscope",
    "--hidden-import", "dashscope.audio.asr",
    "--hidden-import", "dashscope.audio.tts_v2",
    "--hidden-import", "pystray",
    "--hidden-import", "PIL",
    "--hidden-import", "pyttsx3.drivers",
    "--hidden-import", "pyttsx3.drivers.sapi5",
    "--hidden-import", "comtypes",
    "--hidden-import", "comtypes.client",
    "--hidden-import", "keyboard",
    "--hidden-import", "requests",
    "--hidden-import", "edge_tts",
    "--collect-all", "faster_whisper",
    "--collect-all", "ctranslate2",
    "--collect-all", "dashscope",
    "--collect-all", "pystray",
]

PyInstaller.__main__.run(args)
print("\n打包完成: dist/tscy.exe")
