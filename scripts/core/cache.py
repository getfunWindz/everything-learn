"""核心缓存：按 (platform, item_id, sub_id) 保存字幕/转写结果到 scripts/cache/"""
import json
import os

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")

CACHE_VERSION = 2  # M13：复检机制变更 → 旧缓存不携带复检信息，必须失效


def _cache_path(platform: str, item_id: str, sub_id: str = "") -> str:
    name = f"{platform}_{item_id}"
    if sub_id:
        name += f"_{sub_id}"
    return os.path.join(CACHE_DIR, name + ".json")


def get_cached(platform: str, item_id: str, sub_id: str = ""):
    """命中当前 schema 版本的缓存 → dict；未命中/损坏/旧版 → None"""
    path = _cache_path(platform, item_id, sub_id)
    if not os.path.exists(path):
        return None
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("_cache_v") != CACHE_VERSION:
        return None
    return data


def set_cached(platform: str, item_id: str, sub_id: str, lines) -> None:
    """写入缓存（原子写：先写临时文件再改名，防中断损坏）；纯 list 自动包装为 timeline"""
    payload = lines if isinstance(lines, dict) else {"kind": "timeline", "items": lines}
    payload = dict(payload)
    payload["_cache_v"] = CACHE_VERSION
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path(platform, item_id, sub_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)


def clear_cache(platform: str = "", item_id: str = "", sub_id: str = "") -> int:
    """清空全部或指定缓存；返回删除文件数"""
    if platform:
        p = _cache_path(platform, item_id, sub_id)
        if os.path.exists(p):
            os.remove(p)
            return 1
        return 0
    n = 0
    if os.path.isdir(CACHE_DIR):
        for f in os.listdir(CACHE_DIR):
            if f.endswith(".json"):
                os.remove(os.path.join(CACHE_DIR, f))
                n += 1
    return n
