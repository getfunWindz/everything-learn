"""通用扫描框架：条目过滤/主题聚类/优先级排序/快照（bili favs-scan 与全平台 scan 共用）"""
import json
import os
from datetime import datetime

AI_KEYWORDS = [
    "AI", "人工智能", "机器学习", "深度学习", "大模型", "LLM", "GPT", "ChatGPT",
    "Agent", "RAG", "MCP", "Transformer", "KV", "Cache", "MTP", "MoE", "GQA",
    "Python", "编程", "代码", "算法", "PyTorch", "神经网络", "注意力", "提示词",
    "Embedding", "向量", "微调", "部署", "Docker", "推理", "量化", "训练", "模型",
]

OTHER_KEYWORDS = {
    "音乐/乐器": ["钢琴", "吉他", "弹唱", "编曲", "音乐", "鼓", "贝斯"],
    "生活/娱乐": ["vlog", "猫咪", "美食", "探店", "旅行", "电影", "解说", "集锦"],
    "体育": ["篮球", "健身", "足球", "跑步"],
}


def refs_to_items(refs) -> list:
    """ItemRef 列表 → 扫描条目 dict（title/desc/view/url/id）"""
    items = []
    for r in refs:
        extra = getattr(r, "extra", None) or {}
        items.append({
            "id": getattr(r, "item_id", "") or "",
            "title": getattr(r, "title", "") or "",
            "desc": (extra.get("desc") or "")[:200],
            "view": extra.get("view") or extra.get("like") or 0,
            "url": getattr(r, "url", "") or "",
        })
    return items


def filter_items(items: list, keywords: list) -> list:
    """按关键词过滤（标题/简介大小写不敏感匹配）"""
    out = []
    for it in items:
        text = (it.get("title", "") + " " + it.get("desc", "")).lower()
        if any(k.lower() in text for k in keywords):
            out.append(it)
    return out


def group_by_theme(items: list) -> dict:
    """主题聚类：按 OTHER_KEYWORDS 归入生活/音乐/体育，剩余归 'AI/编程'/'未分类'"""
    groups = {"AI/编程": []}
    for name in OTHER_KEYWORDS:
        groups[name] = []
    groups["未分类"] = []
    for it in items:
        text = (it.get("title", "") + " " + it.get("desc", "")).lower()
        matched = False
        for name, kws in OTHER_KEYWORDS.items():
            if any(k.lower() in text for k in kws):
                groups[name].append(it)
                matched = True
                break
        if not matched:
            if any(k.lower() in text for k in AI_KEYWORDS):
                groups["AI/编程"].append(it)
            else:
                groups["未分类"].append(it)
    return groups


def sort_by_priority(items: list) -> list:
    """按热度降序（view 字段）"""
    return sorted(items, key=lambda i: i.get("view", 0), reverse=True)


def make_snapshot(items: list, out_dir: str = "", prefix: str = "scan") -> str:
    """保存快照 JSON → 返回路径（供后续增量对比）"""
    out_dir = out_dir or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{prefix}_snapshot_{datetime.now():%Y%m%d}.json")
    data = {"saved_at": datetime.now().isoformat(timespec="seconds"),
            "count": len(items), "items": items}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return path
