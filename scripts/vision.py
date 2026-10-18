"""第三条路径：多模态大模型 API 视觉转写（OpenAI 兼容）

当字幕不可信且音频无人声（whisper 转写为空/覆盖不足）时，
抽帧交给多模态大模型（支持视觉的 chat 模型）转写画面内容，
再交由 harness 内 LLM 按 skill 规则总结。
"""
import base64
import io
import re
import requests

DEFAULT_PROMPT = (
    "你是视频内容转写助手。以下是一段视频按时间顺序抽取的画面帧（每帧标注时间戳）。"
    "请按帧顺序详细描述每帧画面内容：画面中的文字、图表、代码、操作步骤、关键信息都要尽量完整转录。"
    "输出格式：每行一条 `[时间戳秒] 描述`。如果多帧内容连续，可合并描述并标注起止时间。"
    "请用中文输出。"
)

class VisionError(Exception):
    """多模态 API 相关错误"""


def extract_frames(video_path: str, interval_sec: int = 10, max_frames: int = 24) -> list:
    """用 PyAV 抽帧 → [(timestamp_sec, jpeg_bytes)]"""
    import av
    container = av.open(video_path)
    try:
        stream = container.streams.video[0]
        fps = float(stream.average_rate or 25.0)
        step = max(1, int(interval_sec * fps))
        frames = []
        for i, frame in enumerate(container.decode(stream)):
            if i % step != 0:
                continue
            ts = int(i / fps)
            img = frame.to_image().convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
            frames.append((ts, buf.getvalue()))
            if len(frames) >= max_frames:
                break
        return frames
    finally:
        container.close()


def _encode_data_uri(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


def describe_video(cfg_vision: dict, frames: list, prompt: str = None) -> str:
    """调 OpenAI 兼容 chat completions（多模态）→ 画面转写文本"""
    if not cfg_vision.get("enabled"):
        raise VisionError("多模态 API 未启用：请在 config.json 的 vision 块设置 enabled=true")
    if not cfg_vision.get("api_key") or not cfg_vision.get("base_url"):
        raise VisionError("多模态 API 未配置完整：config.json 的 vision 块需要 base_url/api_key/model")
    url = cfg_vision["base_url"].rstrip("/") + "/chat/completions"
    content = [{"type": "text", "text": prompt or DEFAULT_PROMPT}]
    for ts, jpeg in frames:
        content.append({"type": "text", "text": f"[帧时间 {ts}s]"})
        content.append({"type": "image_url", "image_url": {"url": _encode_data_uri(jpeg)}})
    payload = {
        "model": cfg_vision.get("model", "gpt-4o-mini"),
        "messages": [{"role": "user", "content": content}],
        "max_tokens": int(cfg_vision.get("max_tokens") or 8192),
    }
    session = requests.Session()
    session.trust_env = False  # 不继承环境代理（requests.post 不接受 trust_env 参数，直接传会 TypeError）
    for attempt in range(3):
        r = session.post(url, json=payload,
                         headers={"Authorization": f"Bearer {cfg_vision['api_key']}"},
                         timeout=300)
        r.raise_for_status()
        data = r.json()
        try:
            choice = data["choices"][0]
            text = ((choice.get("message") or {}).get("content") or "").strip()
        except (KeyError, IndexError, TypeError):
            raise VisionError(f"多模态 API 返回格式异常：{str(data)[:200]}")
        if text:
            return text
        # 推理型模型（如 deepseek-flash）可能把 max_tokens 全用在 reasoning_content → 空正文
        if choice.get("finish_reason") == "length" and attempt < 2:
            payload["max_tokens"] *= 2
            continue
        raise VisionError("多模态返回空内容（可能 max_tokens 被推理占满）；"
                          "请提高 vision.max_tokens 或缩小复检窗口")


def vision_transcribe(cfg_vision: dict, video_path: str, prompt: str = None) -> str:
    """完整流程：抽帧 → 多模态转写 → 文本"""
    frames = extract_frames(video_path,
                            cfg_vision.get("frame_interval", 10),
                            cfg_vision.get("max_frames", 24))
    if not frames:
        raise VisionError("视频抽帧失败（无视频流或视频不可解码）")
    return describe_video(cfg_vision, frames, prompt)


def extract_frames_range(video_path: str, start_sec: float, end_sec: float,
                         fps: int = 1, max_frames: int = 30) -> list:
    """区间内按 fps 抽帧（默认每秒 1 帧）→ [(timestamp_sec, jpeg_bytes)]"""
    import av
    container = av.open(video_path)
    try:
        stream = container.streams.video[0]
        frames = []
        last_ts = None
        min_delta = (1.0 / max(1, fps)) - 0.01
        for frame in container.decode(stream):
            ts = float(frame.pts * stream.time_base) if frame.pts is not None else start_sec
            if ts < start_sec - 0.5:
                continue
            if ts > end_sec:
                break
            if last_ts is not None and ts - last_ts < min_delta:
                continue
            img = frame.to_image().convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
            frames.append((int(ts + 0.5), buf.getvalue()))
            last_ts = ts
            if len(frames) >= max_frames:
                break
        return frames
    finally:
        container.close()


def extract_frames_at(video_path: str, timestamps: list) -> list:
    """对指定时间点各取最近一帧（seek 优化，长视频无需从头解码）→ [(秒, jpeg)]"""
    import av
    out = []
    for ts in sorted(set(round(float(t), 2) for t in timestamps)):
        container = av.open(video_path)
        try:
            stream = container.streams.video[0]
            tbase = float(stream.time_base)
            try:
                container.seek(int(ts / tbase), stream=stream, backward=True)
            except Exception:
                try:
                    container.seek(0)
                except Exception:
                    pass
            best = None
            for frame in container.decode(stream):
                fts = float(frame.pts * stream.time_base) if frame.pts is not None else ts
                if fts > ts + 0.05:
                    break
                best = frame
            if best is None:
                continue
            img = best.to_image().convert("RGB")
            if img.width > 640:  # 控体积：复检判断主题无需原始分辨率
                img = img.resize((640, max(1, int(img.height * 640 / img.width))))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
            out.append((int(round(ts)), buf.getvalue()))
        finally:
            container.close()
    return out


ATTRIBUTION_PROMPT = (
    "你是学习报告工具的「字幕归属校验」助手。以下是同一视频 {n} 个时间点的画面帧，"
    "以及各时间点附近的字幕文本。请逐点判断：该点画面内容与字幕是否属于同一内容/语言"
    "（主题一致、字幕不像来自其他视频的台词/歌词/广告）。\n"
    "输出格式：每行一条 `[时间戳秒] 一致` 或 `[时间戳秒] 不一致：简短理由`。只输出这些行。"
)


def check_attribution_aligned(cfg_vision, video_path, lines, duration, points=5):
    """时间对齐多点视觉归属复检（无音频流兜底）。
    每点取帧 + 该点 ±5s 字幕；采样点均匀分布于 5%~95%。
    抽帧/API 异常向上抛（基础设施失败，由调用方降级）；
    duration/lines 无效或解析点数不足 → passed=False（fail-safe）。
    → {"passed": bool, "verdicts": [{ts, match}], "checked": bool, "reason": str}；
    reason ∈ {"", "invalid_input", "insufficient_verdicts"}。"""
    dur = float(duration or 0)
    if dur <= 0 or not lines:
        return {"passed": False, "verdicts": [], "checked": False,
                "reason": "invalid_input"}
    n = max(3, min(5, int(points)))
    fractions = [0.05 + 0.9 * i / (n - 1) for i in range(n)]
    times = [max(0.5, dur * f) for f in fractions]
    frames = extract_frames_at(video_path, times)
    if not frames:
        raise VisionError("抽帧失败（无法解码或视频流不可用）")
    ctx = []
    for ts, _jpeg in frames:
        near = " ".join(l["text"] for l in lines if abs(float(l["start"]) - float(ts)) <= 5.0)
        ctx.append(f"[{int(float(ts))}s] 字幕：{near or '（无）'}")
    prompt = (ATTRIBUTION_PROMPT.format(n=len(frames))
              + "\n\n【各时间点字幕文本】\n" + "\n".join(ctx))
    text = describe_video(cfg_vision, frames, prompt=prompt)
    # 按 ts 取整去重（保留末次投票），再过滤到实际采样点（±1s 容差）
    by_ts = {}
    for m in re.finditer(r"\[(\d+(?:\.\d+)?)\]\s*(一致|不一致)", text or ""):
        by_ts[int(round(float(m.group(1))))] = m.group(2) == "一致"
    sampled_ints = {int(round(t)) for t in times}
    verdicts = [{"ts": k, "match": v} for k, v in by_ts.items()
                if any(abs(k - s) <= 1 for s in sampled_ints)]
    if len(verdicts) < 3:
        return {"passed": False, "verdicts": verdicts, "checked": False,
                "reason": "insufficient_verdicts"}
    mismatches = sum(1 for v in verdicts if not v["match"])
    return {"passed": mismatches <= 1, "verdicts": verdicts, "checked": True,
            "reason": ""}


def _thumb_diff(a: bytes, b: bytes) -> float:
    """两帧缩略图平均像素差（0~1）；解码失败视为不同帧"""
    from PIL import Image
    import numpy as np
    try:
        ta = np.asarray(Image.open(io.BytesIO(a)).convert("L").resize((32, 32)), dtype=np.float32)
        tb = np.asarray(Image.open(io.BytesIO(b)).convert("L").resize((32, 32)), dtype=np.float32)
    except Exception:
        return 1.0
    return float(np.abs(ta - tb).mean() / 255.0)


def deps_available() -> bool:
    """画面复检依赖（Pillow/PyAV）是否就绪（低配环境预检，避免逐窗报错）"""
    try:
        import PIL  # noqa: F401
        import av  # noqa: F401
        return True
    except ImportError:
        return False


def sample_window_frames(video_path, start, end, fps=3, max_side=640,
                         dedup=True, dedup_threshold=0.02, max_frames=100000):
    """窗口抽帧：fps 帧/秒（默认 3=头中尾），缩放到 max_side，静帧去重。
    → [(ts, jpeg)]"""
    from PIL import Image
    raw = extract_frames_range(video_path, start, end, fps=fps, max_frames=max_frames)
    out = []
    for ts, jpeg in raw:
        img = Image.open(io.BytesIO(jpeg)).convert("RGB")
        if max_side and img.width > max_side:
            img = img.resize((max_side, max(1, int(img.height * max_side / img.width))))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        data = buf.getvalue()
        if dedup and out and _thumb_diff(out[-1][1], data) < dedup_threshold:
            continue
        out.append((ts, data))
    return out


WINDOW_CHECK_PROMPT = (
    "你是学习报告工具的「画面保真复检」助手。以下是一个时间窗内的画面帧（每秒 3 帧：头/中/尾，"
    "每帧标注时间戳）与同一窗口的转写文本。\n"
    "先逐帧读取画面中所有可见文字（标题/表格/图表/代码/字幕），再与转写文本逐项对照。\n"
    "必须检查以下两类硬性差异，发现就必须输出校正，不得因为画面内容已被转写大致提到而忽略：\n"
    "- 专有名词（模型名/产品名/人名/英文缩写）与画面不一致 → `[时间戳秒] 校正(置信度:高|中|低)：原转写「X」→「Y」`\n"
    "- 数字/单位/百分比与画面不一致 → `[时间戳秒] 校正(置信度:高|中|低)：原转写「X」→「Y」`\n"
    "「X」必须是【本窗转写文本】中逐字存在的片段（直接复制，不得改写/概括/添加括号说明）；找不到逐字片段时不要输出校正，改输出「不确定」。\n"
    "置信度口径：高=画面文字清晰且唯一；中=画面可读但有歧义；低=画面模糊/仅能推测（低置信不会自动改正文，只留痕）。\n"
    "其他情况：\n"
    "- 画面有知识材料（公式/PPT/代码/图表/演示步骤）而转写未覆盖 → 输出 `[时间戳秒] 补充：内容`\n"
    "- 画面不足以判断（如纯口播）→ 输出 `[时间戳秒] 不确定：原因`\n"
    "- 画面无文字且转写无明显错误 → 只输出四个字 `无遗漏`\n"
    "只输出上述格式，用中文。"
)


def check_window(cfg_vision, frames, transcript_text="", secondary_text=""):
    """单窗口复检 → [{ts, kind, text, original}]；失败空列表由调用方处理"""
    prompt = WINDOW_CHECK_PROMPT + f"\n\n【本窗转写文本】\n{transcript_text or '（无）'}"
    if secondary_text:
        prompt += ("\n\n【另一份机器字幕（仅参考，可能有错，勿直接采信）】\n"
                   + secondary_text[:2000])
    text = describe_video(cfg_vision, frames, prompt=prompt)
    return parse_window_result(text)


def parse_window_result(text):
    """解析窗口复检输出 → [{ts, kind, text, original, confidence}]"""
    if not text or "无遗漏" in text:
        return []
    items = []
    pat = (r"\[(\d+(?:\.\d+)?)\s*(?:s|秒)?\]\s*(补充|校正|不确定)"
           r"\s*(?:[（(][^）)]*[）)])?\s*[：:]\s*(.+)")
    for m in re.finditer(pat, text):
        ts = float(m.group(1))
        kind = {"补充": "supplement", "校正": "correction", "不确定": "uncertain"}[m.group(2)]
        body = m.group(3).strip()
        cm = None
        if kind == "correction":
            cm = re.match(r"原(?:转写)?[「\"](.+?)[」\"]"
                          r"(?:\s*[（(][^）)]*[）)])?\s*(?:→|->)\s*[「\"](.+?)[」\"]", body)
            if cm:
                body = cm.group(2)
        conf = re.search(r"置信度[:：]?\s*(高|中|低)", m.group(0))
        body = re.sub(r"[（(]?置信度[:：]?\s*[高中低][）)]?", "", body).strip()
        items.append({"ts": ts, "kind": kind, "text": body,
                      "original": (cm.group(1) if cm else ""),
                      "confidence": (conf.group(1) if conf else "中")})
    return items
