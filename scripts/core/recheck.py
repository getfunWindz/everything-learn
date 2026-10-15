"""M13：字幕保真复检 —— 评分引擎 / 区域选择 / 预算估算 / 同源判定。

core 层：平台无关、确定性计算（不调用 LLM）。所有函数可离线单测。
"""
import difflib
import re

DISCOURSE_MARKERS = ("定义", "公式", "步骤", "首先", "其次", "因此", "综上", "注意", "定理", "结论")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")
_FORMULA = re.compile(r"[=≈≤≥∑∫√]")
_NUMUNIT = re.compile(r"\d+(?:\.\d+)?\s*(?:%|次|倍|ms|GB|MB|万|亿|秒)")
_ENUM = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩]|(?:^|\s)\d+[.、)]|\([a-cA-C]\)")
_PUNCT = re.compile(r"[\s，。！？；：、,.!?;:\"'“”‘’()（）【】\[\]]+")
VIS_CUES = ("如图", "这张图", "这张表", "表格", "演示", "看一下", "大家注意", "看这里", "如图所示")


def _plain(text: str) -> str:
    return _PUNCT.sub("", text or "")


def _windows(lines, duration_sec, win_sec=60):
    """非重叠窗口 → [(start, end, text)]"""
    dur = float(duration_sec or (lines[-1]["end"] if lines else 0) or 0)
    out = []
    t = 0.0
    while t < max(dur, 1e-9):
        text = " ".join(l["text"] for l in (lines or []) if t <= float(l["start"]) < t + win_sec)
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
    chars = _plain(t)
    ttr = (len(set(chars)) / len(chars)) if chars else 0.0
    ttr_n = max(0.0, min(1.0, (ttr - 0.3) / 0.4))
    score = 2.0 * gloss + 1.5 * (latin + formula + numunit) + 1.0 * (markers + enum) + 0.5 * ttr_n
    return score, {"glossary": gloss, "latin": latin, "formula": formula,
                   "numunit": numunit, "enum": enum, "markers": markers, "ttr": round(ttr, 3)}


def kp_density(lines, duration_sec, win_sec=60, glossary_terms=None):
    """知识点密度（个/分钟）= Σ floor(score_w/3) / 分钟 → (density, detail)"""
    dur_min = max(float(duration_sec or 0) / 60.0, 1e-9)
    blocks = 0
    windows = []
    for s, e, text in _windows(lines or [], duration_sec, win_sec):
        sc, detail = score_window_text(text, glossary_terms)
        n = int(sc // 3.0)
        blocks += n
        windows.append({"start": s, "end": e, "score": round(sc, 2),
                        "blocks": n, "detail": detail})
    return blocks / dur_min, {"blocks": blocks, "windows": windows}
