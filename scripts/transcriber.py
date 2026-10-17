"""Whisper 兜底：下载音频 + faster-whisper 转写（CPU/GPU 自适应，无需系统 ffmpeg）"""
import os
import re
import socket
import sys
import tempfile
import time
import requests

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com"}

class WhisperNotInstalled(Exception):
    """faster-whisper 未安装"""

_FILLER_RULES = [
    (r"([啊哦嗯哎])\1{1,}", r"\1"),      # 语气单字：啊啊啊→啊（2+ 缩到 1）
    (r"(.)\1{2,}", r"\1\1"),            # 重复字：对对对→对对（3+ 缩到 2）
    (r"[ \t]{2,}", " "),                # 空白规范
]

def clean_text(text: str) -> str:
    """转写文本清洗（A3）：压缩语气词/重复字、规范空白。不改变时间戳与句意"""
    t = text.strip()
    for pat, rep in _FILLER_RULES:
        t = re.sub(pat, rep, t)
    return t.strip()

def _load_whisper():
    try:
        from faster_whisper import WhisperModel
        return WhisperModel
    except ImportError as e:
        raise WhisperNotInstalled(
            "未安装 faster-whisper，请执行: pip install faster-whisper"
        ) from e


def whisper_available() -> bool:
    """faster-whisper 可用性探测（低配降级前置判断；不触发下载/加载）"""
    try:
        _load_whisper()
        return True
    except WhisperNotInstalled:
        return False

def _ensure_nvidia_paths() -> None:
    """C2: 自动探测 site-packages 下 nvidia 运行库（cuBLAS/cuDNN/nvrtc）并注入 PATH。
    pip 安装的 nvidia-cublas-cu12 / nvidia-cudnn-cu12 将 DLL 放在 site-packages/nvidia/*/bin，
    ctranslate2 按 PATH 查找，不注入则 GPU 转写报 cublas64_12.dll not found。"""
    import site as _site
    candidates = ["nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_nvrtc/bin"]
    for sp in _site.getsitepackages():
        for sub in candidates:
            p = os.path.join(sp, sub.replace("/", os.sep))
            if os.path.isdir(p) and p not in os.environ.get("PATH", ""):
                os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]


def detect_device() -> str:
    """有 CUDA 用 cuda，否则 cpu（faster-whisper 基于 ctranslate2，无需 torch）"""
    _ensure_nvidia_paths()
    try:
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except ImportError:
        return "cpu"

VAD_LONG_SEC_DEFAULT = 1800.0

def _probe_duration(audio_path: str) -> float:
    """尽力探测音频时长（秒）；失败返回 0（按短视频策略处理）"""
    try:
        import av
        container = av.open(audio_path)
        try:
            if container.duration:
                return float(container.duration) / 1_000_000  # av.time_base = 1e6
            st = container.streams.audio[0]
            if st.duration and st.time_base:
                return float(st.duration * st.time_base)
            return 0.0
        finally:
            container.close()
    except Exception:
        return 0.0

def _vad_plan(vad, duration_sec, vad_long_sec: float = VAD_LONG_SEC_DEFAULT) -> list:
    """VAD 尝试顺序：
    - 显式指定 → 单次；长视频默认先关（防长音频 VAD 切碎/漏内容，可 --vad 手动开）；
    - 短视频保持“先开、滤空则关”的原有兜底"""
    if vad is not None:
        return [bool(vad)]
    if duration_sec and float(duration_sec) >= float(vad_long_sec):
        return [False]
    return [True, False]

_RETRY_STATUS = (408, 429, 500, 502, 503, 504)


def _is_fatal_download_error(e) -> bool:
    """不可重试错误：DNS 解析失败（域名不存在）——非瞬态，应快速失败/换候选。"""
    cur, seen = e, set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, socket.gaierror):
            return True
        msg = str(cur)
        if ("NameResolutionError" in msg or "Failed to resolve" in msg
                or "getaddrinfo failed" in msg):
            return True
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
    return False


def download_audio(url, dest: str, headers: dict = None, attempts: int = 4,
                   backoff: float = 1.5, max_seconds: float = 180.0) -> str:
    """下载媒体到 dest：候选 URL 依次回退 + 瞬态重试 + 断点续传 + 总时长预算。

    - 瞬态错误（连接/超时/SSL/读中断，如 IncompleteRead）：已写入部分自动带 `Range` 续传
      （googlevideo 等 CDN 支持 206）；**有进展的失败不计入连续失败上限**（长文件可分多轮拿到）；
    - attempts：连续「无进展」失败上限（超出则换下一候选）；硬保护：单候选 ≤24 轮；
    - HTTP 4xx（除 408/429）：候选失效（签名过期/拒签）→ 立即换下一候选；
    - 429/5xx/408：可重试；DNS 解析失败：非瞬态，快速失败；
    - max_seconds：单次调用总时长预算（默认 180s，含全部候选/轮次）；耗尽即抛最后一次异常，
      不阻塞流水线（调用方降级/记录）。
    """
    candidates = [url] if isinstance(url, str) else [u for u in (url or []) if u]
    if not candidates:
        raise ValueError("download_audio: 无候选媒体 URL")
    deadline = time.monotonic() + max_seconds if max_seconds else None
    last_exc = None
    for ci, cand in enumerate(candidates, 1):
        pos = 0
        fails = 0          # 连续「无进展」失败数（有续传进展则归零；上限 attempts）
        rounds = 0
        while fails < attempts and rounds < 24:
            rounds += 1
            if deadline is not None and time.monotonic() >= deadline:
                break
            start_pos = pos
            try:
                h = headers or HEADERS
                if pos > 0:
                    h = dict(h)
                    h["Range"] = f"bytes={pos}-"
                r = requests.get(cand, headers=h, stream=True, timeout=60)
                status = int(getattr(r, "status_code", 200) or 200)
                if 400 <= status < 500 and status not in (408, 429):
                    last_exc = requests.exceptions.HTTPError(f"HTTP {status}")
                    if len(candidates) > 1:
                        print(f"  [media] 候选 {ci}/{len(candidates)} 不可用（HTTP {status}），"
                              "换下一候选", file=sys.stderr)
                    break                                  # 换下一候选
                if status >= 400:                          # 429/5xx/408 → 可重试
                    raise requests.exceptions.HTTPError(f"HTTP {status}")
                r.raise_for_status()
                if pos > 0 and status != 206:              # 服务器忽略 Range → 从头下载
                    pos = 0
                mode = "ab" if pos > 0 else "wb"
                with open(dest, mode) as f:
                    for chunk in r.iter_content(chunk_size=65536):   # 64KB：中断时保留已收数据（续传有效）
                        if chunk:
                            f.write(chunk)
                            pos += len(chunk)
                            if deadline is not None and time.monotonic() >= deadline:
                                # trickle 流（缓慢持续有数据）也必须在预算内中止
                                raise requests.exceptions.Timeout("下载时长预算耗尽（max_seconds）")
                return dest
            except requests.exceptions.HTTPError as e:
                last_exc = e
                fails += 1
                if fails < attempts and rounds < 24:
                    time.sleep(backoff * (2 ** max(0, fails - 1)))
                elif len(candidates) > 1:
                    print(f"  [media] 候选 {ci}/{len(candidates)} 重试耗尽"
                          f"（{str(e)[:60]}），换下一候选", file=sys.stderr)
            except Exception as e:                         # 连接/超时/SSL/读中断等瞬态
                last_exc = e
                if str(e).startswith("下载时长预算耗尽"):
                    break                                  # 预算耗尽：不再重试/换候选
                if _is_fatal_download_error(e):            # DNS 解析失败等 → 不重试
                    if len(candidates) > 1:
                        print(f"  [media] 候选 {ci}/{len(candidates)} 解析失败"
                              f"（{str(e)[:60]}），换下一候选", file=sys.stderr)
                    break
                progressed = pos > start_pos
                fails = 0 if progressed else fails + 1
                if fails < attempts and rounds < 24:
                    tag = f"已收 {pos} 字节，续传" if progressed else "重试"
                    print(f"  [media] 下载中断（候选 {ci}/{len(candidates)}，第 {rounds} 轮，{tag}）："
                          f"{str(e)[:70]}", file=sys.stderr)
                    time.sleep(backoff * (2 ** max(0, fails - 1)))
                elif len(candidates) > 1:
                    print(f"  [media] 候选 {ci}/{len(candidates)} 重试耗尽"
                          f"（已收 {pos} 字节），换下一候选", file=sys.stderr)
        if deadline is not None and time.monotonic() >= deadline:
            print(f"  [media] 下载时长预算（{max_seconds:.0f}s）耗尽，放弃", file=sys.stderr)
            break
    raise last_exc if last_exc else RuntimeError("download_audio: 下载失败")

def transcribe(audio_path: str, model_size: str = "medium", language: str = "zh",
                progress_callback=None, vad=None, duration_sec: float = None,
                vad_long_sec: float = VAD_LONG_SEC_DEFAULT, stats: dict = None) -> list:
    """转写音频 → [{start,end,text}]；faster-whisper 用 PyAV 解码，无需系统 ffmpeg。
    - GPU 检测误报/库缺失（cublas DLL 等）→ 自动回退 CPU int8
    - VAD：显式 vad=True/False 强制；默认（auto）长视频禁用、短视频先开滤空则关
    - progress_callback(done_seconds, total_seconds) 可选进度回调
    - stats（可选 dict 出参）：回填置信度（avg_logprob/no_speech_ratio/language(_probability)）"""
    WhisperModel = _load_whisper()
    device = detect_device()
    duration = duration_sec if duration_sec else _probe_duration(audio_path)
    plan = _vad_plan(vad, duration, vad_long_sec)
    try:
        return _transcribe_with(WhisperModel, model_size, device, audio_path, language,
                                progress_callback, plan, stats=stats)
    except RuntimeError as e:
        if device == "cpu":
            raise
        print(f"GPU 转写失败（{str(e)[:60]}），回退 CPU…", file=sys.stderr)
        return _transcribe_with(WhisperModel, model_size, "cpu", audio_path, language,
                                progress_callback, plan, stats=stats)


def _collect_stats(stats: dict, seg_list: list, info) -> None:
    """whisper 置信度采集（低配质量检测用）"""
    lps = [x for x in (getattr(s, "avg_logprob", None) for s in seg_list)
           if isinstance(x, (int, float))]
    nsp = [bool((getattr(s, "no_speech_prob", 0) or 0) > 0.5) for s in seg_list]
    stats.update({
        "avg_logprob": (sum(lps) / len(lps)) if lps else None,
        "no_speech_ratio": (sum(nsp) / len(nsp)) if nsp else 0.0,
        "language": getattr(info, "language", None) if info is not None else None,
        "language_probability": (getattr(info, "language_probability", None)
                                 if info is not None else None),
        "segments": len(seg_list),
    })


def _transcribe_with(WhisperModel, model_size: str, device: str, audio_path: str,
                     language: str, progress_callback=None,
                     vad_plan: tuple = (True, False), stats: dict = None) -> list:
    compute = "int8" if device == "cpu" else "float16"
    model = WhisperModel(model_size, device=device, compute_type=compute)
    for vad in vad_plan:
        # faster-whisper >=1.2 移除 progress_callback；旧版/测试的 FakeModel 仍用它
        try:
            segments, info = model.transcribe(audio_path, language=language, vad_filter=vad,
                                              progress_callback=progress_callback)
        except TypeError:
            segments, info = model.transcribe(audio_path, language=language, vad_filter=vad,
                                              log_progress=progress_callback is not None)
        seg_list = list(segments)
        lines = [{"start": round(s.start, 2), "end": round(s.end, 2),
                  "text": clean_text(s.text)}  # A3：清洗语气词/重复
                 for s in seg_list]
        if lines:
            if stats is not None:
                _collect_stats(stats, seg_list, info)
            return lines
    return []

def transcribe_url(url, model_size: str = "medium", progress_callback=None,
                   vad=None, duration_sec: float = None,
                   vad_long_sec: float = VAD_LONG_SEC_DEFAULT, headers: dict = None,
                   language: str = "zh", stats: dict = None,
                   max_seconds: float = None) -> list:
    """平台无关入口：下载媒体直链（临时目录）+ 转写；vad/duration 透传（长视频默认禁 VAD）

    url：媒体直链或候选列表（core 多候选回退，见 download_audio）。
    headers：媒体 CDN 可能要求平台特定 UA/Referer（扣音等）；默认用内置 HEADERS（B 站向）。
    language：默认 zh（B 站向）；多语平台（如 X）传 None 自动检测。
    stats：可选 dict 出参，回填置信度（质量检测用）。"""
    with tempfile.TemporaryDirectory() as td:
        audio_path = download_audio(url, os.path.join(td, "audio.m4s"), headers=headers,
                                    max_seconds=(max_seconds if max_seconds else 180.0))
        return transcribe(audio_path, model_size=model_size, progress_callback=progress_callback,
                          vad=vad, duration_sec=duration_sec, vad_long_sec=vad_long_sec,
                          language=language, stats=stats)


def transcribe_video(client, bvid: str, cid: int, **kwargs) -> list:
    """兼容封装：从平台的音频直链接口取 URL 后转写"""
    return transcribe_url(client.get_audio_url(bvid, cid), **kwargs)


def transcribe_video_with_progress(client, bvid: str, cid: int, model_size: str = "medium",
                                   progress_callback=None, **kwargs) -> list:
    """带进度回调的转写（保留旧接口；内部映射为 log_progress）"""
    return transcribe_video(client, bvid, cid, model_size=model_size,
                            progress_callback=progress_callback, **kwargs)


def extract_audio_clip(path: str, start_sec: float, end_sec: float, sample_rate: int = 16000):
    """PyAV 截取音频片段 → (np.float32 单声道 16k 数组, sample_rate)"""
    import av
    import numpy as np
    container = av.open(path)
    try:
        stream = container.streams.audio[0]
        tbase = float(stream.time_base)
        try:
            container.seek(int(start_sec / tbase), stream=stream, backward=True)
        except Exception:
            pass
        resampler = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)
        chunks = []
        for frame in container.decode(stream):
            ts = float(frame.pts * tbase) if frame.pts is not None else 0.0
            if ts + frame.samples / sample_rate < start_sec:
                continue
            if ts > end_sec:
                break
            out_frames = resampler.resample(frame)
            if out_frames is None:  # 极老版本可能返回 None
                continue
            if not isinstance(out_frames, (list, tuple)):  # PyAV<9 返回单帧，新版返回列表
                out_frames = [out_frames]
            for out in out_frames:
                arr = out.to_ndarray().reshape(-1)
                chunks.append(arr)
        if chunks:
            pcm = np.concatenate(chunks).astype(np.float32) / 32768.0
        else:
            pcm = np.zeros(0, dtype=np.float32)
        return pcm, sample_rate
    finally:
        container.close()


def transcribe_samples_info(samples, sample_rate: int = 16000, model_size: str = "small",
                            language: str = None):
    """短片段转写（同源校验用）→ (text, asr_language)；语言用于翻译轨识别"""
    WhisperModel = _load_whisper()
    device = detect_device()
    model = WhisperModel(model_size, device=device,
                         compute_type="int8" if device == "cpu" else "float16")
    segments, info = model.transcribe(samples, language=language, vad_filter=False)
    lang = getattr(info, "language", "") or ""
    return clean_text(" ".join(s.text for s in segments)), lang


def transcribe_samples(samples, sample_rate: int = 16000, model_size: str = "small",
                       language: str = None) -> str:
    """短片段转写（同源校验用）→ 纯文本（兼容旧接口）"""
    return transcribe_samples_info(samples, sample_rate=sample_rate,
                                   model_size=model_size, language=language)[0]
