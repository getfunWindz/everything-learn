# -*- coding: utf-8 -*-
"""A0/B3: 名词注释与报告覆盖校验
- 术语库管理：load/save/merge（agent 写报告时自动沉淀新术语）
- 报告校验：check_coverage（字幕出现但报告未提及）与 check_annotation（提及但未注释）
"""
import json
import os
import re


try:                                    # GBK 控制台不因 emoji/符号崩溃
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass
_REFERENCES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "references")
DEFAULT_PATH = os.path.join(_REFERENCES_DIR, "glossary.json")

# 勘误映射键名（与术语条目并列，向后兼容）
ALIASES_KEY = "_aliases"

# 报告注释小节标记：每个知识点末尾的「名词注释」小节
ANNOTATION_HEADER = "**名词注释**"


def load_glossary(path: str = DEFAULT_PATH) -> dict:
    """读取术语库 → {术语: 一句话解释}；不存在返回 {}"""
    if not os.path.exists(path):
        return {}
    try:
        return json.load(open(path, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def get_aliases(glossary: dict = None) -> dict:
    """取勘误映射 _aliases（无则返回 {}）"""
    g = glossary if glossary is not None else load_glossary()
    return g.get(ALIASES_KEY, {}) or {}


def add_alias(raw: str, correct: str, glossary: dict = None, note: str = "") -> dict:
    """登记勘误：raw(错误写法) → correct(标准写法)；type 自动判定 stt_error / naming / uncertain"""
    g = glossary if glossary is not None else load_glossary()
    aliases = dict(g.get(ALIASES_KEY, {}) or {})
    if not correct:
        aliases[raw] = {"correct": None, "type": "uncertain", "note": note}
    else:
        aliases[raw] = {"correct": correct, "type": "stt_error", "note": note}
    g[ALIASES_KEY] = aliases
    return g


def clean_text(text: str, glossary: dict = None) -> tuple:
    """按 _aliases 替换：返回 (修正后文本, 替换计数 dict, 存疑清单 list)
    替换规则：最长错误写法优先（防子串先命中）；大小写敏感匹配原文写法
    存疑项（correct=None）不替换，仅收集到清单
    """
    g = glossary if glossary is not None else load_glossary()
    aliases = get_aliases(g)
    if not aliases:
        return text, {}, []
    # 按长度降序，避免子串抢先（如 "AH" 命中 "AH的目录" 前）
    ordered = sorted(aliases.items(), key=lambda kv: -len(kv[0]))
    fixed = text
    counts = {}
    uncertain = []
    for raw, meta in ordered:
        correct = meta.get("correct") if isinstance(meta, dict) else meta
        if not correct:
            # 存疑：不替换，收集出现次数
            n = fixed.count(raw)
            if n:
                uncertain.append({"raw": raw, "count": n, "note": meta.get("note", "") if isinstance(meta, dict) else ""})
            continue
        n = fixed.count(raw)
        if n:
            fixed = fixed.replace(raw, correct)
            counts[raw] = n
    return fixed, counts, uncertain


def absorb_corrections(pairs, glossary: dict = None) -> dict:
    """自动反哺（2026-10-17）：画面校正对 (原词, 新词) → 自动登记别名。

    仅当 任一侧归一化后命中术语库（术语名 / 别名目标）时登记（保守，防空沉淀）；
    已有别名不覆盖；返回新增 {原词: 新词}（并保存）。
    """
    g = glossary if glossary is not None else load_glossary()
    known = {_norm_term(k) for k in g if not k.startswith("_")}
    aliases = get_aliases(g)
    known |= {_norm_term(v.get("correct")) for v in aliases.values()
              if isinstance(v, dict) and v.get("correct")}
    known_aliases = {_norm_term(k) for k in aliases}
    added = {}
    for pair in pairs or []:
        try:
            orig, correct = pair[0], pair[1]
        except (TypeError, IndexError):
            continue
        o, c = _norm_term(orig), _norm_term(correct)
        if not o or not c or o == c or o in known_aliases:
            continue
        if o in known or c in known:
            g = add_alias(orig, correct, g)
            added[orig] = correct
            known_aliases.add(o)
    if added:
        save_glossary(g)
    return added


def _norm_term(s) -> str:
    """术语归一化（比较用）：strip + lower"""
    return (str(s) if s is not None else "").strip().lower()


def clean_lines(lines: list, glossary: dict = None) -> tuple:
    """批量术语筛验（2026-10-17）：逐条别名替换 → (new_lines, counts, uncertain)。

    - counts：{错误写法: 累计替换次数}
    - uncertain：correct=None 的存疑项（按 raw 合并 count），不替换
    - 返回新列表（不修改原对象），其他字段（时间戳/corrected_from 等）保留
    """
    g = glossary if glossary is not None else load_glossary()
    out, counts, uncertain = [], {}, {}
    for l in lines or []:
        nl = dict(l or {})
        fixed, c, u = clean_text(nl.get("text") or "", g)
        nl["text"] = fixed
        out.append(nl)
        for k, n in c.items():
            counts[k] = counts.get(k, 0) + n
        for x in u:
            raw = x["raw"]
            if raw in uncertain:
                uncertain[raw]["count"] += x["count"]
            else:
                uncertain[raw] = dict(x)
    return out, counts, [uncertain[k] for k in uncertain]


def absorb_terms_from_wiki(wiki_root: str, glossary: dict = None) -> dict:
    """反哺：扫描知识库 entities/topics 目录，把新术语（front matter 或标题）沉淀进 glossary
    只 add 不覆盖；返回新增的 {术语: 解释}
    """
    import glob
    g = glossary if glossary is not None else load_glossary()
    new_terms = {}
    # 只扫描实体页（主题页是聚合页，不是术语）
    for fp in sorted(glob.glob(os.path.join(wiki_root, "entities", "*.md"))):
        if not os.path.exists(fp):
            continue
        text = open(fp, encoding="utf-8").read()
        # 标题（# xxx）作为术语名；首个非空段落的第一句作为解释（截断）
        m = re.search(r"(?m)^#\s+(.+)$", text)
        if not m:
            continue
        term = m.group(1).strip()
        # 跳过已知格式页（如带“)”开头的多术语标题）与过长标题
        if not term or term in g or len(term) > 40:
            continue
        # 取「## 简介」之后、下一个 ## 之前的内容；跳过引用块/空行
        if "## 简介" in text:
            seg = text.split("## 简介", 1)[1].split("\n## ", 1)[0]
            clean = "\n".join(
                line for line in seg.splitlines()
                if line.strip() and not line.strip().startswith(">")
            )
            first_para = clean.strip().split("\n\n")[0] if clean.strip() else ""
            m2 = re.search(r"^(.{5,150}?[。.])", first_para, re.S) if first_para else None
        else:
            # 兜底①：标题下方紧跟的「> 一句话描述」
            m2 = re.search(r"(?m)^>\s*(.{8,150}?[。.])", text)
        if not m2:
            continue  # 无实质简介（主题页/标题即解释）,跳过
        expl = m2.group(1).strip()
        new_terms[term] = expl
    return g, new_terms

def save_glossary(glossary: dict, path: str = DEFAULT_PATH) -> None:
    """写回术语库（自动沉淀）"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(glossary, f, ensure_ascii=False, indent=2)


def merge_terms(glossary: dict, new_terms: dict) -> dict:
    """自动沉淀：新术语追加；已有术语保留原解释（不覆盖）"""
    merged = dict(glossary)
    for k, v in new_terms.items():
        if k not in merged and v:
            merged[k] = v
    return merged


_LATIN_TERM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+\-/]*$")


def _term_pattern(term: str):
    """术语匹配器：英文/数字术语按词边界（允许复数 s/es），中文按子串

    修复（2026-10-18）：纯子串匹配会让 pi 命中 pipeline、KDA 命中 backdata，
    导致「已提及/未注释」误报；英文术语必须独立成词。"""
    t = (term or "").strip()
    esc = re.escape(t)
    if _LATIN_TERM.match(t):
        return re.compile(rf"(?<![A-Za-z0-9]){esc}(?:s|es)?(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(esc, re.IGNORECASE)


def _term_in_subtitle(term: str, subtitle_text: str) -> bool:
    """术语是否出现在字幕中（英文按词边界，大小写不敏感）"""
    return bool(_term_pattern(term).search(subtitle_text or ""))


def check_coverage(report_text: str, subtitle_text: str, glossary: dict = None) -> list:
    """B3 覆盖自检：字幕中出现、但报告全文（正文+注释）完全未提及的术语"""
    glossary = glossary if glossary is not None else load_glossary()
    missing = []
    for term in glossary:
        if not _term_in_subtitle(term, subtitle_text):
            continue
        if not _term_pattern(term).search(report_text):
            missing.append(term)
    return missing


def _annotation_sections(report_text: str) -> str:
    """提取报告中所有「名词注释」小节的内容（拼接为一个文本）"""
    parts = re.split(r"(?m)^[-*]?\s*\*\*名词注释\*\*", report_text)
    if len(parts) < 2:
        return ""
    chunks = []
    for part in parts[1:]:
        # 每个小节到下一个 ## 或 段落空行处结束（保守取前 500 字符）
        section = part.split("\n##")[0][:500]
        chunks.append(section)
    return "\n".join(chunks)


def check_annotation(report_text: str, subtitle_text: str, glossary: dict = None) -> list:
    """A0 注释校验：字幕中出现且报告正文提及、但未在「名词注释」小节注释的术语"""
    glossary = glossary if glossary is not None else load_glossary()
    annotations = _annotation_sections(report_text)
    unannotated = []
    for term in glossary:
        if not _term_in_subtitle(term, subtitle_text):
            continue
        if re.search(re.escape(term), report_text, re.IGNORECASE) and \
           not re.search(re.escape(term), annotations, re.IGNORECASE):
            unannotated.append(term)
    return unannotated


def main(argv=None):
    """CLI：
    glossary.py list              列出术语库
    glossary.py add <术语> <解释>  自动沉淀单个术语（已有则保留原解释）
    glossary.py check <报告.md> <content.txt>   注释/覆盖校验（R11/B3）
    glossary.py alias <错误> <标准>        登记勘误映射（标准为空→uncertain）
    glossary.py aliases                    列出勘误映射
    glossary.py clean <文件> [--out <dst>] 按勘误映射替换，输出 corrected 稿 + 存疑清单
    glossary.py absorb <wiki目录> [--dry]  反哺：扫描知识库实体/主题页沉淀新术语
    """
    import sys
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__doc__)
        print("用法：list | add <术语> <解释> | check <报告.md> <content.txt> | alias <错误> <标准> | aliases | clean <文件> [--out <dst>] | absorb <wiki目录> [--dry]")
        return 1
    cmd = args[0]
    if cmd == "list":
        g = load_glossary()
        for k, v in g.items():
            if k == ALIASES_KEY:
                continue
            print(f"{k}: {v}")
        return 0
    if cmd == "add" and len(args) >= 3:
        term, expl = args[1], " ".join(args[2:])
        g = load_glossary()
        merged = merge_terms(g, {term: expl})
        save_glossary(merged)
        print(f"已沉淀：{term} → {expl}")
        return 0
    if cmd == "check" and len(args) >= 3:
        report_text = open(args[1], encoding="utf-8").read()
        subtitle_text = open(args[2], encoding="utf-8").read()
        unannotated = check_annotation(report_text, subtitle_text)
        uncovered = check_coverage(report_text, subtitle_text)
        print("== 注释缺失（正文提及但未注释）==")
        for t in unannotated:
            print(f"  - {t}")
        print("== 覆盖遗漏（字幕出现但报告未提及）==")
        for t in uncovered:
            print(f"  - {t}")
        if not unannotated and not uncovered:
            print("✅ 无遗漏")
        return 0 if not unannotated and not uncovered else 2
    if cmd == "alias" and len(args) >= 3:
        raw, correct = args[1], args[2]
        note = " ".join(args[3:])
        g = add_alias(raw, correct or None, note=note)
        save_glossary(g)
        state = "uncertain(存疑)" if not correct else "stt_error(勘误)"
        print(f"已登记：{raw} → {correct or '[待确认]'} [{state}]")
        return 0
    if cmd == "aliases":
        for raw, meta in get_aliases().items():
            if isinstance(meta, dict):
                print(f"{raw} → {meta.get('correct') or '[存疑]'} ({meta.get('type','')})")
            else:
                print(f"{raw} → {meta}")
        return 0
    if cmd == "clean" and len(args) >= 2:
        src = args[1]
        dst = None
        if "--out" in args:
            dst = args[args.index("--out") + 1]
        text = open(src, encoding="utf-8").read()
        fixed, counts, uncertain = clean_text(text)
        if dst:
            open(dst, "w", encoding="utf-8").write(fixed)
        print(f"替换 {len(counts)} 类 + 共 {sum(counts.values())} 处")
        for raw, n in counts.items():
            meta = get_aliases()[raw]
            print(f"  替换: {raw} → {meta['correct']} ({n}处)")
        if uncertain:
            print("存疑项（未替换，需人工确认）：")
            for u in uncertain:
                print(f"  {u['raw']} x{u['count']} {u['note']}")
        else:
            print("✅ 无存疑项")
        if dst:
            print(f"已写：{dst}")
        return 0 if not uncertain else 3
    if cmd == "absorb" and len(args) >= 2:
        wiki_root = args[1]
        dry = "--dry" in args
        g, new_terms = absorb_terms_from_wiki(wiki_root)
        if dry:
            print(f"[dry-run] 将沉淀 {len(new_terms)} 个新术语：")
            for k, v in new_terms.items():
                print(f"  {k}: {v[:50]}")
            return 0
        merged = merge_terms(g, new_terms)
        save_glossary(merged)
        print(f"已反哺沉淀 {len(new_terms)} 个新术语")
        for k, v in new_terms.items():
            print(f"  {k}: {v[:50]}")
        return 0
    print(f"未知命令：{cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
