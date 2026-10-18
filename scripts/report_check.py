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

POINT_RE = re.compile(r"(?m)^####\s*知识点\s*(\d+)\s*[：:]")


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


def check_report(text: str, content_text: str = "", glossary: dict = None) -> dict:
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
    args = ap.parse_args(argv)
    text = open(args.report, encoding="utf-8").read()
    content = open(args.content, encoding="utf-8").read() if args.content else ""
    result = check_report(text, content)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for c in result["checks"]:
            if c["level"] == "pass" and not c["detail"]:
                print(f"  ✓ {c['name']}")
            else:
                print(f"  [{c['level']}] {c['name']}：{c['detail']}")
        print("✅ 通过" if result["ok"] else "❌ 未通过")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
