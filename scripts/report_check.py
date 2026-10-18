"""R1–R13 报告结构自检（交付前核验）

用法：
    python scripts/report_check.py <报告.md> [--content <content.txt>] [--json]

检查项（fail 会以非零退出码阻断交付）：
- sections     学习地图 / 知识详解 / 自测 / 复检说明 四节齐全
- garble       无 U+FFFD 乱码
- placeholder  无 TBD/TODO/待补/占位符
- numbering    知识点编号从 1 连续
- point_structure  每个知识点含「定义 / 通俗 / 名词注释」
- map          学习地图覆盖全部知识点编号
- selftest     自测题数 ≥ ceil(知识点数/5)（R6 每 3–5 点一题）
- quotes       时间轴报告建议 5–10 条金句（warn）
- glossary     传入 --content 时执行 R11 术语覆盖/注释校验
"""
import argparse
import json
import re
import sys


try:                                    # GBK 控制台不因 emoji/符号崩溃
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass
POINT_RE = re.compile(r"(?m)^####\s*知识点\s*(\d+)\s*[：:]")
# 带单位的数字（防幻觉回溯；纯序号/日期不查）
NUM_UNIT_RE = re.compile(
    r"([$￥]?\s*\d[\d,]*(?:\.\d+)?)\s*(%|％|亿|万|美元|元|nm|mm|cm|kg|吨|台|片|层|倍|分钟|秒|小时|天|年|°C|℃|W|kW|MW|GB|TB|MB|Gbps|fps)")
RAW_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
QUOTE_RE = re.compile(r"[「“\"]([^」”\"]{4,})[」”\"]")
TS_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]")
CONTENT_TS_RE = re.compile(r"(?m)^\[([\d.]+)-([\d.]+)\]")
_PUNCT = re.compile(r"[\s，。！？；：、,.!?;:\"'“”‘’()（）【】\[\]…—\-]+")


def _digit_key(num: str) -> str:
    return re.sub(r"\D", "", num or "")


def _numbers_with_units(text: str):
    return {(_digit_key(m.group(1)), m.group(2)) for m in NUM_UNIT_RE.finditer(text or "")}


def _clean_transcript(text: str) -> str:
    """去掉【画面补充】整行、剥离【画面校正】标注，并拼接时间戳行正文（便于金句回溯）"""
    out, found = [], False
    for line in (text or "").splitlines():
        m = re.match(r"^\[([\d.]+)-([\d.]+)\]\s*(.*)$", line)
        if not m:
            continue
        found = True
        body = m.group(3)
        if "【画面补充】" in body:
            continue
        body = re.sub(r"\s*【画面校正[^】]*】", "", body)
        out.append(body.strip())
    return "".join(out) if found else (text or "")


def _norm_text(s: str) -> str:
    return _PUNCT.sub("", s or "").lower()


def check_quotes(report_text: str, content_text: str):
    """「金句」小节中的引号短语逐字回溯（避免把术语/分析引用误判）"""
    section = _section(report_text, "金句")
    if not section.strip():
        return [], 0
    content_norm = _norm_text(_clean_transcript(content_text))
    quotes = [q.strip() for q in QUOTE_RE.findall(section) if len(_norm_text(q)) >= 4]
    missing = []
    for q in quotes:
        segs = [x for x in re.split(r"…+|\.{3,}", q) if len(_norm_text(x)) >= 4]
        if not segs:
            continue
        if any(_norm_text(x) not in content_norm for x in segs):
            missing.append(q)
    return missing, len(quotes)


def check_timestamps(report_text: str, content_text: str):
    """报告时间戳不得超过 content 末尾（允许 5s 余量）"""
    ends = [float(m.group(2)) for m in CONTENT_TS_RE.finditer(content_text or "")]
    if not ends:
        return [], 0
    dur = max(ends)
    total, bad = 0, []
    for m in TS_RE.finditer(report_text or ""):
        total += 1
        a, b, c = m.group(1), m.group(2), m.group(3)
        sec = (int(a) * 3600 + int(b) * 60 + int(c)) if c is not None else (int(a) * 60 + int(b))
        if sec > dur + 5:
            bad.append(m.group(0))
    return bad, total


def check_citations(report_text: str, content_text: str):
    """带单位数字是否能在原文找到（数字去分隔符/小数点后比对）；返回 (missing, total)

    原文侧用「全部数字」做集合（表格里的 53,536 与报告 535.36 亿可对齐）；
    年份（1900–2100 + 年）不计入，避免把“1980 年”误报。"""
    content_keys = {_digit_key(m.group(0)) for m in RAW_NUM_RE.finditer(content_text or "")}
    if not content_keys:
        return [], 0
    report_nums = _numbers_with_units(report_text)
    missing = []
    for k, u in report_nums:
        if u == "年" and len(k) == 4 and k.isdigit() and 1900 <= int(k) <= 2100:
            continue
        if k not in content_keys:
            missing.append((k, u))
    return missing, len(report_nums)


def _section(text: str, title: str) -> str:
    """取 `## title` 到下一个 `## ` 之间的正文（title 允许带后缀说明）"""
    m = re.search(rf"(?m)^##\s+{re.escape(title)}.*?$", text or "")
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"(?m)^##\s+", rest)
    return rest[:nxt.start()] if nxt else rest


def _points(text: str):
    """→ [(编号, 该知识点正文)]（正文在下一个知识点/章节标题处截断）"""
    ms = list(POINT_RE.finditer(text or ""))
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        block = text[m.end():end]
        cut = re.search(r"(?m)^#{2,3}\s", block)
        if cut:
            block = block[:cut.start()]
        out.append((int(m.group(1)), block))
    return out


def check_report(text: str, content_text: str = "", glossary: dict = None,
                 strict_citations: bool = False) -> dict:
    checks = []

    def add(name, level, detail=""):
        checks.append({"name": name, "level": level, "detail": detail})

    required = ["学习地图", "知识详解", "自测", "复检说明"]
    missing = [s for s in required if not re.search(rf"(?m)^##\s+{s}", text or "")]
    add("sections", "fail" if missing else "pass",
        "缺少：" + "、".join(missing) if missing else "")

    n_bad = (text or "").count("\ufffd")
    add("garble", "fail" if n_bad else "pass", f"{n_bad} 处乱码" if n_bad else "")

    bad_words = [w for w in ("TBD", "TODO", "FIXME", "待补", "占位符") if w in (text or "")]
    add("placeholder", "fail" if bad_words else "pass",
        "含：" + "、".join(bad_words) if bad_words else "")

    pts = _points(text)
    if not pts:
        add("numbering", "fail", "未找到「#### 知识点」")
        add("point_structure", "fail", "无知识点")
        add("map", "fail", "无知识点")
    else:
        nums = [n for n, _ in pts]
        seq = list(range(1, len(nums) + 1))
        add("numbering", "fail" if nums != seq else "pass",
            f"编号不连续：{nums}" if nums != seq else f"{len(nums)} 个知识点")

        bad_pts = []
        for n, block in pts:
            miss = [k for k in ("定义", "通俗", "名词注释") if k not in block]
            if miss:
                bad_pts.append(f"{n}(缺{'/'.join(miss)})")
        add("point_structure", "fail" if bad_pts else "pass", "；".join(bad_pts))

        map_numbers = [int(x) for x in re.findall(r"(?m)^\s*(\d+)\.\s", _section(text, "学习地图"))]
        miss_map = [n for n in nums if n not in map_numbers]
        add("map", "fail" if miss_map else "pass",
            f"学习地图缺少：{miss_map}" if miss_map else "")

        selftest = _section(text, "自测")
        q = len(re.findall(r"(?m)^\s*\d+\.\s", selftest))
        need = (len(nums) + 4) // 5
        add("selftest", "fail" if q < need else "pass", f"{q} 题（至少需 {need} 题）")

    if re.search(r"(?m)^###\s+段", text or ""):
        qn = len(re.findall(r"(?m)^\s*\d+\.\s", _section(text, "金句")))
        add("quotes", "warn" if qn < 5 else "pass",
            f"{qn} 条金句（建议 5–10）" if qn < 5 else "")

    if content_text:
        missing_nums, total_nums = check_citations(text, content_text)
        if missing_nums:
            level = "fail" if strict_citations else "warn"
            sample = "、".join(f"{n}{u}" for n, u in missing_nums[:5])
            add("citations", level,
                f"{len(missing_nums)}/{total_nums} 个带单位数字未在原文找到（如 {sample}）")
        else:
            add("citations", "pass", f"{total_nums} 个带单位数字均可回溯" if total_nums else "")
        miss_q, total_q = check_quotes(text, content_text)
        if miss_q:
            add("quotes", "fail" if strict_citations else "warn",
                f"{len(miss_q)}/{total_q} 条引号短语未在原文找到（如 {'；'.join(miss_q[:2])[:80]}）")
        else:
            add("quotes", "pass", f"{total_q} 条引号短语可回溯" if total_q else "")
        bad_ts, total_ts = check_timestamps(text, content_text)
        if bad_ts:
            add("timestamps", "fail" if strict_citations else "warn",
                f"{len(bad_ts)} 个时间戳超出原文时长（如 {', '.join(bad_ts[:3])}）")
        else:
            add("timestamps", "pass", f"{total_ts} 个时间戳在范围内" if total_ts else "")
        try:
            import glossary as g
            miss = g.check_coverage(text, content_text, glossary)
            unann = g.check_annotation(text, content_text, glossary)
            add("glossary", "fail" if (miss or unann) else "pass",
                f"未覆盖：{miss}；未注释：{unann}" if (miss or unann) else "")
        except Exception as e:      # 校验器异常不应阻断报告交付
            add("glossary", "warn", f"校验失败：{str(e)[:100]}")

    return {"ok": not any(c["level"] == "fail" for c in checks), "checks": checks}


def main(argv=None):
    ap = argparse.ArgumentParser(description="R1–R13 报告结构自检")
    ap.add_argument("report", help="报告 .md 路径")
    ap.add_argument("--content", help="对应 content.txt（启用术语覆盖/注释校验）")
    ap.add_argument("--json", action="store_true", help="机读输出")
    ap.add_argument("--strict", action="store_true",
                    help="严格模式：带单位数字无法回溯即 fail（默认仅 warn）")
    args = ap.parse_args(argv)
    text = open(args.report, encoding="utf-8").read()
    content = open(args.content, encoding="utf-8").read() if args.content else ""
    result = check_report(text, content, strict_citations=args.strict)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for c in result["checks"]:
            if c["level"] == "pass" and not c["detail"]:
                print(f"  [ok] {c['name']}")
            else:
                print(f"  [{c['level']}] {c['name']}：{c['detail']}")
        print("PASS" if result["ok"] else "FAIL")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
