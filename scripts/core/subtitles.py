"""字幕文本解析（vtt/srt）：平台无关，供各适配器复用"""
import re


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


_VTT_TS = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})")


def parse_vtt(text: str) -> list:
    """解析 WebVTT 字幕 → [{start, end, text}]"""
    lines = (text or "").splitlines()
    out, i = [], 0
    while i < len(lines):
        m = _VTT_TS.search(lines[i])
        if not m:
            i += 1
            continue
        s = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000
        e = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 1000
        i += 1
        buf = []
        while i < len(lines) and lines[i].strip():
            buf.append(re.sub(r"<[^>]+>", "", lines[i]).strip())
            i += 1
        t = _norm_text(" ".join(buf))
        if t:
            out.append({"start": round(s, 2), "end": round(e, 2), "text": t})
    return out


def parse_srt(text: str) -> list:
    """解析 SRT 字幕（时间戳格式与 VTT 兼容，序号行会被忽略）→ [{start, end, text}]"""
    return parse_vtt(text)
