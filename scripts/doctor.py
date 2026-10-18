# -*- coding: utf-8 -*-
"""C1: bili doctor 环境自检
诊断 cookie / GPU(cuBLAS·cuDNN) / faster-whisper 版本兼容 / 模型缓存 / 输出目录，
输出 ✅/❌ 与修复建议，避免每次环境问题靠人工多轮排查。
"""
import os
import sys
import importlib.util
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILES = {
    "bilibili": os.path.join(_SCRIPT_DIR, ".bili_cookie"),
    "zhihu": os.path.join(_SCRIPT_DIR, ".zhihu_cookie"),
    "weibo": os.path.join(_SCRIPT_DIR, ".weibo_cookie"),
    "douyin": os.path.join(_SCRIPT_DIR, ".douyin_cookie"),
}
COOKIE_MAX_AGE_DAYS = 3


def check_cookie(client=None) -> bool:
    """B 站 cookie 有效性：nav 接口正常即有效；异常视为无效"""
    try:
        if client is None:
            from providers.bilibili import ApiClient
            client = ApiClient()
        return client.check_login()
    except Exception:
        return False


def cookie_status(client=None, now: float = None) -> list:
    """逐平台 cookie 状态（不盲称「有效」）：bilibili 真实探针；其余看文件与龄期。

    state：valid / invalid / missing / expired_risk / unverified。
    除 B 站外不做网络请求，避免 doctor 变成高频风控触发源。
    """
    now = time.time() if now is None else now
    rows = []
    for platform, path in COOKIE_FILES.items():
        row = {"platform": platform, "path": path, "age_days": None}
        if not os.path.exists(path):
            row.update(state="missing", label="未配置")
            rows.append(row)
            continue
        age = max(0.0, (now - os.path.getmtime(path)) / 86400.0)
        row["age_days"] = round(age, 1)
        if platform == "bilibili":
            ok = check_cookie(client)
            row.update(state="valid" if ok else "invalid",
                       label="有效" if ok else "无效/已过期，请重新导出")
        elif age > COOKIE_MAX_AGE_DAYS:
            row.update(state="expired_risk",
                       label=f"未验证（文件 {age:.0f} 天未更新，可能已过期）")
        else:
            row.update(state="unverified",
                       label=f"未验证（文件 {age:.1f} 天前导出）")
        rows.append(row)
    return rows


def check_gpu() -> dict:
    """GPU 可用性：ctranslate2 设备 + cuBLAS/cuDNN DLL 探测（缺失时给出 pip 修复命令）"""
    from transcriber import _ensure_nvidia_paths, detect_device
    _ensure_nvidia_paths()
    out = {"device": "cpu", "cublas_ok": False, "cudnn_ok": False,
           "hint": "CPU 模式可用（较慢）；安装运行库可启用 GPU：pip install nvidia-cublas-cu12 nvidia-cudnn-cu12"}
    try:
        device = detect_device()
        out["device"] = device
        if device != "cuda":
            return out
        import ctypes.util
        cublas = ctypes.util.find_library("cublas64_12") or (
            _dll_in_site_packages("cublas64_12.dll"))
        cudnn = ctypes.util.find_library("cudnn64_9") or (
            _dll_in_site_packages("cudnn64_9.dll"))
        out["cublas_ok"] = bool(cublas)
        out["cudnn_ok"] = bool(cudnn)
        if not (out["cublas_ok"] and out["cudnn_ok"]):
            out["hint"] = ("GPU 可用但缺少运行库 DLL："
                           "pip install nvidia-cublas-cu12 nvidia-cudnn-cu12")
        else:
            out["hint"] = "GPU 转写就绪"
    except Exception as e:
        out["hint"] = f"GPU 检查异常：{e}"
    return out


def _dll_in_site_packages(dll_name: str) -> str:
    import site
    for sp in site.getsitepackages():
        for root, _dirs, files in os.walk(os.path.join(sp, "nvidia")):
            if dll_name in files:
                return os.path.join(root, dll_name)
    return ""


def check_whisper() -> dict:
    """faster-whisper 安装与 API 兼容（>=1.2 用 log_progress，旧版用 progress_callback）"""
    out = {"installed": False, "compatible": False, "version": "", "hint": ""}
    if importlib.util.find_spec("faster_whisper") is None:
        out["hint"] = "未安装 faster-whisper：pip install faster-whisper"
        return out
    import inspect
    try:
        from faster_whisper import WhisperModel
    except Exception as e:                 # find_spec 成功但 import 失败（缺 DLL 等）
        out["hint"] = (f"faster-whisper 不可用：{str(e)[:100]}；"
                       "pip install --force-reinstall faster-whisper")
        return out
    out["installed"] = True
    try:
        from faster_whisper import __version__ as v
        out["version"] = v
    except Exception:
        out["version"] = "unknown"
    params = inspect.signature(WhisperModel.transcribe).parameters
    out["compatible"] = "log_progress" in params or "progress_callback" in params
    out["hint"] = "OK" if out["compatible"] else "API 不兼容：请升级 faster-whisper"
    return out


def check_models() -> dict:
    """Whisper 模型缓存（huggingface hub；目录不存在时返回空）"""
    cache = os.path.expanduser(r"~\.cache\huggingface\hub")
    models = []
    if os.path.isdir(cache):
        models = [d for d in os.listdir(cache) if d.startswith("models--Systran--faster-whisper")]
    return {"models": [m.replace("models--Systran--faster-whisper-", "") for m in models],
            "cache_dir": cache}


def check_vision() -> dict:
    """vision 配置状态与复检档位策略（D2：旧 subtitle_check 死键退役，改展示 recheck.tier_policy）"""
    try:
        from core import config
        cfg = config.load_config()
        v = cfg.get("vision") or {}
        rc = cfg.get("recheck") or {}
    except Exception:
        v, rc = {}, {}
    return {"enabled": bool(v.get("enabled")),
            "tier_policy": rc.get("tier_policy", "auto")}


def check_ytdlp() -> dict:
    """yt-dlp（YouTube 适配器依赖，可选）"""
    try:
        import yt_dlp
        if yt_dlp is None:
            raise ImportError("yt_dlp")
        return {"installed": True, "version": getattr(yt_dlp.version, "__version__", "unknown"),
                "hint": "OK（YouTube 适配器可用；如遇提取失败先升级：pip install -U yt-dlp）"}
    except Exception as e:
        return {"installed": False, "version": "",
                "hint": f"不可用：{str(e)[:80]}；pip install -U yt-dlp（YouTube 适配器需要）"}


def _has_module(name: str) -> bool:
    """模块可导入性检查（纯本地，不联网）"""
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def missing_optional() -> list:
    """缺失的可选依赖（纯本地检查）：[{name, hint}]；供引导/质量强制推荐使用"""
    from core import config as core_config
    cfg = core_config.load_config()
    out = []
    if not _has_module("faster_whisper"):
        out.append({"name": "faster-whisper",
                    "hint": "pip install faster-whisper（Whisper 转写；时间线平台需要）"})
    if not _has_module("yt_dlp"):
        out.append({"name": "yt-dlp",
                    "hint": "pip install yt-dlp（YouTube / 抖音 适配器）"})
    if not _has_module("playwright"):
        out.append({"name": "playwright",
                    "hint": "pip install playwright（cookie 一键导出 / browserfetch 平台）"})
    if not (cfg.get("vision") or {}).get("enabled"):
        out.append({"name": "vision（可选）",
                    "hint": "配置 config.vision（多模态画面复检；不装仍可用，复检将跳过）"})
    return out


def _ensure_utf8_stdout() -> None:
    """Windows GBK 控制台无法编码 emoji（✅/❌/👁️）→ stdout 重配置为 utf-8（失败则忽略）"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def check_out_dir(path: str = "") -> dict:
    """输出目录可写性"""
    path = path or os.getcwd()
    try:
        os.makedirs(path, exist_ok=True)
        test = os.path.join(path, ".write_test")
        open(test, "w").close()
        os.remove(test)
        return {"ok": True, "path": path}
    except Exception as e:
        return {"ok": False, "path": path, "error": str(e)}


def describe_out_dir() -> dict:
    """输出目录状态：读 config.out_dir。未配置时明确标记（configured=False），
    避免把 cwd 误报为可用输出目录（run 实际会拒绝运行）。"""
    try:
        from core import config
        path = (config.load_config().get("out_dir") or "").strip()
    except Exception:
        path = ""
    if not path:
        return {"configured": False, "ok": False, "path": ""}
    result = check_out_dir(path)
    result["configured"] = True
    return result


def main(argv=None) -> int:
    """CLI 入口。bili.py 子命令 doctor 调用本函数。"""
    _ensure_utf8_stdout()
    from providers import bilibili  # noqa: F401  确保适配器可导入（cookie 检查用）
    print("=== everything-learn 环境自检 ===")
    # cookie
    rows = cookie_status()
    for r in rows:
        icon = {"valid": "✅", "invalid": "❌", "missing": "❌",
                "expired_risk": "⚠️", "unverified": "ℹ️"}.get(r["state"], "ℹ️")
        print(f"{icon} cookie[{r['platform']}]：{r['label']}")
    if any(r["state"] == "invalid" for r in rows):
        print("  提示：cookie 失效时用 python scripts/el.py cookie <平台> 一键重新导出")
    # GPU
    g = check_gpu()
    gpu_flag = "✅" if (g["device"] == "cuda" and g["cublas_ok"] and g["cudnn_ok"]) else "❌"
    print(f"{gpu_flag} GPU：设备={g['device']} cuBLAS={'✓' if g['cublas_ok'] else '✗'} cuDNN={'✓' if g['cudnn_ok'] else '✗'}")
    print(f"     {g['hint']}")
    # whisper
    w = check_whisper()
    print(f"{'✅' if w['installed'] and w['compatible'] else '❌'} faster-whisper："
          f"{'v'+w['version'] if w['version'] else '未安装'} 兼容={'✓' if w['compatible'] else '✗'} {w['hint']}")
    # 模型缓存
    m = check_models()
    if m["models"]:
        print(f"✅ 模型缓存：{', '.join(m['models'])}（{m['cache_dir']}）")
    else:
        print("⚠️ 模型缓存为空：首次转写将自动下载（国内网络可设 HF_ENDPOINT=https://hf-mirror.com）")
    # vision 复检档位（配置后告知用户可自选 auto/off/tier1/tier2）
    vi = check_vision()
    if vi["enabled"]:
        print(f"👁️ vision：已启用；复检档位策略={vi['tier_policy']}"
              f"（可选 auto/off/tier1/tier2，以你的选择为准）")
    # yt-dlp（YouTube 适配器）
    y = check_ytdlp()
    print(f"{'✅' if y['installed'] else '⚠️'} yt-dlp："
          f"{'v' + y['version'] if y['installed'] else '未安装'}（{y['hint']}）")
    # 输出目录（以 config.out_dir 为准；未配置明确提示，不误报 cwd）
    o = describe_out_dir()
    if not o["configured"]:
        print("⚠️ 输出目录：未配置（首次使用需自选：--out 指定或写入 config.json 的 out_dir）")
    else:
        suffix = "" if o["ok"] else f"（{o.get('error', '不可写')}）"
        print(f"{'✅' if o['ok'] else '❌'} 输出目录：{o['path']}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
