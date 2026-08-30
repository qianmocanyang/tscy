"""
游戏实时同声传译 (tscy) —— 命令行入口

常用命令：
    python main.py --list-devices          列出麦克风设备
    python main.py --selftest              自检（不依赖麦克风）
    python main.py                         按 config.json 启动
    python main.py --target ko --mode ptt  临时指定目标韩语 + 按住说话
    python main.py --model small           换更大模型（更准但更慢）
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 保证 Windows 控制台能正确显示中文/emoji，不会出现 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from tscy.config import CONFIG_FILE, Config, load_config          # noqa: E402
from tscy.lang import CYCLE_ORDER, display, is_supported           # noqa: E402
from tscy.log import get_logger, setup                             # noqa: E402

BANNER = r"""
  _____ _____ _____ _____
 |_   _/  ___/  __ \  _  |
   | | \ `--.| /  \/ | | |
   | |  `--. \ |   | | | |
   | | /\__/ / \__/\ \_/ /
   \_/ \____/ \____/\___/
     游戏实时同声传译  v1.0
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tscy",
        description="游戏实时同声传译：自动识别中/英/韩/日/俄，翻译成指定语言并输出字幕+语音",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"支持的语言: {', '.join(f'{c}={display(c)}' for c in CYCLE_ORDER)}",
    )
    p.add_argument("--mode", choices=["ptt", "two_step", "auto"],
                   help="触发模式：ptt=按住说话 / two_step=两步式 / auto=自动监听")
    p.add_argument("--target", choices=CYCLE_ORDER, help="目标语言")
    p.add_argument("--model", help="Whisper 模型：tiny base small medium large-v3 turbo")
    p.add_argument("--device", choices=["auto", "cpu", "cuda"], help="推理设备")
    p.add_argument("--config", type=Path, default=CONFIG_FILE, help="配置文件路径")
    p.add_argument("--log-level", dest="log_level",
                   choices=["DEBUG", "INFO", "WARN", "ERROR"], help="日志级别")
    p.add_argument("--no-tts", action="store_true", help="关闭语音播报")
    p.add_argument("--no-overlay", action="store_true", help="关闭字幕浮层")
    p.add_argument("--list-devices", action="store_true", help="列出麦克风设备并退出")
    p.add_argument("--selftest", action="store_true", help="自检并退出")
    p.add_argument("--with-model", action="store_true",
                   help="自检时同时加载 Whisper 模型（首次会下载）")
    return p


def collect_overrides(args: argparse.Namespace) -> dict:
    o: dict = {}
    if args.mode:
        o["mode"] = args.mode
    if args.target:
        o["target_lang"] = args.target
    if args.model:
        o.setdefault("asr", {})["model_size"] = args.model
    if args.device:
        o.setdefault("asr", {})["device"] = args.device
    if args.log_level:
        o.setdefault("log", {})["level"] = args.log_level
    if args.no_tts:
        o.setdefault("output", {})["speech"] = False
    if args.no_overlay:
        o.setdefault("output", {})["subtitle"] = False
    return o


# ============================ 自检 ============================

def _check_deps(log) -> bool:
    log.info("── 1/4 依赖检查 ──")
    deps = {
        "numpy": "numpy",
        "sounddevice": "sounddevice",
        "faster_whisper": "faster-whisper",
        "keyboard": "keyboard",
        "requests": "requests",
        "edge_tts": "edge-tts",
        "tkinter": "tkinter(标准库)",
    }
    ok = True
    for mod, label in deps.items():
        try:
            __import__(mod)
            log.info(f"  ✔ {label}")
        except Exception as e:
            log.error(f"  ✘ {label} —— 未安装或不可用 ({e})")
            ok = False

    try:
        import pyttsx3  # noqa: F401
        log.info("  ✔ pyttsx3 (离线兜底)")
    except Exception:
        log.warning("  ⚠ pyttsx3 不可用（只影响离线兜底播报，不影响主流程）")

    try:
        import tkinter  # noqa: F401
    except Exception:
        log.error("  ✘ tkinter 不可用，字幕浮层无法启动")
        ok = False
    return ok


def _check_devices(log) -> bool:
    log.info("── 2/4 音频设备 ──")
    try:
        from tscy.audio.capture import MicCapture
    except Exception as e:
        log.error(f"  ✘ 无法导入采集模块: {e}")
        return False

    devices = MicCapture.list_devices()
    if not devices:
        log.error("  ✘ 没找到任何输入设备")
        return False
    for d in devices:
        mark = " ← 默认" if d["default"] else ""
        log.info(f"  [{d['id']}] {d['name']}  {d['channels']}ch {int(d['rate'])}Hz{mark}")
    log.info("  若要用非默认麦克风，把 config.json 的 audio.device 改成对应 id")
    return True


def _check_vad(log) -> bool:
    """用合成音频验证 VAD 状态机和静音裁剪，不需要真麦克风。"""
    log.info("── 3/4 端点检测(VAD) 逻辑校验 ──")
    import numpy as np

    from tscy.audio.vad import EnergyVAD, dbfs, trim_silence

    sr, frame_ms = 16000, 30
    frame = int(sr * frame_ms / 1000)
    quiet = np.zeros(frame, dtype=np.float32)
    loud = (np.random.default_rng(0).normal(0, 0.15, frame)).astype(np.float32)

    ok = True
    lvl_q, lvl_l = dbfs(quiet), dbfs(loud)
    log.info(f"  静音电平 {lvl_q:.1f} dBFS / 说话电平 {lvl_l:.1f} dBFS")

    vad = EnergyVAD(frame_ms=frame_ms, start_db=-40, end_db=-45, start_frames=3, silence_ms=300)
    events = []
    for _ in range(5):
        events.append(vad.feed(quiet))
    for _ in range(30):
        events.append(vad.feed(loud))
    for _ in range(20):
        events.append(vad.feed(quiet))

    started = "start" in events
    ended = "end" in events
    log.info(f"  {'✔' if started else '✘'} 检测到起说事件")
    log.info(f"  {'✔' if ended else '✘'} 检测到说完事件")
    ok = started and ended

    seg = np.concatenate([quiet] * 10 + [loud] * 30 + [quiet] * 10)
    trimmed = trim_silence(seg, frame_ms=frame_ms, sample_rate=sr)
    orig_ms = len(seg) / sr * 1000
    trim_ms = len(trimmed) / sr * 1000
    good = 0 < trim_ms < orig_ms
    log.info(f"  {'✔' if good else '✘'} 静音裁剪: {orig_ms:.0f}ms → {trim_ms:.0f}ms")
    return ok and good


def _check_translate(log, cfg: Config) -> bool:
    log.info("── 4/4 翻译后端连通性 ──")
    from tscy.translate.translator import Translator

    tr = Translator(cfg.section("translate"))
    results = tr.selftest()
    ok = False
    for name, res in results.items():
        if res.startswith(("失败", "未配置")):
            log.warning(f"  ⚠ {name}: {res}")
        else:
            log.info(f"  ✔ {name}: {res}")
            ok = True
    primary = cfg.get("translate.backend", "google")
    if not ok:
        log.error("  所有翻译后端都不可用 —— 检查网络，或在 secrets.json 里配 Key")
    else:
        log.info(f"  当前主后端: {primary}")
    tr.close()
    return ok


def _check_model(log, cfg: Config, with_model: bool) -> None:
    from tscy.asr.whisper_engine import WhisperEngine

    asr_cfg = cfg.section("asr")
    engine = WhisperEngine(
        model_size=asr_cfg.get("model_size", "base"),
        device=asr_cfg.get("device", "auto"),
        compute_type=asr_cfg.get("compute_type", "auto"),
        model_dir=asr_cfg.get("model_dir", "models"),
        hf_endpoint=asr_cfg.get("hf_endpoint", "auto"),
    )
    model_dir = engine.model_dir
    cached = engine.has_cached_model()
    log.info(f"  模型目录: {model_dir}")
    log.info(f"  已缓存模型: {'是' if cached else '否（首次运行会自动下载）'}")

    if not cached:
        from tscy.asr.whisper_engine import HF_MIRROR, HF_OFFICIAL, _reachable
        ep = HF_OFFICIAL if _reachable(HF_OFFICIAL, 4.0) else HF_MIRROR
        log.info(f"  下载源: {ep}（官方站超时会自动切国内镜像）")

    if with_model:
        t0 = time.perf_counter()
        engine.load()
        log.info(f"  ✔ 模型加载成功（{int((time.perf_counter() - t0) * 1000)}ms）")


def run_selftest(cfg: Config, with_model: bool) -> int:
    log = get_logger("selftest")
    log.info("开始自检")
    ok = True
    ok &= _check_deps(log)
    ok &= _check_devices(log)
    ok &= _check_vad(log)
    ok &= _check_translate(log, cfg)
    _check_model(log, cfg, with_model)

    log.info("──" * 20)
    if ok:
        log.info("自检通过。可以运行: python main.py")
        return 0
    log.error("自检发现问题，按上面的提示修复后再运行")
    return 1


def list_devices() -> int:
    from tscy.audio.capture import MicCapture

    print("\n可用输入设备：")
    devices = MicCapture.list_devices()
    if not devices:
        print("  （无）")
        return 1
    for d in devices:
        mark = "  ← 当前默认" if d["default"] else ""
        print(f"  [{d['id']:>2}] {d['name']}")
        print(f"        {d['channels']} 声道 / {int(d['rate'])} Hz{mark}")
    print("\n把想用的设备 id 填到 config/config.json 的 audio.device（null = 系统默认）")
    return 0


# ============================ 主流程 ============================

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_devices:
        setup("INFO")
        return list_devices()

    if not is_supported(args.target or ""):
        if args.target:
            print(f"不支持的语言: {args.target}（可选 {', '.join(CYCLE_ORDER)}）")
            return 2

    cfg = load_config(args.config, collect_overrides(args))
    setup(cfg.get("log.level", "INFO"))

    if args.selftest:
        return run_selftest(cfg, args.with_model)

    print(BANNER)
    log = get_logger("main")

    try:
        from tscy.hotkey import is_admin
        from tscy.pipeline import Pipeline

        if not is_admin():
            log.warning(
                "⚠ 非管理员运行：全局热键很可能无效。请用管理员权限运行 run.bat"
            )

        pipeline = Pipeline(cfg)
        pipeline.start()
    except KeyboardInterrupt:
        log.info("收到 Ctrl+C，退出")
        return 0
    except Exception as e:
        log.error(f"启动失败: {e}", exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
