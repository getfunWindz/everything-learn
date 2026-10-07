"""产物三件套：content_info.json / content.txt / report_template.md"""
import json
import os
from datetime import datetime

import report


def build_info(meta, ref, kind: str, source: str, page_count: int = 1,
               tool: str = "everything-learn v0.1") -> dict:
    """组装 content_info.json 的字段（meta: ItemMeta；ref: ItemRef）"""
    return {
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
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "tool": tool,
    }


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
        out.append(f"[{float(s):.1f}-{float(e):.1f}] {t}")
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
