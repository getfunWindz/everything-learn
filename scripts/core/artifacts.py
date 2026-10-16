"""产物三件套：content_info.json / content.txt / report_template.md"""
import json
import os
from datetime import datetime

import report


def build_info(meta, ref, kind: str, source: str, page_count: int = 1,
               tool: str = "everything-learn v0.1", assets=None,
               recheck=None, provenance=None, language=None,
               quality=None, quality_hint=None, glossary=None) -> dict:
    """组装 content_info.json 的字段（meta: ItemMeta；ref: ItemRef）

    可选扩展（M13/T13）：recheck=复检元数据；provenance=来源分级；language=实际使用的语言。
    （2026-10-17）quality=转写置信度；quality_hint=低配质量强制推荐；glossary=术语筛验/反哺留痕。
    为空则字段缺省，保持旧产物向后兼容。"""
    info = {
        "platform": ref.platform,
        "item_id": ref.item_id,
        "sub_id": ref.sub_id,
        "url": ref.url,
        "title": meta.title,
        "author": meta.author,
        "published": meta.published,
        "desc": meta.desc,
        "duration_seconds": meta.duration,
        "kind": kind,
        "page": meta.page or (ref.sub_id or "1"),
        "page_part": meta.page_part or ref.title,
        "page_count": page_count,
        "source": source,
        "stats": dict(meta.stats or {}),
        "assets": list(assets or []),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "tool": tool,
    }
    if provenance:
        info["provenance"] = provenance
    if language:
        info["language"] = language
    if recheck:
        info["recheck"] = recheck
    if quality:
        info["quality"] = quality
    if quality_hint:
        info["quality_hint"] = quality_hint
    if glossary:
        info["glossary"] = glossary
    return info


def _line_annotation(ln) -> str:
    """复检留痕（T15）：vision 补充/校正行的行尾标注；其余行原样"""
    source = ln.get("source") if isinstance(ln, dict) else getattr(ln, "source", None)
    if source == "vision补充":
        return " 【画面补充】"
    if source == "vision校正":
        orig = (ln.get("corrected_from") if isinstance(ln, dict)
                else getattr(ln, "corrected_from", None)) or ""
        return f" 【画面校正：原「{orig}」】" if orig else " 【画面校正】"
    return ""


def render_content_txt(content) -> str:
    """归一化文本：timeline → "[起-止] 文本" 行；document → Markdown 结构"""
    kind = getattr(content, "kind", "timeline")
    if kind == "document":
        parts = []
        for b in getattr(content, "blocks", []):
            heading = b.get("heading") if isinstance(b, dict) else getattr(b, "heading", "")
            text = b.get("text") if isinstance(b, dict) else getattr(b, "text", "")
            parts.append((f"## {heading}\n\n" if heading else "") + (text or ""))
        return "\n\n".join(parts) + ("\n" if parts else "")
    out = []
    for ln in getattr(content, "segments", []):
        s = ln.get("start") if isinstance(ln, dict) else ln.start
        e = ln.get("end") if isinstance(ln, dict) else ln.end
        t = ln.get("text") if isinstance(ln, dict) else ln.text
        out.append(f"[{float(s):.1f}-{float(e):.1f}] {t}{_line_annotation(ln)}")
    return "\n".join(out) + ("\n" if out else "")


def write_item_artifacts(out_dir: str, info: dict, content) -> None:
    """落盘三件套（目录不存在则创建）"""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "content_info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "content.txt"), "w", encoding="utf-8") as f:
        f.write(render_content_txt(content))
    with open(os.path.join(out_dir, "report_template.md"), "w", encoding="utf-8") as f:
        f.write(report.render_template_for(info["kind"], info))


def write_recheck_report(out_dir: str, data: dict) -> None:
    """复检透明度报告（T15）：档位/预算/同源校验/不确定项留痕（timeline 专用）"""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "recheck_report.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
