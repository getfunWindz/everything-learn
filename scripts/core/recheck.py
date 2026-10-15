"""M13：字幕保真复检 —— 评分引擎 / 区域选择 / 预算估算 / 同源判定。

core 层：平台无关、确定性计算（不调用 LLM）。所有函数可离线单测。
"""
import difflib  # 后续任务（同源校验 §8.2）预留
import re

DISCOURSE_MARKERS = ("定义", "公式", "步骤", "首先", "其次", "因此", "综上", "注意", "定理", "结论")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")
_FORMULA = re.compile(r"[=≈≤≥∑∫√]")
_NUMUNIT = re.compile(r"\d+(?:\.\d+)?\s*(?:%|次|倍|ms|GB|万|亿)")  # spec §7.3 正则
_ENUM = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩]|(?:^|\s)\d+[.、)]|\([a-cA-C]\)")
_WORD = re.compile(r"[A-Za-z]+")  # TTR token：英文按词
_CJK = re.compile(r"[\u4e00-\u9fff]+")  # TTR token：中文连续片段
VIS_CUES = ("如图", "这张图", "这张表", "表格", "演示", "看一下", "大家注意", "看这里", "如图所示")  # M13 信号：画面引导词（最长优先非重叠匹配）
_VIS_RE = re.compile("|".join(re.escape(c) for c in sorted(VIS_CUES, key=len, reverse=True)))


def _effective_duration(lines, duration_sec):
    """统一时长口径：duration_sec>0 用它；否则回退末条字幕 end；无 lines 则 0.0。"""
    d = float(duration_sec or 0)
    if d > 0:
        return d
    if lines:
        return max(float(lines[-1]["end"] or 0), 0.0)
    return 0.0


def _ttr_tokens(text):
    """TTR token：英文按词（小写）；中文连续片段按 2-gram。"""
    toks = [w.lower() for w in _WORD.findall(text or "")]
    for run in _CJK.findall(text or ""):
        toks.extend(run[i:i + 2] for i in range(len(run) - 1))
    return toks


def _windows(lines, duration_sec, win_sec=60):
    """非重叠窗口 → [(start, end, text)]"""
    lines = lines or []
    dur = _effective_duration(lines, duration_sec)
    win_sec = max(float(win_sec), 1.0)
    if dur <= 0:
        return []
    out = []
    t = 0.0
    while t < dur:
        text = " ".join(l["text"] for l in lines if t <= float(l["start"]) < t + win_sec)
        out.append((t, min(t + win_sec, dur), text))
        t += win_sec
    return out


def score_window_text(text, glossary_terms=None):
    """单窗口加权得分 → (score, breakdown)"""
    t = text or ""
    terms = glossary_terms or ()
    gloss = sum(1 for w in set(terms) if w and w in t)
    latin = len(_LATIN.findall(t))
    formula = len(_FORMULA.findall(t))
    numunit = len(_NUMUNIT.findall(t))
    enum = len(_ENUM.findall(t))
    markers = sum(t.count(m) for m in DISCOURSE_MARKERS)
    toks = _ttr_tokens(t)
    ttr = (len(set(toks)) / len(toks)) if toks else 0.0
    ttr_n = max(0.0, min(1.0, (ttr - 0.3) / 0.4))
    score = 2.0 * gloss + 1.5 * (latin + formula + numunit) + 1.0 * (markers + enum) + 0.5 * ttr_n
    return score, {"glossary": gloss, "latin": latin, "formula": formula,
                   "numunit": numunit, "enum": enum, "markers": markers, "ttr": round(ttr, 3)}


def kp_density(lines, duration_sec, win_sec=60, glossary_terms=None):
    """知识点密度（个/分钟）= Σ floor(score_w/3) / 分钟 → (density, detail)"""
    lines = lines or []
    dur_min = max(_effective_duration(lines, duration_sec) / 60.0, 1e-9)
    blocks = 0
    windows = []
    for s, e, text in _windows(lines, duration_sec, win_sec):
        sc, detail = score_window_text(text, glossary_terms)
        n = int(sc // 3.0)
        blocks += n
        windows.append({"start": s, "end": e, "score": round(sc, 2), "score_raw": sc,
                        "blocks": n, "detail": detail})
    return blocks / dur_min, {"blocks": blocks, "windows": windows}


def _gaps(lines, duration_sec, min_gap=5.0):
    """转写空档 → [(start, end)]（含末尾）"""
    dur = _effective_duration(lines, duration_sec)
    if dur <= 0:
        return []
    gaps, prev_end = [], 0.0
    for l in sorted(lines or [], key=lambda x: float(x["start"])):
        s = float(l["start"])
        if s - prev_end >= min_gap:
            gaps.append((prev_end, s))
        prev_end = max(prev_end, float(l["end"]))
    if dur - prev_end >= min_gap:
        gaps.append((prev_end, dur))
    return gaps


def gap_stats(lines, duration_sec, min_gap=5.0):
    """→ (空档占比, 最长空档秒)"""
    dur = _effective_duration(lines, duration_sec)
    if dur <= 0:
        return 0.0, 0.0
    gaps = _gaps(lines, duration_sec, min_gap)
    coverage = sum(e - s for s, e in gaps)
    return min(1.0, coverage / dur), max((e - s for s, e in gaps), default=0.0)


def kp_spread(lines, duration_sec, segments=10, glossary_terms=None):
    """知识点分散度（HHI 归一化）：0=全集中，1=全均匀"""
    dur = _effective_duration(lines, duration_sec)
    if dur <= 0 or int(segments) < 2:
        return 0.0
    step = dur / segments
    counts = []
    for i in range(segments):
        s, e = i * step, (i + 1) * step
        # kp_density 的窗口自 0 起扫（绝对时间轴），故将片段字幕平移到段内相对时间
        seg = [{"start": float(l["start"]) - s, "end": float(l["end"]) - s, "text": l["text"]}
               for l in (lines or []) if s <= float(l["start"]) < e]
        _, det = kp_density(seg, max(e - s, 1e-9), glossary_terms=glossary_terms)
        counts.append(det["blocks"])
    total = sum(counts)
    if total <= 0:
        return 0.0
    p = [c / total for c in counts]
    hhi = sum(x * x for x in p)
    return max(0.0, min(1.0, (1 - hhi) / (1 - 1 / segments)))


def vis_ref_density(lines, duration_sec):
    """画面引导词密度（个/10min）；最长优先非重叠匹配，避免“如图/如图所示”等重复计数"""
    text = " ".join(l["text"] for l in (lines or []))
    n = len(_VIS_RE.findall(text))
    return n / max(_effective_duration(lines, duration_sec) / 600.0, 1e-9)


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


_SRC_RISK = {"whisper": 1.0, "auto": 1.0, "none": 1.0, "unknown": 0.8, "human": 0.3, "official": 0.2}


def gather_signals(lines, duration_sec, provenance, glossary_terms=None):
    """汇总全部确定性信号 → dict"""
    density, detail = kp_density(lines, duration_sec, glossary_terms=glossary_terms)
    ratio, longest = gap_stats(lines, duration_sec)
    return {"dur_min": _effective_duration(lines, duration_sec) / 60.0,
            "kp_density": density, "kp_density_detail": detail,
            "kp_spread": kp_spread(lines, duration_sec, glossary_terms=glossary_terms),
            "src_risk": _SRC_RISK.get(provenance, 0.8),
            "vis_ref": vis_ref_density(lines, duration_sec),
            "gap_ratio": ratio, "max_gap": longest}


def decide_tier(signals, cfg, provenance, has_video=True, policy=None):
    """档位判决 → {"tier": 1|2|3|"n/a", "score": float, "reasons": [str]}"""
    rc = cfg.get("recheck", {})
    policy = policy or rc.get("tier_policy", "auto")
    if policy in ("off", 3, "3"):
        return {"tier": 3, "score": 0.0, "reasons": ["用户指定不复检（tier_policy=off）"]}
    if policy in ("tier1", 1, "1"):
        return {"tier": 1, "score": 0.0, "reasons": ["用户强制档1"]}
    if policy in ("tier2", 2, "2"):
        return {"tier": 2, "score": 1.0, "reasons": ["用户强制档2"]}
    if not has_video:
        return {"tier": "n/a", "score": 0.0, "reasons": ["纯音频来源：多模态复检不适用"]}

    density, dur = signals["kp_density"], signals["dur_min"]
    t3 = rc.get("tier3", {})
    no_visual_risk = signals["vis_ref"] <= 0.5 and signals["max_gap"] < t3.get("max_gap_sec", 15)
    if (t3.get("allow") and provenance in ("human", "official")
            and no_visual_risk and density < t3.get("max_density", 1.5)):
        return {"tier": 3, "score": 0.0,
                "reasons": ["档3 白名单：人工/官方来源 + 无画面风险 + 低密度"]}

    if dur <= 15 and density >= 3:
        return {"tier": 2, "score": 1.0,
                "reasons": [f"短视频+高密度（{dur:.0f}min, {density:.1f}/min）"]}
    if dur >= 120 and density <= 1:
        return {"tier": 1, "score": 0.0,
                "reasons": [f"超长+低密度（{dur:.0f}min, {density:.1f}/min）"]}

    density_n = _clamp(density / 6.0)
    vis_n = _clamp(signals["vis_ref"] / 5.0)
    dur_n = _clamp(dur / 120.0)
    score = (1.0 * density_n + 0.6 * signals["kp_spread"] + 0.5 * signals["src_risk"]
             + 0.4 * vis_n - 0.5 * dur_n)
    tier = 2 if score >= rc.get("tier2_score", 0.55) else 1
    return {"tier": tier, "score": round(score, 4),
            "reasons": [f"density={density:.1f}/min", f"spread={signals['kp_spread']:.2f}",
                        f"src_risk={signals['src_risk']:.1f}",
                        f"vis_ref={signals['vis_ref']:.1f}/10min",
                        f"dur={dur:.0f}min", f"score={score:.2f}"]}


def select_regions(lines, duration_sec, cfg, disputed=None):
    """档1 候选区域：知识点密集区 + 无讲解空档 + 双 ASR 分歧区。
    → [{start, end, reason, score}] 按 score 降序，Top N（重叠去重：低分者丢弃）。"""
    rc = cfg.get("recheck", {})
    win = rc.get("region_window_sec", 60)
    gap_sec = rc.get("gap_sec", 20)
    top_n = int(rc.get("region_top_n", 5))
    if top_n <= 0:
        return []
    candidates = []
    for s, e, text in _windows(lines or [], duration_sec, win):
        sc, _ = score_window_text(text)
        if sc >= 3.0:
            candidates.append({"start": s, "end": e, "reason": "知识点密集区", "score": sc})
    for a, b in _gaps(lines or [], duration_sec, gap_sec):
        candidates.append({"start": a, "end": b, "reason": "无讲解空档（画面展示）",
                           "score": 0.5 + min((b - a) / 60.0, 2.0)})
    for a, b in (disputed or []):
        candidates.append({"start": float(a), "end": float(b), "reason": "双 ASR 分歧区",
                           "score": 0.8 + min((float(b) - float(a)) / 60.0, 1.0)})
    candidates.sort(key=lambda r: -r["score"])
    merged = []
    for c in candidates:
        if any(not (c["end"] <= m["start"] or c["start"] >= m["end"]) for m in merged):
            continue
        merged.append(c)
        if len(merged) >= top_n:
            break
    return sorted(merged, key=lambda r: -r["score"])


def plan_frames(regions, fps=3, max_frames=20000):
    """区域 → 帧预算（含硬帽裁剪）"""
    wanted = int(sum(max(0.0, r["end"] - r["start"]) for r in regions) * fps + 1e-9)
    return {"wanted": wanted, "frames": min(wanted, max_frames), "capped": wanted > max_frames}


def estimate_budget(frames, windows, pricing, whisper_secs=0.0):
    """预算估算（金额区间 ±30%）"""
    input_tokens = frames * pricing.get("tokens_per_frame", 600) + windows * 600
    output_tokens = windows * 800
    amount = ((input_tokens / 1e6) * pricing.get("input_per_1m", 0.3)
              + (output_tokens / 1e6) * pricing.get("output_per_1m", 1.2))
    return {"frames": frames, "windows": windows,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "amount": round(amount, 6), "low": round(amount * 0.7, 6),
            "high": round(amount * 1.3, 6),
            "currency": pricing.get("currency", "USD"), "whisper_secs": whisper_secs}


def needs_gate(est, cfg, duration_sec):
    """是否触发预算闸门（金额超免打扰 或 超长）"""
    rc = cfg.get("recheck", {})
    return (est["amount"] > rc.get("auto_approve_amount", 0.3)
            or float(duration_sec or 0) >= rc.get("confirm_over_sec", 3600))


_PUNCT_NORM = re.compile(r"[\s，。！？；：、,.!?;:\"'“”‘’()（）【】\[\]]+")


def _norm_for_match(text: str) -> str:
    """同源比对归一化：去标点空白；英文转小写（中文按字、英文按词的效果）"""
    t = _PUNCT_NORM.sub(" ", text or "")
    return "".join(ch.lower() if ch.isascii() else ch for ch in t)


def _text_in_range(lines, start, end, pad=6.0):
    return " ".join(l["text"] for l in (lines or [])
                    if start - pad <= float(l["start"]) <= end + pad)


def _similarity(a, b):
    ta, tb = _norm_for_match(a), _norm_for_match(b)
    if not ta or not tb:
        return 0.0
    return difflib.SequenceMatcher(None, ta, tb).ratio()


def same_source_check(clips, subtitle_lines, cfg):
    """同源判定：clips=[(start,end,asr_text)] → {checked, ratios, avg, passed,
    threshold, compared, empty, reason_code}

    仅统计「ASR 与字幕区间文本两侧均非空」的可比点；空文本点（说话空档/纯音乐段/无字幕）
    不是异源证据，计 0 会系统性误杀稀疏正确字幕，故只计 empty 并跳过。
    可比点 <2 → checked=False（fail-safe，单点一致不足以放行），reason_code=insufficient。
    """
    thr = cfg.get("recheck", {}).get("same_source_threshold", 0.45)
    ratios, empty = [], 0
    for s, e, asr in clips:
        ref_text = _text_in_range(subtitle_lines, s, e)
        if not _norm_for_match(asr) or not _norm_for_match(ref_text):
            empty += 1
            continue
        ratios.append(_similarity(asr, ref_text))
    compared = len(ratios)
    avg = (sum(ratios) / compared) if compared else 0.0
    checked = compared >= 2
    return {"checked": checked, "ratios": [round(r, 3) for r in ratios],
            "avg": round(avg, 3),
            "passed": checked and avg >= thr,
            "threshold": thr, "compared": compared, "empty": empty,
            "reason_code": "" if checked else "insufficient"}


def pick_spot_times(duration_sec, n=3):
    """同源校验时间点：10%/50%/90%（避开首尾 5%）"""
    dur = float(duration_sec or 0)
    if dur <= 0:
        return [(0.0, 12.0)]
    pts = []
    for frac in (0.1, 0.5, 0.9):
        a = max(dur * 0.05, dur * frac - 6.0)
        pts.append((round(a, 2), round(min(a + 12.0, dur - 1.0), 2)))
    return pts
